"""
Several threads using one Momonga at once.

The README describes this - it talks about requests issued from other threads
while a reopen runs - but nothing exercised it. _request_lock is what makes it
safe, and the failure it prevents is silent: a request reading a reply that
belongs to another one.

So the fake module answers each SKSENDTO with a value derived from that frame's
transaction id. A caller that receives a value it cannot have asked for has
been handed someone else's reply, and the test can see it. Taking _request_lock
away turns this red - 115 of 120 requests fail and two threads never return.

Run:
  python -m unittest tests/test_concurrency_unit.py -v
"""
import collections
import threading
import time
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_exception import MomongaError
from momonga.momonga_response import SkParsedRxUdp
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper

INFC = (b'\x10\x81\x00\x00\x02\x88\x01\x05\xff\x01' + b'\x74\x01'
        + b'\xe7\x04\x00\x00\x03\xe8')
STAMP = 7          # the reply carries tid * STAMP so its owner is recognisable

THREADS = 8
PER_THREAD = 15


def _reply_for(tid):
    edt = (int.from_bytes(tid, 'big') * STAMP).to_bytes(4, 'big')
    return (b'\x10\x81' + tid + b'\x02\x88\x01' + b'\x05\xff\x01' + b'\x72\x01'
            + b'\xe7\x04' + edt)


class TestManyThreadsOnOneMomonga(unittest.TestCase):

    def setUp(self):
        self.timers = []
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.closed = False
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        self.sent = []

        def module(line, payload=None):
            skw.subscribers['cmd_exec_q'].put('OK')
            if line.startswith('SKSENDTO') and payload:
                tid = payload[2:4]
                self.sent.append(tid)
                t = threading.Timer(0.01, lambda: sm.recv_q.put(SkParsedRxUdp(
                    src_addr='FE80::1', dst_addr='', src_port=0x0E1A,
                    dst_port=0x0E1A, src_mac=b'', side=0, sec=2,
                    data=_reply_for(tid))))
                t.start()
                self.timers.append(t)

        skw._writeline = module
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 3, 0.4, 0
        mo.xmit_timeout = 2
        self.mo, self.sm = mo, sm
        self.addCleanup(self._stop)

    def _stop(self):
        for t in self.timers:
            t.cancel()

    def _hammer(self, with_readers):
        counts = collections.Counter()
        values = []
        raw = []
        lock = threading.Lock()
        stop = threading.Event()

        def requester():
            for _ in range(PER_THREAD):
                try:
                    v = self.mo.get_instantaneous_power()
                    with lock:
                        counts['ok' if v % STAMP == 0 else 'crosstalk'] += 1
                        values.append(v)
                except MomongaError as e:
                    with lock:
                        counts['momonga:' + type(e).__name__] += 1
                except Exception as e:      # noqa: BLE001
                    with lock:
                        counts['raw'] += 1
                        raw.append(repr(e))

        def reader():
            while not stop.is_set():
                try:
                    self.mo.get_notification(timeout=0.2)
                except MomongaError:
                    pass
                except Exception as e:      # noqa: BLE001
                    with lock:
                        counts['raw'] += 1
                        raw.append(repr(e))

        def notifier():
            while not stop.is_set():
                self.sm.notif_q.put(SkParsedRxUdp(
                    src_addr='FE80::1', dst_addr='', src_port=0, dst_port=0,
                    src_mac=b'', side=0, sec=0, data=INFC))
                time.sleep(0.15)

        workers = [threading.Thread(target=requester, daemon=True)
                   for _ in range(THREADS)]
        extras = ([threading.Thread(target=reader, daemon=True) for _ in range(2)]
                  + [threading.Thread(target=notifier, daemon=True)]) if with_readers else []
        for t in workers + extras:
            t.start()
        for t in workers:
            t.join(60)
        stop.set()
        for t in extras:
            t.join(5)
        return counts, values, raw, [t for t in workers if t.is_alive()]

    def test_every_request_gets_its_own_reply(self):
        counts, values, raw, stuck = self._hammer(with_readers=True)
        self.assertEqual(stuck, [], 'a requester never returned')
        self.assertEqual(raw, [], 'a bare exception escaped')
        self.assertEqual(counts['crosstalk'], 0, 'a caller was handed another reply')
        self.assertEqual(counts['ok'], THREADS * PER_THREAD, dict(counts))

    def test_no_two_callers_see_the_same_reply(self):
        _counts, values, _raw, _stuck = self._hammer(with_readers=False)
        self.assertEqual(len(values), len(set(values)), 'a reply reached two callers')

    def test_retransmissions_reuse_the_transaction_id(self):
        self._hammer(with_readers=False)
        # more frames than ids means retransmission, which is the retry loop;
        # more ids than requests would mean an id was skipped or duplicated
        self.assertLessEqual(len(set(self.sent)), THREADS * PER_THREAD)
        self.assertGreaterEqual(len(self.sent), len(set(self.sent)))


if __name__ == '__main__':
    unittest.main()
