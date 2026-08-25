"""Notifications held for reads that were cancelled after the meter answered.

A notification the meter has handed over is gone from the meter. If the read
that took it was cancelled, the only place left for it is the reader itself,
which holds it for whoever asks next - the manual says a notification already
read carries over to the next call, without qualifying it.

It was held in one slot. Two reads can be in flight at once, and each cancel
and start again adds another, so a second one arriving overwrote the first and
the meter never sent it again. Nothing was logged: a reading simply never
appeared. Running two readers and cancelling both, forty of eighty-four
notifications were lost that way, always one of each pair.

A queue holds them instead, bounded so a caller that never reads them does not
grow one without end, and saying so in the log when it drops one rather than
losing it in silence.

Run:
  python -m unittest tests/test_orphaned_notifications_unit.py -v
"""
import asyncio
import logging
import unittest

from unittest.mock import MagicMock

from momonga.momonga_async import AsyncMomonga, _ORPHAN_LIMIT
from momonga.momonga_session_manager import MomongaSessionManager

from tests._timebox import TimeBoxedAsyncTestCase


def _amo():
    amo = AsyncMomonga('', '', '/dev/ttyUSB0')
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.subscribers = {}
    amo._sync.session_manager = sm
    amo._sync.is_open = True
    return amo


def _reading(result):
    """A finished read of the meter, as the done callback receives it."""
    done = asyncio.get_running_loop().create_future()
    done.set_result(result)
    return done


class _OrphanTestCase(TimeBoxedAsyncTestCase):

    async def asyncSetUp(self):
        self.amo = _amo()
        self.addCleanup(lambda: [ex.shutdown(wait=False) for ex in
                                 (self.amo._executor, self.amo._notif_executor,
                                  self.amo._life_executor)])

    def _hand_over(self, *notifs):
        for notif in notifs:
            self.amo._keep_what_was_read(_reading(notif))


class TestASecondOneDoesNotDisplaceTheFirst(_OrphanTestCase):

    async def test_both_are_kept(self):
        self._hand_over({'n': 1}, {'n': 2})

        self.assertEqual(len(self.amo._orphaned), 2)

    async def test_they_come_back_in_the_order_the_meter_sent_them(self):
        self._hand_over({'n': 1}, {'n': 2}, {'n': 3})

        got = [await self.amo.get_notification(timeout=0) for _ in range(3)]

        self.assertEqual(got, [{'n': 1}, {'n': 2}, {'n': 3}])

    async def test_none_is_handed_out_twice(self):
        self._hand_over({'n': 1}, {'n': 2})

        first = await self.amo.get_notification(timeout=0)
        second = await self.amo.get_notification(timeout=0)

        self.assertNotEqual(first, second)
        self.assertEqual(len(self.amo._orphaned), 0)


class TestTheQueueIsBounded(_OrphanTestCase):

    async def test_a_caller_who_never_reads_does_not_grow_it_without_end(self):
        self._hand_over(*({'n': i} for i in range(_ORPHAN_LIMIT + 18)))

        self.assertEqual(len(self.amo._orphaned), _ORPHAN_LIMIT)

    async def test_what_is_dropped_is_said_out_loud(self):
        with self.assertLogs('momonga.momonga_async', logging.WARNING) as logged:
            self._hand_over(*({'n': i} for i in range(_ORPHAN_LIMIT + 3)))

        self.assertEqual(len(logged.records), 3)

    async def test_it_is_the_oldest_that_goes(self):
        self._hand_over(*({'n': i} for i in range(_ORPHAN_LIMIT + 1)))

        self.assertEqual(await self.amo.get_notification(timeout=0), {'n': 1})

    async def test_nothing_is_said_while_there_is_room(self):
        with self.assertNoLogs('momonga.momonga_async', logging.WARNING):
            self._hand_over(*({'n': i} for i in range(_ORPHAN_LIMIT)))


class TestNothingAboutTheOldRulesChanged(_OrphanTestCase):

    async def test_a_read_that_returned_nothing_is_not_queued(self):
        self._hand_over(None, None)

        self.assertEqual(len(self.amo._orphaned), 0)

    async def test_a_reading_from_a_session_that_is_gone_is_thrown_away(self):
        self.amo._orphaned.append(({'n': 1}, object()))
        self.amo._orphaned.append(({'n': 2}, self.amo._sync.session_manager))

        self.assertEqual(await self.amo.get_notification(timeout=0), {'n': 2})

    async def test_a_queue_of_nothing_but_stale_ones_empties(self):
        self.amo._orphaned.append(({'n': 1}, object()))
        self.amo._orphaned.append(({'n': 2}, object()))

        self.assertIsNone(await self.amo.get_notification(timeout=0))
        self.assertEqual(len(self.amo._orphaned), 0)


class TestTwoReadersCancelledTogetherLoseNothing(_OrphanTestCase):
    """The shape from the report, without the sleeps: each reader took a
    notification off the meter, and both were cancelled before either could
    hand it back."""

    async def test_neither_reading_disappears(self):
        for pair in (({'n': 1}, {'n': 2}), ({'n': 3}, {'n': 4})):
            self._hand_over(*pair)

        got = []
        while True:
            notif = await self.amo.get_notification(timeout=0)
            if notif is None:
                break
            got.append(notif['n'])

        self.assertEqual(got, [1, 2, 3, 4])


if __name__ == '__main__':
    unittest.main()
