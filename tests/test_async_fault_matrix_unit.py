"""
The async wrapper's own failure mode, checked the way the sync matrix checks
the link's.

What goes wrong here is not an unbounded wait. It is a call nobody is waiting
for any more holding a worker that a different kind of call needs. Three of
the defects found in this branch were exactly that, and each passed every
test that existed at the time.

So the wrapper splits its work into lanes - reads, session lifecycle, and
everything else - and promises they do not block one another. That promise is
what this checks: abandon a call in one lane, then require the other lanes to
answer. Requests may queue behind abandoned requests, which the README says
and _request_lock would enforce anyway; the point is that a read or a close
must not.

Run:
  python -m unittest tests/test_async_fault_matrix_unit.py -v
"""
import asyncio
import itertools
import threading
import unittest
from unittest.mock import MagicMock, patch

import serial

from momonga.momonga_async import AsyncMomonga
from momonga.momonga_exception import MomongaError
from momonga.momonga_response import SkParsedRxUdp
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedAsyncTestCase

_HEAD = b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01'
GOOD_INF = _HEAD + b'\x73\x01' + b'\xe7\x04\x00\x00\x03\xe8'

SKTERM_LIMIT = 'momonga.momonga_session_manager._SKTERM_LIMIT'
RECEIVER_LIMIT = 'momonga.momonga_session_manager._RECEIVER_JOIN_LIMIT'
PUBLISHER_LIMIT = 'momonga.momonga_sk_wrapper._PUBLISHER_JOIN_LIMIT'
BOUND = 6

LANES = ('request', 'read', 'lifecycle')
# a lane may be held up by its own abandoned work; it may not be held up by
# another lane's
BLOCKS = {'request': {'request'}, 'read': set(), 'lifecycle': set()}


def _frame(data):
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=0x0E1A,
                         dst_port=0x0E1A, src_mac=b'', side=0, sec=2, data=data)


class _Rig:
    """An AsyncMomonga whose three lanes can be blocked one at a time."""

    def __init__(self, test):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.closed = False
        self.timers = []

        def answer(line, payload=None):
            reply = 'EVENT 27 FE80::1 0' if line.startswith('SKTERM') else 'OK'
            t = threading.Timer(0.01, lambda: skw.subscribers['cmd_exec_q'].put(reply))
            t.start()
            self.timers.append(t)

        skw._writeline = answer
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        amo = AsyncMomonga('', '', '/dev/ttyUSB0')
        amo._sync.is_open = True
        amo._sync.session_manager = sm
        amo._sync.xmit_retries, amo._sync.recv_timeout = 2, 0.1
        amo._sync.internal_xmit_interval, amo._sync.xmit_timeout = 0, 0.4
        self.amo, self.sm, self.skw = amo, sm, skw
        self.hold = threading.Event()
        test.addCleanup(self.stop)

    def stop(self):
        self.hold.set()
        for t in self.timers:
            t.cancel()
        for e in (self.amo._executor, self.amo._notif_executor, self.amo._life_executor):
            e.shutdown(wait=False)

    def _block_first(self, owner, name, count):
        """Hang the next `count` calls of owner.name, then hand back to the real
        one - the fault belongs to the calls being abandoned, not to the object."""
        real = getattr(owner, name)
        remaining = [count]
        lock = threading.Lock()

        def blocker(*args, **kwargs):
            with lock:
                mine = remaining[0] > 0
                if mine:
                    remaining[0] -= 1
            if mine:
                return self.hold.wait(20)
            return real(*args, **kwargs)

        setattr(owner, name, blocker)

    async def abandon(self, lane):
        """Start work in `lane`, give up waiting for it, leave it running."""
        amo = self.amo
        if lane == 'request':
            n = amo._executor._max_workers
            self._block_first(amo._sync, 'get_instantaneous_power', n)
            tasks = [asyncio.ensure_future(amo.get_instantaneous_power())
                     for _ in range(n)]
        elif lane == 'read':
            self._block_first(amo._sync, '_read_notification', 1)
            tasks = [asyncio.ensure_future(amo.get_notification(timeout=30))]
        else:
            self._block_first(amo._sync, 'open', 1)
            tasks = [asyncio.ensure_future(amo.open())]
        await asyncio.sleep(0.25)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def call(self, lane):
        amo = self.amo
        if lane == 'request':
            return await asyncio.wait_for(amo.get_instantaneous_power(), BOUND)
        if lane == 'read':
            self.sm.notif_q.put(_frame(GOOD_INF))
            return await asyncio.wait_for(amo.get_notification(timeout=0.3), BOUND)
        return await asyncio.wait_for(amo.close(), BOUND)


class TestALaneIsNotBlockedByAnother(TimeBoxedAsyncTestCase):

    def setUp(self):
        for target in (SKTERM_LIMIT, RECEIVER_LIMIT, PUBLISHER_LIMIT):
            p = patch(target, 0.3)
            p.start()
            self.addCleanup(p.stop)

    async def test_the_matrix(self):
        for abandoned, asked in itertools.product(LANES, LANES):
            if asked in BLOCKS[abandoned]:
                continue                      # documented to queue, not a defect
            with self.subTest(abandoned=abandoned, asked=asked):
                rig = _Rig(self)
                await rig.abandon(abandoned)
                try:
                    await rig.call(asked)
                except asyncio.TimeoutError:
                    self.fail('%s did not answer while an abandoned %s ran'
                              % (asked, abandoned))
                except MomongaError:
                    pass
                rig.stop()

    async def test_leaving_always_completes(self):
        for abandoned in LANES:
            with self.subTest(abandoned=abandoned):
                rig = _Rig(self)
                rig.amo._sync.close = lambda: None
                await rig.abandon(abandoned)
                try:
                    await asyncio.wait_for(
                        rig.amo.__aexit__(None, None, None), BOUND)
                except asyncio.TimeoutError:
                    self.fail('leaving hung with an abandoned %s' % abandoned)
                rig.stop()


class TestTheWrapperDoesNotRewriteWhatItIsGiven(TimeBoxedAsyncTestCase):

    async def test_a_bare_exception_reaches_the_caller_unchanged(self):
        rig = _Rig(self)
        rig.amo._sync.get_instantaneous_power = lambda: (_ for _ in ()).throw(
            IndexError('something the wrapper never expected'))
        with self.assertRaises(IndexError):
            await rig.amo.get_instantaneous_power()

    async def test_a_momonga_timeout_is_not_mistaken_for_wait_for_expiring(self):
        # asyncio.TimeoutError is the builtin TimeoutError from 3.11 on, so a
        # library error inheriting it would be indistinguishable from the
        # caller's own deadline running out
        from momonga.momonga_exception import (MomongaXmitTimeout,
                                               MomongaSkCommandBusy)
        for exc in (MomongaXmitTimeout, MomongaSkCommandBusy):
            with self.subTest(exc=exc.__name__):
                self.assertNotIsInstance(exc('x'), asyncio.TimeoutError)


if __name__ == '__main__':
    unittest.main()
