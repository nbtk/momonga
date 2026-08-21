"""
Unit tests for the transmission budget of the automatic INFC_Res reply.

Run:
  python -m unittest tests/test_infc_res_budget_unit.py -v
"""
import logging
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga, _INFC_RES_XMIT_LIMIT
from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_exception import MomongaNeedToReopen, MomongaTimeoutError
from momonga.momonga_response import SkParsedRxUdp
from momonga.momonga_session_manager import MomongaSessionManager


def _infc_frame() -> SkParsedRxUdp:
    data = (b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01'  # EHD + TID + SEOJ + DEOJ
            + b'\x74'                                     # ESV = INFC
            + b'\x01'                                     # OPC = 1
            + b'\xe7\x04\x00\x00\x03\xe8')                # instantaneous_power = 1000
    return SkParsedRxUdp(src_addr='', dst_addr='', src_port=0, dst_port=0,
                         src_mac=b'', side=0, sec=0, data=data)


def _make_sm() -> MomongaSessionManager:
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.session_established = True
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.device_strategy = BP35C2Strategy()
    return sm


class TestGetNotificationHonoursTimeout(unittest.TestCase):

    def test_returns_within_timeout_while_gate_is_closed(self):
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        sm = _make_sm()
        sm._xmit_allowed.clear()
        mo.session_manager = sm
        sm.notif_q.put(_infc_frame())

        started = time.monotonic()
        result = mo.get_notification(timeout=0.3)
        elapsed = time.monotonic() - started

        self.assertIsNotNone(result)
        self.assertLess(elapsed, 5)

    def test_notification_is_still_delivered_when_infc_res_is_dropped(self):
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        sm = _make_sm()
        sm._xmit_allowed.clear()
        mo.session_manager = sm
        sm.notif_q.put(_infc_frame())

        result = mo.get_notification(timeout=0.3)

        self.assertEqual(result['properties'][0xE7], 1000)


class TestInfcResBudget(unittest.TestCase):

    def _capture_budget(self, timeout):
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.session_manager = MagicMock()
        mo._send_infc_res(_infc_frame().data, timeout)
        return mo.session_manager.xmitter.call_args.kwargs['timeout']

    def test_clamped_to_the_limit_when_the_caller_waits_longer(self):
        self.assertEqual(self._capture_budget(3600), _INFC_RES_XMIT_LIMIT)

    def test_clamped_to_the_limit_when_the_caller_did_not_set_a_timeout(self):
        self.assertEqual(self._capture_budget(None), _INFC_RES_XMIT_LIMIT)

    def test_uses_the_remaining_time_when_it_is_shorter(self):
        self.assertEqual(self._capture_budget(0.5), 0.5)


class TestXmitterBudget(unittest.TestCase):

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_gate_wait_is_capped_by_the_budget(self, _sleep):
        sm = _make_sm()
        sm._xmit_allowed.clear()
        with patch.object(sm._xmit_allowed, 'wait', return_value=False) as wait:
            # the mocked wait consumes no time, so the budget never actually runs out
            with self.assertRaises(MomongaNeedToReopen):
                sm.xmitter(b'\x00', timeout=5)
        self.assertLessEqual(wait.call_args_list[0].kwargs['timeout'], 5)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_gate_wait_gives_up_once_the_budget_is_spent(self, _sleep):
        sm = _make_sm()
        sm._xmit_allowed.clear()
        with patch.object(sm._xmit_allowed, 'wait', return_value=False) as wait:
            with self.assertRaises(MomongaTimeoutError):
                sm.xmitter(b'\x00', timeout=0)
        self.assertEqual(wait.call_count, 1)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_no_budget_keeps_the_full_gate_wait(self, _sleep):
        sm = _make_sm()
        sm._xmit_allowed.clear()
        with patch.object(sm._xmit_allowed, 'wait', return_value=False) as wait:
            with self.assertRaises(MomongaNeedToReopen):
                sm.xmitter(b'\x00')
        self.assertEqual(wait.call_args_list[0].kwargs['timeout'], 60)
        self.assertEqual(wait.call_count, 60)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_send_retries_stop_once_the_budget_is_spent(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = OSError('io error')
        with self.assertRaises(MomongaTimeoutError):
            sm.xmitter(b'\x00', timeout=0)
        self.assertEqual(sm.skw.sksendto.call_count, 1)


class _CaptureLogs(logging.Handler):

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        self._logger = logging.getLogger('momonga.momonga_session_manager')
        self._level = self._logger.level
        self._logger.addHandler(self)
        self._logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self)
        self._logger.setLevel(self._level)

    def at(self, level):
        return [r.getMessage() for r in self.records if r.levelname == level]


class TestASpentBudgetIsNotReportedAsALostSession(unittest.TestCase):

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_a_spent_budget_does_not_advise_reopening(self, _sleep):
        sm = _make_sm()
        sm._xmit_allowed.clear()
        with _CaptureLogs() as logs:
            with patch.object(sm._xmit_allowed, 'wait', return_value=False):
                with self.assertRaises(MomongaTimeoutError):
                    sm.xmitter(b'\x00', timeout=0)
        self.assertEqual(logs.at('ERROR'), [])

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_a_gate_that_never_opens_still_advises_reopening(self, _sleep):
        sm = _make_sm()
        sm._xmit_allowed.clear()
        with _CaptureLogs() as logs:
            with patch.object(sm._xmit_allowed, 'wait', return_value=False):
                with self.assertRaises(MomongaNeedToReopen):
                    sm.xmitter(b'\x00')
        self.assertEqual(len(logs.at('ERROR')), 1)

    def test_an_undeliverable_infc_res_stays_a_warning(self):
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        sm = _make_sm()
        sm._xmit_allowed.clear()
        mo.session_manager = sm
        sm.notif_q.put(_infc_frame())

        with _CaptureLogs() as logs:
            mo.get_notification(timeout=0.2)

        self.assertEqual(logs.at('ERROR'), [])


if __name__ == '__main__':
    unittest.main()
