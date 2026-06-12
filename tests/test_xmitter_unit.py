"""
Unit tests for MomongaSessionManager.xmitter().

Run:
  python -m unittest tests/test_xmitter_unit.py -v
"""
import threading
import unittest
from unittest.mock import MagicMock, patch, call

from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_exception import (
    MomongaSkCommandExecutionFailure,
    MomongaNeedToReopen,
)
from momonga.momonga_session_manager import MomongaSessionManager


def _make_sm():
    sm = object.__new__(MomongaSessionManager)
    sm.xmit_allowed = threading.Event()
    sm.xmit_allowed.set()
    sm.session_established = True
    sm.receiver_exception = None
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.device_strategy = BP35C2Strategy()
    return sm


class TestXmitterSuccess(unittest.TestCase):

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_sends_when_gate_open(self, _sleep):
        sm = _make_sm()
        sm.xmitter(b'\x00\x01')
        sm.skw.sksendto.assert_called_once_with('FE80::1', b'\x00\x01')

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_no_retry_on_first_success(self, _sleep):
        sm = _make_sm()
        sm.xmitter(b'\x00')
        self.assertEqual(sm.skw.sksendto.call_count, 1)


class TestXmitterGateTimeout(unittest.TestCase):

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_gate_never_opens_raises(self, _sleep):
        sm = _make_sm()
        sm.xmit_allowed.clear()
        with patch.object(sm.xmit_allowed, 'wait', return_value=False):
            with self.assertRaises(MomongaNeedToReopen):
                sm.xmitter(b'\x00')

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_receiver_exception_during_wait_raises(self, _sleep):
        sm = _make_sm()
        sm.xmit_allowed.clear()
        sm.receiver_exception = RuntimeError('receiver died')
        with patch.object(sm.xmit_allowed, 'wait', return_value=False):
            with self.assertRaises(MomongaNeedToReopen):
                sm.xmitter(b'\x00')

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_gate_opens_after_wait_succeeds(self, _sleep):
        sm = _make_sm()
        responses = [False] * 5 + [True]
        with patch.object(sm.xmit_allowed, 'wait', side_effect=responses):
            sm.xmitter(b'\x00')
        sm.skw.sksendto.assert_called_once()


class TestXmitterSendFailure(unittest.TestCase):

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_sk_failure_retries_up_to_limit(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = MomongaSkCommandExecutionFailure('fail')
        with self.assertRaises(MomongaNeedToReopen):
            sm.xmitter(b'\x00')
        self.assertEqual(sm.skw.sksendto.call_count, 3)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_sk_failure_then_success_does_not_raise(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = [
            MomongaSkCommandExecutionFailure('fail'),
            None,
        ]
        sm.xmitter(b'\x00')
        self.assertEqual(sm.skw.sksendto.call_count, 2)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_need_to_reopen_propagates_immediately(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = MomongaNeedToReopen('session gone')
        with self.assertRaises(MomongaNeedToReopen):
            sm.xmitter(b'\x00')
        self.assertEqual(sm.skw.sksendto.call_count, 1)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_generic_exception_retries(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = [OSError('io error'), None]
        sm.xmitter(b'\x00')
        self.assertEqual(sm.skw.sksendto.call_count, 2)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_session_not_established_raises(self, _sleep):
        sm = _make_sm()
        sm.session_established = False
        with self.assertRaises(MomongaNeedToReopen):
            sm.xmitter(b'\x00')
        sm.skw.sksendto.assert_not_called()

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_sleep_called_between_retries(self, mock_sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = [
            MomongaSkCommandExecutionFailure('fail'),
            MomongaSkCommandExecutionFailure('fail'),
            None,
        ]
        sm.xmitter(b'\x00')
        self.assertEqual(mock_sleep.call_count, 2)


if __name__ == '__main__':
    unittest.main()
