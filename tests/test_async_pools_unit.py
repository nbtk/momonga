"""
Which thread pool each AsyncMomonga call runs on.

There are three: a general one for requests, one for reading notifications and
one for open/close/reopen. The split is the whole point - a request that has
wedged must not be able to keep the caller from closing, and the reserved pools
each keep a spare so a call nobody is awaiting any more cannot hold the next
one out.

test_limit_values checks the sizes. Nothing checked the wiring, and mutation
testing found every dedicated pool could be pointed back at the general one
with the suite green.

Run:
  python -m unittest tests/test_async_pools_unit.py -v
"""
import asyncio
import threading
import unittest
from unittest.mock import MagicMock

from momonga.momonga_async import AsyncMomonga, _RESERVED_WORKERS
from tests._timebox import TimeBoxedAsyncTestCase

GENERAL_WORKERS = 2
JAMMED = GENERAL_WORKERS * 2   # more callers than the general pool can run


class _JammedGeneralPool(TimeBoxedAsyncTestCase):
    """Every worker of the general pool is stuck inside a request."""

    async def asyncSetUp(self):
        self.amo = AsyncMomonga('', '', '/dev/ttyUSB0', max_workers=GENERAL_WORKERS)
        self.release = threading.Event()
        self.amo._sync = MagicMock()
        self.amo._sync.get_instantaneous_power.side_effect = \
            lambda: self.release.wait(30)

        self.jam = [asyncio.ensure_future(self.amo.get_instantaneous_power())
                    for _ in range(JAMMED)]
        await asyncio.sleep(0.3)  # let them take the workers

    async def asyncTearDown(self):
        self.release.set()
        for f in self.jam:
            f.cancel()
        await asyncio.gather(*self.jam, return_exceptions=True)
        for ex in (self.amo._executor, self.amo._notif_executor,
                   self.amo._life_executor):
            ex.shutdown(wait=False)


class TestClosingIsNotQueuedBehindRequests(_JammedGeneralPool):

    async def test_close_still_runs(self):
        await asyncio.wait_for(self.amo.close(), 5)

        self.amo._sync.close.assert_called_once()

    async def test_reopen_still_runs(self):
        await asyncio.wait_for(self.amo.reopen(), 5)

        self.amo._sync.reopen.assert_called_once()

    async def test_open_still_runs(self):
        await asyncio.wait_for(self.amo.open(), 5)

        self.amo._sync.open.assert_called_once()

    async def test_leaving_the_context_manager_still_runs(self):
        await asyncio.wait_for(self.amo.__aexit__(None, None, None), 5)

        self.amo._sync.close.assert_called_once()


class TestReadingNotificationsIsNotQueuedBehindRequests(_JammedGeneralPool):

    async def test_a_notification_read_still_runs(self):
        self.amo._sync._read_notification.return_value = {'esv': 0x73}

        got = await asyncio.wait_for(self.amo.get_notification(timeout=2), 5)

        self.assertEqual(got, {'esv': 0x73})


class TestADeadReadDoesNotHoldTheNextOne(TimeBoxedAsyncTestCase):

    async def test_a_read_nobody_awaits_leaves_a_worker_for_the_next(self):
        amo = AsyncMomonga('', '', '/dev/ttyUSB0')
        release = threading.Event()
        amo._sync = MagicMock()
        self.addCleanup(release.set)
        self.addCleanup(lambda: [ex.shutdown(wait=False) for ex in
                                 (amo._executor, amo._notif_executor,
                                  amo._life_executor)])

        # the first read wedges; it is shielded, so it keeps its worker
        amo._sync._read_notification.side_effect = lambda *a: release.wait(30)
        abandoned = asyncio.ensure_future(amo.get_notification(timeout=10))
        await asyncio.sleep(0.3)
        abandoned.cancel()
        await asyncio.gather(abandoned, return_exceptions=True)

        amo._sync._read_notification.side_effect = None
        amo._sync._read_notification.return_value = {'esv': 0x73}
        got = await asyncio.wait_for(amo.get_notification(timeout=2), 5)

        self.assertEqual(got, {'esv': 0x73})
        self.assertGreaterEqual(_RESERVED_WORKERS, 2)  # what makes that possible


if __name__ == '__main__':
    unittest.main()
