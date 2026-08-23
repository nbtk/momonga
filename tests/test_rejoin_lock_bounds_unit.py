"""
Unit tests for the rejoin lock waits, on both sides of it.

close() takes the lock so a rejoin in flight finishes first, and the receiver
takes it on EVENT 24 so two rejoins cannot overlap. Either wait, left
unbounded, wedges the thread that is on it for good: mutation testing showed
the receiver's acquire() could drop its timeout with the whole suite green,
because nothing drove EVENT 24 into a receiver whose lock was already held.

Run:
  python -m unittest tests/test_rejoin_lock_bounds_unit.py -v
"""
import logging
import threading
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_session_manager import MomongaSessionManager, _STOP_RECEIVER
from tests._timebox import TimeBoxedTestCase

REJOIN_LIMIT = 'momonga.momonga_session_manager._REJOIN_LOCK_LIMIT'
REJOIN_FAILED = 'EVENT 24 FE80::1 0'


def _make_sm():
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0', reset_dev=False)
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.subscribers = {}
    sm.skw.device_strategy = BP35C2Strategy()
    return sm


def _start_receiver(sm):
    sm._receiver_th = threading.Thread(target=sm._receiver, daemon=True)
    sm._receiver_th.start()
    return sm._receiver_th


class TestTheReceiverGivesUpOnAHeldLock(TimeBoxedTestCase):

    def test_a_rejoin_it_cannot_start_does_not_stop_it_reading(self):
        sm = _make_sm()
        sm.session_established = False  # keeps this on the wait, not on the skjoin
        sm._rejoin_lock.acquire()       # a close(), or a rejoin already running
        try:
            th = _start_receiver(sm)
            with patch(REJOIN_LIMIT, 0.2):
                sm._pkt_sbsc_q.put(REJOIN_FAILED)
                sm._pkt_sbsc_q.put(_STOP_RECEIVER)
                th.join(5)
            # still on the lock means the sentinel queued behind it is never read
            self.assertFalse(th.is_alive())
        finally:
            sm._rejoin_lock.release()

    def test_giving_up_says_so(self):
        sm = _make_sm()
        sm.session_established = False
        records = []

        class Cap(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        lg = logging.getLogger('momonga.momonga_session_manager')
        cap = Cap()
        lg.addHandler(cap)
        sm._rejoin_lock.acquire()
        try:
            th = _start_receiver(sm)
            with patch(REJOIN_LIMIT, 0.2):
                sm._pkt_sbsc_q.put(REJOIN_FAILED)
                sm._pkt_sbsc_q.put(_STOP_RECEIVER)
                th.join(5)
        finally:
            sm._rejoin_lock.release()
            lg.removeHandler(cap)

        self.assertTrue(any('Gave up rejoining' in m for m in records), records)

    def test_a_free_lock_is_taken_and_handed_back(self):
        sm = _make_sm()
        sm.session_established = False
        th = _start_receiver(sm)

        sm._pkt_sbsc_q.put(REJOIN_FAILED)
        sm._pkt_sbsc_q.put(_STOP_RECEIVER)
        th.join(5)

        self.assertFalse(th.is_alive())
        self.assertFalse(sm._rejoin_lock.locked())  # released on the way out


class TestCloseGivesUpOnAHeldLock(TimeBoxedTestCase):

    def test_close_comes_back_while_a_rejoin_holds_the_lock(self):
        sm = _make_sm()
        sm.session_established = False
        sm._rejoin_lock.acquire()
        try:
            with patch(REJOIN_LIMIT, 0.2):
                done = threading.Thread(target=sm.close, daemon=True)
                done.start()
                done.join(5)
            self.assertFalse(done.is_alive())
        finally:
            sm._rejoin_lock.release()

    def test_a_lock_it_never_got_is_not_one_it_hands_back(self):
        sm = _make_sm()
        sm.session_established = False
        sm._rejoin_lock.acquire()  # held by someone else for the whole close
        try:
            with patch(REJOIN_LIMIT, 0.2):
                t = threading.Thread(target=sm.close, daemon=True)
                t.start()
                t.join(5)
                self.assertFalse(t.is_alive())
            self.assertTrue(sm._rejoin_lock.locked())  # still the other thread's
        finally:
            sm._rejoin_lock.release()


if __name__ == '__main__':
    unittest.main()
