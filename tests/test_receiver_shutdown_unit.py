"""
Unit tests for close() when the packet receiver will not stop.

Run:
  python -m unittest tests/test_receiver_shutdown_unit.py -v
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_session_manager import MomongaSessionManager
from tests._timebox import TimeBoxedTestCase

JOIN_LIMIT = 'momonga.momonga_session_manager._RECEIVER_JOIN_LIMIT'


def _make_sm():
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0', reset_dev=False)
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.subscribers = {}
    sm.skw.device_strategy = BP35C2Strategy()
    sm.skw.skscan.return_value = MagicMock(mac_addr=b'\x00' * 8, channel='21', pan_id='1234')
    return sm


def _meter_frame() -> str:
    data = '1081000102880105FF017301E704000003E8'
    return ('ERXUDP FE80::1 FE80::2 0E1A 0E1A AABBCCDDEEFF 50 00 00 %04X %s'
            % (len(data) // 2, data))


def _start_receiver_stuck_in_the_callback(sm):
    """Leaves the receiver inside on_meter_frame and returns the event that frees it."""
    entered, release = threading.Event(), threading.Event()

    def never_returns(_frame):
        entered.set()
        release.wait(30)

    sm.on_meter_frame = never_returns
    sm._receiver_th = threading.Thread(target=sm._receiver, daemon=True)
    sm._receiver_th.start()
    sm._pkt_sbsc_q.put(_meter_frame())
    assert entered.wait(5)
    return release


class TestCloseGivesUpOnAStuckReceiver(TimeBoxedTestCase):

    def test_close_returns_while_the_callback_is_still_running(self):
        sm = _make_sm()
        release = _start_receiver_stuck_in_the_callback(sm)

        try:
            with patch(JOIN_LIMIT, 0.2):
                started = time.monotonic()
                sm.close()
                elapsed = time.monotonic() - started
        finally:
            release.set()

        self.assertLess(elapsed, 5)

    def test_it_says_so(self):
        sm = _make_sm()
        release = _start_receiver_stuck_in_the_callback(sm)

        try:
            with patch(JOIN_LIMIT, 0.2):
                with self.assertLogs('momonga.momonga_session_manager', 'WARNING') as caught:
                    sm.close()
        finally:
            release.set()

        self.assertIn('on_meter_frame', '\n'.join(caught.output))

    def test_a_receiver_that_does_stop_is_not_complained_about(self):
        sm = _make_sm()
        sm._receiver_th = threading.Thread(target=sm._receiver, daemon=True)
        sm._receiver_th.start()

        with self.assertLogs('momonga.momonga_session_manager', 'DEBUG') as caught:
            sm.close()

        self.assertNotIn('on_meter_frame', '\n'.join(caught.output))


class TestTheNextSessionIsNotSharedWithIt(TimeBoxedTestCase):

    def test_open_hands_the_new_receiver_a_queue_of_its_own(self):
        sm = _make_sm()
        release = _start_receiver_stuck_in_the_callback(sm)
        abandoned = sm._pkt_sbsc_q

        try:
            with patch(JOIN_LIMIT, 0.2):
                sm.close()
            sm.on_meter_frame = None
            sm.open()

            self.assertIsNot(sm._pkt_sbsc_q, abandoned)
            self.assertIs(sm.skw.subscribers['pkt_sbsc_q'], sm._pkt_sbsc_q)
        finally:
            release.set()

    def test_the_abandoned_receiver_still_has_its_way_out(self):
        sm = _make_sm()
        release = _start_receiver_stuck_in_the_callback(sm)
        abandoned_th, abandoned_q = sm._receiver_th, sm._pkt_sbsc_q

        with patch(JOIN_LIMIT, 0.2):
            sm.close()
        sm.on_meter_frame = None
        sm.open()  # would have drained the stop sentinel before

        release.set()
        abandoned_th.join(5)

        self.assertFalse(abandoned_th.is_alive())
        self.assertTrue(abandoned_q.empty())


if __name__ == '__main__':
    unittest.main()
