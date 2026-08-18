"""
Unit tests for what a cancelled AsyncMomonga await leaves behind.

Run:
  python -m unittest tests/test_async_cancellation_unit.py -v
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_async import AsyncMomonga
from momonga.momonga_exception import MomongaRuntimeError
from momonga.momonga_session_manager import MomongaSessionManager

POLL = 'momonga.momonga_async._NOTIFICATION_POLL'


def _make_amo():
    amo = AsyncMomonga('', '', '/dev/ttyUSB0')
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.subscribers = {}
    amo._sync.session_manager = sm
    amo._sync.is_open = True
    return amo, sm


def _frame():
    f = MagicMock()
    f.data = b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01\x73\x01\xe7\x04\x00\x00\x03\xe8'
    return f


class _Traced:
    """Counts entries into and exits from the blocking sync call."""

    def __init__(self, amo):
        self.entered = 0
        self.left = 0
        self._real = amo._sync.get_notification
        amo._sync.get_notification = self

    def __call__(self, timeout=None):
        self.entered += 1
        try:
            return self._real(timeout=timeout)
        finally:
            self.left += 1

    @property
    def in_flight(self):
        return self.entered - self.left


class TestACancelledReadReleasesItsWorker(unittest.IsolatedAsyncioTestCase):

    async def test_the_worker_is_not_held_past_one_poll(self):
        amo, _sm = _make_amo()
        traced = _Traced(amo)

        with patch(POLL, 0.2):
            t = asyncio.create_task(amo.get_notification(timeout=None))
            await asyncio.sleep(0.1)
            self.assertEqual(traced.in_flight, 1)  # blocked in the sync layer

            t.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await t

            await asyncio.sleep(0.5)  # more than one poll
            self.assertEqual(traced.in_flight, 0)

    async def test_a_read_that_is_never_cancelled_still_blocks(self):
        amo, sm = _make_amo()

        with patch(POLL, 0.1):
            t = asyncio.create_task(amo.get_notification(timeout=None))
            await asyncio.sleep(0.5)  # several polls
            self.assertFalse(t.done())

            sm.notif_q.put(_frame())
            got = await asyncio.wait_for(t, timeout=5)

        self.assertIsNotNone(got)


class TestANotificationTakenByACancelledReadIsKept(unittest.IsolatedAsyncioTestCase):

    @staticmethod
    def _slow_read(amo, notif):
        def read(timeout=None):
            time.sleep(0.2)  # long enough for the caller to give up first
            return notif
        amo._sync.get_notification = read

    async def test_the_next_call_gets_what_the_cancelled_one_took(self):
        amo, _sm = _make_amo()
        taken = {'esv': 'INF', 'properties': {}}
        self._slow_read(amo, taken)

        t = asyncio.create_task(amo.get_notification(timeout=None))
        await asyncio.sleep(0.05)
        t.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await t

        await asyncio.sleep(0.4)  # the read the caller abandoned finishes
        self.assertIs(await amo.get_notification(timeout=0), taken)

    async def test_it_is_handed_over_only_once(self):
        amo, _sm = _make_amo()
        taken = {'esv': 'INF', 'properties': {}}
        self._slow_read(amo, taken)

        t = asyncio.create_task(amo.get_notification(timeout=None))
        await asyncio.sleep(0.05)
        t.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await t
        await asyncio.sleep(0.4)

        self.assertIs(await amo.get_notification(timeout=0), taken)
        amo._sync.get_notification = lambda timeout=None: None
        self.assertIsNone(await amo.get_notification(timeout=0))

    async def test_a_cancelled_read_that_took_nothing_keeps_nothing(self):
        amo, _sm = _make_amo()

        with patch(POLL, 0.2):
            t = asyncio.create_task(amo.get_notification(timeout=None))
            await asyncio.sleep(0.05)
            t.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await t
            await asyncio.sleep(0.5)

        self.assertIsNone(amo._orphaned)


class TestWhatIsHeldBelongsToOneSession(unittest.IsolatedAsyncioTestCase):

    async def test_a_closed_momonga_is_refused_rather_than_handed_it(self):
        amo, _sm = _make_amo()
        amo._orphaned = {'esv': 'INF', 'properties': {}}
        amo._orphaned_session = amo._sync.session_manager
        amo._sync.is_open = False

        with self.assertRaises(MomongaRuntimeError):
            await amo.get_notification(timeout=0)

    async def test_a_rebuilt_session_does_not_inherit_it(self):
        amo, _sm = _make_amo()
        amo._orphaned = {'esv': 'INF', 'properties': {}}
        amo._orphaned_session = object()  # the session it was read from is gone

        self.assertIsNone(await amo.get_notification(timeout=0))
        self.assertIsNone(amo._orphaned)


class TestTheTimeoutContractIsUnchanged(unittest.IsolatedAsyncioTestCase):

    async def test_zero_makes_exactly_one_attempt(self):
        amo, _sm = _make_amo()
        traced = _Traced(amo)

        self.assertIsNone(await amo.get_notification(timeout=0))

        self.assertEqual(traced.entered, 1)  # the drain idiom reads one item per call

    async def test_zero_still_drains_a_queued_notification(self):
        amo, sm = _make_amo()
        sm.notif_q.put(_frame())

        self.assertIsNotNone(await amo.get_notification(timeout=0))
        self.assertIsNone(await amo.get_notification(timeout=0))

    async def test_a_finite_timeout_gives_up_at_the_deadline(self):
        amo, _sm = _make_amo()

        with patch(POLL, 0.1):
            started = time.monotonic()
            self.assertIsNone(await amo.get_notification(timeout=0.5))
            elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.5)
        self.assertLess(elapsed, 3)

    async def test_a_notification_arriving_mid_poll_is_returned(self):
        amo, sm = _make_amo()

        with patch(POLL, 0.1):
            loop = asyncio.get_running_loop()
            loop.call_later(0.25, sm.notif_q.put, _frame())
            got = await amo.get_notification(timeout=5)

        self.assertIsNotNone(got)


if __name__ == '__main__':
    unittest.main()
