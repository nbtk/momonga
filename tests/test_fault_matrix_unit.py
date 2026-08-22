"""
Every public call against every way the link can misbehave, checked for three
things it must do whatever happens:

  1. finish inside a bound
  2. let nothing but a MomongaError out
  3. leave no thread behind

The point is to catch the next one of these without knowing about it first.
Every defect of this shape found so far - an unbounded wait, a bare IndexError
from a short property, a publisher that outlived its session - is a cell here.

Run:
  python -m unittest tests/test_fault_matrix_unit.py -v
"""
import itertools
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import serial

from momonga.momonga import Momonga
from momonga.momonga_echonet_enum import EchonetPropertyCode as EPC
from momonga.momonga_exception import MomongaError
from momonga.momonga_response import SkParsedRxUdp
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper

_HEAD = b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01'
GOOD_INF = _HEAD + b'\x73\x01' + b'\xe7\x04\x00\x00\x03\xe8'
SHORT_INF = _HEAD + b'\x73\x01' + b'\xea\x01\x07'      # EDT shorter than its type
INFC = _HEAD + b'\x74\x01' + b'\xe7\x04\x00\x00\x03\xe8'
GET_SNA = _HEAD + b'\x52\x01' + b'\xe7\x00'

SKTERM_LIMIT = 'momonga.momonga_session_manager._SKTERM_LIMIT'
REJOIN_LIMIT = 'momonga.momonga_session_manager._REJOIN_LOCK_LIMIT'
RECEIVER_LIMIT = 'momonga.momonga_session_manager._RECEIVER_JOIN_LIMIT'
PUBLISHER_LIMIT = 'momonga.momonga_sk_wrapper._PUBLISHER_JOIN_LIMIT'

FAULTS = ('healthy', 'module_silent', 'module_slow', 'gate_closed', 'cmd_lock_held',
          'publisher_dead', 'receiver_dead', 'serial_raises', 'meter_sna', 'no_session')


def _frame(data):
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=0x0E1A,
                         dst_port=0x0E1A, src_mac=b'', side=0, sec=2, data=data)


CALLS = {
    'get_instantaneous_power': lambda mo, sm: mo.get_instantaneous_power(),
    'request_to_get': lambda mo, sm: mo.request_to_get({EPC.instantaneous_power}),
    'request_to_set': lambda mo, sm: mo.request_to_set(day_for_historical_data_1={'day': 0}),
    'get_historical_1': lambda mo, sm: mo.get_historical_cumulative_energy_1(),
    'notification': lambda mo, sm: (sm.notif_q.put(_frame(GOOD_INF)),
                                    mo.get_notification(timeout=0.5))[1],
    'notification_short_edt': lambda mo, sm: (sm.notif_q.put(_frame(SHORT_INF)),
                                              mo.get_notification(timeout=0.5))[1],
    'notification_infc': lambda mo, sm: (sm.notif_q.put(_frame(INFC)),
                                         mo.get_notification(timeout=0.5))[1],
    'notification_empty': lambda mo, sm: mo.get_notification(timeout=0.2),
    'close': lambda mo, sm: mo.close(),
}

BOUND = 8  # every limit below is well under this


class TestNothingEscapesItsBound(unittest.TestCase):

    def _build(self, fault):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.closed = False
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 2, 0.1, 0
        mo.xmit_timeout = 0.5
        undo = []

        def answer(line, delay=0.01):
            reply = 'EVENT 27 FE80::1 0' if line.startswith('SKTERM') else 'OK'
            timer = threading.Timer(delay, lambda: skw.subscribers['cmd_exec_q'].put(reply))
            timer.start()
            undo.append(timer.cancel)

        skw._writeline = lambda line, payload=None: answer(line)

        if fault == 'module_silent':
            skw._writeline = lambda line, payload=None: None
        elif fault == 'module_slow':
            skw._writeline = lambda line, payload=None: answer(line, delay=1.5)
        elif fault == 'gate_closed':
            sm._xmit_allowed.clear()
        elif fault == 'cmd_lock_held':
            skw._cmd_lock.acquire()
            undo.append(skw._cmd_lock.release)
        elif fault == 'publisher_dead':
            skw.publisher_exception = serial.SerialException('gone')
        elif fault == 'receiver_dead':
            sm.receiver_exception = RuntimeError('receiver died')
        elif fault == 'serial_raises':
            def boom(line, payload=None):
                raise serial.SerialException('write failed')
            skw._writeline = boom
        elif fault == 'meter_sna':
            def sna(line, payload=None):
                answer(line)
                timer = threading.Timer(0.02, lambda: sm.recv_q.put(_frame(GET_SNA)))
                timer.start()
                undo.append(timer.cancel)
            skw._writeline = sna
        elif fault == 'no_session':
            sm.session_established = False
        return mo, sm, undo

    def _run_cell(self, call, fault):
        before = {t.ident for t in threading.enumerate()}
        mo, sm, undo = self._build(fault)
        box = {}
        done = threading.Event()

        def run():
            try:
                box['value'] = CALLS[call](mo, sm)
            except BaseException as e:      # noqa: BLE001 - the point is to see everything
                box['exc'] = e
            done.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        finished = done.wait(BOUND)
        for u in undo:
            u()
        self.assertTrue(finished, 'did not finish inside %ds' % BOUND)

        exc = box.get('exc')
        if exc is not None:
            self.assertIsInstance(exc, MomongaError,
                                  'let a %s out: %s' % (type(exc).__name__, exc))

        worker.join(2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            extra = [t for t in threading.enumerate()
                     if t.ident not in before and t.is_alive()
                     and not isinstance(t, threading.Timer)]
            if not extra:
                return
            time.sleep(0.05)
        self.assertEqual([t.name for t in extra], [], 'left a thread behind')

    def test_the_matrix(self):
        limits = (patch(SKTERM_LIMIT, 0.4), patch(REJOIN_LIMIT, 0.4),
                  patch(RECEIVER_LIMIT, 0.4), patch(PUBLISHER_LIMIT, 0.4))
        for p in limits:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in limits])
        for call, fault in itertools.product(CALLS, FAULTS):
            with self.subTest(call=call, fault=fault):
                self._run_cell(call, fault)


if __name__ == '__main__':
    unittest.main()
