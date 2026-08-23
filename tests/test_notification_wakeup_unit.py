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
from tests._timebox import TimeBoxedTestCase


def _make_sm():
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.subscribers = {}
    return sm


def _make_mo(sm):
    mo = Momonga(rbid='', pwd='', dev='')
    mo.is_open = True
    mo.session_manager = sm
    return mo


class TestCloseWakesTheReader(TimeBoxedTestCase):

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


NOTIF_FRAME = b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01\x73\x01\xe7\x04\x00\x00\x00\x64'


class TestReopenWakesTheReader(TimeBoxedTestCase):

    def test_reader_moves_to_the_queue_of_the_new_session(self):
        old = _make_sm()
        mo = _make_mo(old)
        new = _make_sm()
        result = []

        def read_twice():
            result.append(mo.get_notification(timeout=None))   # blocked on the old queue
            try:
                result.append(mo.get_notification(timeout=5))  # spans the reopen window
            except Exception as e:
                result.append(e)

        reader = threading.Thread(target=read_twice, daemon=True)
        reader.start()
        time.sleep(0.05)

        def slow_open():
            time.sleep(0.3)  # stands in for skscan and skjoin
            mo.is_open = True

        with patch.object(mo, 'open', slow_open), \
             patch('momonga.momonga.MomongaSessionManager', return_value=new):
            mo.reopen()

        frame = MagicMock()
        frame.data = NOTIF_FRAME
        new.notif_q.put(frame)
        reader.join(5)

        self.assertIsNone(result[0])                    # woken by the old session closing
        self.assertNotIsInstance(result[1], Exception)  # not refused mid-reopen
        self.assertIsNotNone(result[1])                 # read from the new session

    def test_a_read_that_runs_out_of_time_mid_reopen_returns_none(self):
        mo = _make_mo(_make_sm())
        mo.is_open = False
        mo._reopen_done.clear()  # a reopen is in flight and will not finish in time

        try:
            self.assertIsNone(mo.get_notification(timeout=0.1))
        finally:
            mo._reopen_done.set()

    def test_a_failed_reopen_is_reported_at_once(self):
        mo = _make_mo(_make_sm())

        with patch.object(mo, 'open', side_effect=OSError('no device')), \
             patch('momonga.momonga.MomongaSessionManager', return_value=_make_sm()):
            with self.assertRaises(OSError):
                mo.reopen()

        started = time.monotonic()
        with self.assertRaises(MomongaRuntimeError):
            mo.get_notification(timeout=5)
        self.assertLess(time.monotonic() - started, 1)  # not waited out


class TestReceiverDeathIsReported(TimeBoxedTestCase):

    def test_blocked_reader_is_woken_when_the_receiver_dies(self):
        sm = _make_sm()
        mo = _make_mo(sm)
        result = []

        reader = threading.Thread(target=lambda: result.append(mo.get_notification(timeout=None)),
                                  daemon=True)
        reader.start()
        time.sleep(0.05)

        sm._pkt_sbsc_q.put(object())  # not a str; parse_sk_line will fail on it
        sm._receiver()

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
