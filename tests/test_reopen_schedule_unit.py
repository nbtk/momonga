"""
What reopen_delays promises: every outage starts at the head of the schedule.

That is how a backoff is supposed to work - a connection that drops twice an
hour apart should ramp from the bottom both times, not carry on from where the
last one stopped. momonga keeps that promise for a list, and for a one-shot
iterator by replaying what it has already yielded.

It could not keep it across instances. An iterator handed to a second Momonga
carries on from wherever the first left it, so a schedule that ramps
60/120/300 and then settles at 600 would ramp once and never again - silently,
since nothing about the constructor call changes. A callable says "build me a
fresh one" and holds everywhere.

Run:
  python -m unittest tests/test_reopen_schedule_unit.py -v
"""
import unittest
from itertools import chain, repeat
from unittest.mock import MagicMock, patch

import momonga
from momonga.momonga import Momonga, _ReplayableIterator
from momonga.momonga_echonet_enum import EchonetServiceCode
from tests._timebox import TimeBoxedTestCase

RAMP = [60.0, 120.0, 300.0]


def _backoff():
    return chain(RAMP, repeat(600.0))


class _FakeSessionManager:
    def __init__(self, *args, **kwargs):
        self.skw = MagicMock()
        self.on_meter_frame = None

    def open(self):
        return self

    def close(self):
        pass


class _Base(TimeBoxedTestCase):

    def _delays_used(self, reopen_delays, outages=1, failures=3):
        """The waits the recovery loop takes, per outage."""
        per_outage, calls = [], {'n': 0}

        def failing_request(_self, _esv, _props):
            calls['n'] += 1
            if calls['n'] <= failures:
                raise momonga.MomongaNeedToReopen('session lost')
            return []

        with patch('momonga.momonga.MomongaSessionManager', _FakeSessionManager), \
             patch.object(Momonga, '_init_energy_unit', lambda _self: None), \
             patch.object(Momonga, '_request', failing_request), \
             patch('momonga.momonga.time.sleep',
                   lambda s: current.append(s) if s >= 10 else None):
            mo = momonga.Momonga('', '', '/dev/ttyUSB0', reopen_delays=reopen_delays)
            mo.open()
            for _ in range(outages):
                current, calls['n'] = [], 0
                mo._request_with_recovery(EchonetServiceCode.get, [])
                per_outage.append(current)
        return per_outage


class TestEveryOutageStartsAtTheHead(_Base):

    def test_a_list_ramps_again_each_time(self):
        used = self._delays_used(list(RAMP), outages=3)

        self.assertEqual(used, [RAMP] * 3)

    def test_a_one_shot_iterator_is_not_spent_by_the_first_outage(self):
        used = self._delays_used(iter(RAMP), outages=3)

        self.assertEqual(used, [RAMP] * 3)

    def test_an_endless_schedule_still_ramps_from_the_bottom(self):
        used = self._delays_used(_backoff(), outages=3)

        self.assertEqual(used, [RAMP] * 3)

    def test_a_callable_is_asked_for_a_fresh_one(self):
        used = self._delays_used(_backoff, outages=3)

        self.assertEqual(used, [RAMP] * 3)


class TestTheScheduleSurvivesASecondInstance(_Base):
    """A reconnect loop builds a new Momonga each round, and the schedule it
    is given has to behave the same on the second round as on the first."""

    def _first_delays_of_three_instances(self, reopen_delays):
        return [self._delays_used(reopen_delays)[0] for _ in range(3)]

    def test_a_shared_iterator_ramps_only_once(self):
        # what a module level BACKOFF = chain(...) does, and why the callable
        # form exists. Kept as a test so the difference stays visible
        shared = _backoff()

        self.assertEqual(self._first_delays_of_three_instances(shared),
                         [RAMP, [600.0] * 3, [600.0] * 3])

    def test_a_callable_ramps_every_time(self):
        self.assertEqual(self._first_delays_of_three_instances(_backoff),
                         [RAMP] * 3)

    def test_a_list_is_safe_to_share(self):
        self.assertEqual(self._first_delays_of_three_instances(list(RAMP)),
                         [RAMP] * 3)


class TestTheCallableIsNotMistakenForASchedule(_Base):

    def test_it_is_not_wrapped_for_replay(self):
        mo = momonga.Momonga('', '', '/dev/ttyUSB0', reopen_delays=_backoff)

        self.assertIs(mo.reopen_delays, _backoff)

    def test_an_iterator_still_is(self):
        mo = momonga.Momonga('', '', '/dev/ttyUSB0', reopen_delays=iter(RAMP))

        self.assertIsInstance(mo.reopen_delays, _ReplayableIterator)

    def test_a_list_is_left_alone(self):
        delays = list(RAMP)
        mo = momonga.Momonga('', '', '/dev/ttyUSB0', reopen_delays=delays)

        self.assertIs(mo.reopen_delays, delays)

    def test_the_async_wrapper_takes_one_too(self):
        from momonga.momonga_async import AsyncMomonga
        amo = AsyncMomonga('', '', '/dev/ttyUSB0', reopen_delays=_backoff)
        self.addCleanup(lambda: [ex.shutdown(wait=False) for ex in
                                 (amo._executor, amo._notif_executor, amo._life_executor)])

        self.assertIs(amo._sync.reopen_delays, _backoff)


class TestABoundedScheduleStillGivesUp(_Base):

    def test_it_raises_once_the_ramp_is_spent(self):
        with self.assertRaises(momonga.MomongaNeedToReopen):
            self._delays_used(list(RAMP), failures=99)

    def test_a_callable_returning_a_bounded_one_gives_up_the_same_way(self):
        with self.assertRaises(momonga.MomongaNeedToReopen):
            self._delays_used(lambda: iter(RAMP), failures=99)


if __name__ == '__main__':
    unittest.main()
