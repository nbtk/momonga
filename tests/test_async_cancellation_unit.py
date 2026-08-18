"""
Unit tests for what a cancelled AsyncMomonga await leaves behind.

Run:
  python -m unittest tests/test_async_cancellation_unit.py -v
"""
import asyncio
import threading
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


class TestTheSharedPoolIsLeftAlone(unittest.IsolatedAsyncioTestCase):

    async def test_work_does_not_run_on_the_default_executor(self):
        amo, _sm = _make_amo()
        names = []
        amo._sync.get_notification = lambda timeout=None: names.append(
            threading.current_thread().name)

        await amo.get_notification(timeout=0)

        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith('momonga'), names[0])

    async def test_two_instances_do_not_share_a_pool(self):
        first, _ = _make_amo()
        second, _ = _make_amo()

        self.assertIsNot(first._executor, second._executor)

    async def test_leaving_the_context_manager_shuts_the_pool_down(self):
        amo, _sm = _make_amo()
        amo._sync.open = lambda: amo._sync
        amo._sync.close = lambda: None

        async with amo:
            pass

        with self.assertRaises(MomongaRuntimeError):  # no new work after shutdown
            await amo.get_notification(timeout=0)

    async def test_the_pool_is_shut_down_even_if_close_raises(self):
        amo, _sm = _make_amo()

        def failing_close():
            raise OSError('port gone')

        amo._sync.close = failing_close
        amo._sync.open = lambda: amo._sync

        with self.assertRaises(OSError):
            async with amo:
                pass

        self.assertTrue(amo._executor._shutdown)


    async def test_a_reader_still_running_at_exit_is_told_momonga_closed(self):
        amo, sm = _make_amo()
        amo._sync.open = lambda: amo._sync
        amo._sync.close = lambda: None
        seen = []

        async def consume():
            try:
                async for notif in amo.notifications(timeout=5):
                    seen.append(notif)
            except Exception as e:
                seen.append(type(e).__name__)

        with patch(POLL, 0.1):
            async with amo:
                reader = asyncio.create_task(consume())
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.4)

        reader.cancel()
        self.assertEqual(seen, ['MomongaRuntimeError'])


if __name__ == '__main__':
    unittest.main()
