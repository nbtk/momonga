"""
Unit tests for open() cleaning up after itself and never reading forever.

Run:
  python -m unittest tests/test_open_bounds_unit.py -v
"""
import logging
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_exception import MomongaError, MomongaTimeoutError
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper

WRAPPER = 'momonga.momonga_sk_wrapper'


def _make_skw():
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    skw._ser.closed = False
    return skw


class TestAFailedOpenLeavesNothingRunning(unittest.TestCase):

    def _open_with_failing(self, method):
        skw = _make_skw()
        with patch.object(MomongaSkWrapper, '_clear_buf'), \
                patch.object(MomongaSkWrapper, '_exec_ropt', return_value=1), \
                patch.object(MomongaSkWrapper, 'received_packet_publisher'), \
                patch.object(MomongaSkWrapper, method,
                             side_effect=MomongaError('the module went quiet')), \
                patch(WRAPPER + '.serial.Serial', return_value=skw._ser):
            with self.assertRaises(MomongaError):
                skw.open()
        return skw

    def test_a_failed_detect_device_leaves_no_publisher_behind(self):
        skw = self._open_with_failing('detect_device')
        self.assertIsNone(skw._publisher_th)

    def test_a_failed_detect_device_closes_the_port(self):
        skw = self._open_with_failing('detect_device')
        skw._ser.close.assert_called_once()

    def test_a_failure_before_the_publisher_starts_still_closes_the_port(self):
        skw = _make_skw()
        with patch.object(MomongaSkWrapper, '_clear_buf',
                          side_effect=MomongaError('garbage forever')), \
                patch(WRAPPER + '.serial.Serial', return_value=skw._ser):
            with self.assertRaises(MomongaError):
                skw.open()
        skw._ser.close.assert_called_once()


class TestTheOpenReadsAreBounded(unittest.TestCase):

    def test_a_chattering_device_does_not_hold_clear_buf(self):
        skw = _make_skw()
        skw._ser.read.return_value = b'x'  # never stops talking

        started = time.monotonic()
        with patch(WRAPPER + '._BUF_CLEAR_LIMIT', 0.2):
            skw._clear_buf()
        self.assertLess(time.monotonic() - started, 5)

    def test_the_serial_timeout_is_restored_after_giving_up(self):
        skw = _make_skw()
        skw._ser.read.return_value = b'x'
        skw._ser.timeout = 300

        with patch(WRAPPER + '._BUF_CLEAR_LIMIT', 0.2):
            skw._clear_buf()
        self.assertEqual(skw._ser.timeout, 300)

    def test_a_dribbling_device_does_not_hold_ropt(self):
        skw = _make_skw()
        skw._ser.read.side_effect = lambda: (time.sleep(0.02), b'x')[1]

        with patch(WRAPPER + '._SK_COMMAND_LIMIT', 0.2):
            with self.assertRaises(MomongaTimeoutError):
                skw._exec_ropt()

    def test_a_dribbling_device_does_not_hold_wopt(self):
        skw = _make_skw()
        skw._ser.read.side_effect = lambda: (time.sleep(0.02), b'x')[1]

        with patch(WRAPPER + '._SK_COMMAND_LIMIT', 0.2):
            with self.assertRaises(MomongaTimeoutError):
                skw._exec_wopt(1)

    def test_an_answering_device_still_gets_through(self):
        skw = _make_skw()
        skw._ser.read.side_effect = [bytes([b]) for b in b'OK 01\r']
        self.assertEqual(skw._exec_ropt(), 1)


class TestTheReceiverDoesNotWaitOutAClose(unittest.TestCase):

    def test_it_gives_up_rejoining_while_the_lock_is_held(self):
        skw = _make_skw()
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.skw = skw
        sm.smart_meter_addr = 'FE80::1'
        sm.session_established = True
        sm._rejoin_lock.acquire()  # what close() does
        try:
            with patch.object(skw, 'skjoin') as skjoin, \
                    patch('momonga.momonga_session_manager._REJOIN_LOCK_LIMIT', 0.2):
                th = threading.Thread(target=sm._receiver, daemon=True)
                th.start()
                sm._pkt_sbsc_q.put('EVENT 24 FE80::1 0')
                time.sleep(0.6)
                still_receiving = th.is_alive()
                sm._pkt_sbsc_q.put('EVENT 25 FE80::1 0')
                time.sleep(0.2)
        finally:
            sm._rejoin_lock.release()

        self.assertTrue(still_receiving)  # not parked on the lock for good
        self.assertTrue(sm.session_established)  # the later event was still handled
        skjoin.assert_not_called()


if __name__ == '__main__':
    unittest.main()
