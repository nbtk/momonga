"""
Unit tests for waking a blocked get_notification() when its feeder goes away.

Run:
  python -m unittest tests/test_notification_wakeup_unit.py -v
"""
import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga
from momonga.momonga_exception import MomongaNeedToReopen, MomongaRuntimeError
from momonga.momonga_session_manager import MomongaSessionManager, SESSION_ENDED


def _make_sm():
    sm = object.__new__(MomongaSessionManager)
    sm.notif_q = queue.Queue()
    sm.recv_q = queue.Queue()
    sm.pkt_sbsc_q = queue.Queue()
    sm.receiver_exception = None
    sm.receiver_th = None
    sm.session_established = False
    sm.rejoin_lock = threading.Lock()
    sm.gate_lock = threading.Lock()
    sm.session_available = True
    sm.rate_ok = True
    sm.xmit_allowed = threading.Event()
    sm.xmit_allowed.set()
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.subscribers = {}
    return sm


def _make_mo(sm):
    mo = Momonga(rbid='', pwd='', dev='')
    mo.is_open = True
    mo.session_manager = sm
    return mo


class TestCloseWakesTheReader(unittest.TestCase):

    def test_close_releases_a_blocked_reader(self):
        sm = _make_sm()
        mo = _make_mo(sm)
        result = []

        reader = threading.Thread(target=lambda: result.append(mo.get_notification(timeout=None)),
                                  daemon=True)
        reader.start()
        time.sleep(0.05)
        self.assertTrue(reader.is_alive())  # blocked, as intended

        sm.close()
        reader.join(5)

        self.assertFalse(reader.is_alive())
        self.assertEqual(result, [None])

    def test_a_closed_momonga_is_reported_on_the_next_call(self):
        sm = _make_sm()
        mo = _make_mo(sm)
        sm.notif_q.put(SESSION_ENDED)

        self.assertIsNone(mo.get_notification(timeout=1))

        mo.is_open = False
        with self.assertRaises(MomongaRuntimeError):
            mo.get_notification(timeout=1)


class TestReopenWakesTheReader(unittest.TestCase):

    def test_reader_moves_to_the_queue_of_the_new_session(self):
        old = _make_sm()
        mo = _make_mo(old)
        new = _make_sm()
        result = []

        def read_twice():
            result.append(mo.get_notification(timeout=None))   # blocked on the old queue
            result.append(mo.get_notification(timeout=5))      # must read the new queue

        reader = threading.Thread(target=read_twice, daemon=True)
        reader.start()
        time.sleep(0.05)

        with patch.object(mo, 'close', side_effect=lambda: old.close()), \
             patch.object(mo, 'open'):
            with patch('momonga.momonga.MomongaSessionManager', return_value=new):
                mo._rbid = mo._pwd = mo._dev = ''
                mo._baudrate = 115200
                mo._reset_dev = True
                mo.reopen()

        frame = MagicMock()
        frame.data = b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01\x73\x01\xe7\x04\x00\x00\x00\x64'
        new.notif_q.put(frame)
        reader.join(5)

        self.assertIsNone(result[0])          # woken by the old session closing
        self.assertIsNotNone(result[1])       # read from the new session


class TestReceiverDeathIsReported(unittest.TestCase):

    def test_blocked_reader_is_woken_when_the_receiver_dies(self):
        sm = _make_sm()
        mo = _make_mo(sm)
        result = []

        reader = threading.Thread(target=lambda: result.append(mo.get_notification(timeout=None)),
                                  daemon=True)
        reader.start()
        time.sleep(0.05)

        sm.pkt_sbsc_q.put(object())  # not a str; parse_sk_line will fail on it
        sm.receiver()

        reader.join(5)
        self.assertFalse(reader.is_alive())
        self.assertIsNotNone(sm.receiver_exception)

    def test_next_call_reports_the_dead_receiver(self):
        sm = _make_sm()
        mo = _make_mo(sm)
        sm.receiver_exception = RuntimeError('receiver died')

        with self.assertRaises(MomongaNeedToReopen):
            mo.get_notification(timeout=1)

    def test_xmitter_reports_a_dead_receiver_before_sending(self):
        sm = _make_sm()
        sm.session_established = True
        sm.receiver_exception = RuntimeError('receiver died')

        with self.assertRaises(MomongaNeedToReopen):
            sm.xmitter(b'\x00')

        sm.skw.sksendto.assert_not_called()


if __name__ == '__main__':
    unittest.main()
