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
from momonga.momonga_exception import (MomongaNeedToReopen, MomongaXmitTimeout,
                                       MomongaSkCommandCancelled, MomongaSkCommandBusy)
from momonga.momonga_response import SkParsedRxUdp
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper, _SK_COMMAND_LIMIT
from tests._timebox import TimeBoxedTestCase


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


class TestGetNotificationHonoursTimeout(TimeBoxedTestCase):

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


def _make_mo_with_a_real_wrapper():
    """A session manager whose skw is the real wrapper, so the send is not a mock."""
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.session_established = True
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = skw
    mo = Momonga('', '', '/dev/ttyUSB0')
    mo.is_open = True
    mo.session_manager = sm
    sm.notif_q.put(_infc_frame())
    return mo, sm, skw


class TestTheWholeSendIsBounded(TimeBoxedTestCase):

    def _elapsed(self, mo, timeout=0.3):
        started = time.monotonic()
        mo.get_notification(timeout=timeout)
        return time.monotonic() - started

    def test_a_module_that_never_answers_does_not_hold_the_reader(self):
        mo, sm, skw = _make_mo_with_a_real_wrapper()
        self.assertLess(self._elapsed(mo), 5)  # not the sk command limit

    def test_another_command_holding_the_lock_does_not_hold_the_reader(self):
        mo, sm, skw = _make_mo_with_a_real_wrapper()
        skw._cmd_lock.acquire()
        try:
            self.assertLess(self._elapsed(mo), 5)  # not an unbounded lock wait
        finally:
            skw._cmd_lock.release()

    def test_the_packet_still_reaches_the_module_when_the_ack_is_not_awaited(self):
        mo, sm, skw = _make_mo_with_a_real_wrapper()
        with patch.object(skw, '_writeline') as writeline:
            self._elapsed(mo)
        writeline.assert_called_once()

    def test_a_send_without_a_budget_keeps_the_wrapper_defaults(self):
        sm = _make_sm()
        sm.skw = MagicMock()
        with patch.object(MomongaSkWrapper, 'exec_command') as exec_command:
            MomongaSkWrapper.sksendto(MomongaSkWrapper('/dev/ttyUSB0'), 'FE80::1', b'\x00')
        self.assertEqual(exec_command.call_args.kwargs['timeout'], _SK_COMMAND_LIMIT)
        self.assertEqual(exec_command.call_args.kwargs['lock_timeout'], -1)


class TestInfcResBudget(TimeBoxedTestCase):

    def _capture_budget(self, timeout):
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.session_manager = MagicMock()
        mo._send_infc_res(_infc_frame().data, timeout)
        return mo.session_manager.xmitter.call_args.kwargs['timeout']

    def test_clamped_to_the_limit_when_the_caller_waits_longer(self):
        self.assertEqual(self._capture_budget(300), _INFC_RES_XMIT_LIMIT)

    def test_clamped_to_the_limit_when_the_caller_did_not_set_a_timeout(self):
        self.assertEqual(self._capture_budget(None), _INFC_RES_XMIT_LIMIT)

    def test_uses_the_remaining_time_when_it_is_shorter(self):
        self.assertEqual(self._capture_budget(0.5), 0.5)


class TestXmitterBudget(TimeBoxedTestCase):

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
            with self.assertRaises(MomongaXmitTimeout):
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
        with self.assertRaises(MomongaXmitTimeout):
            sm.xmitter(b'\x00', timeout=0)
        self.assertEqual(sm.skw.sksendto.call_count, 1)


class TestASpentBudgetStaysCatchable(TimeBoxedTestCase):

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_the_handler_every_caller_already_has_still_catches_it(self, _sleep):
        sm = _make_sm()
        sm._xmit_allowed.clear()
        with patch.object(sm._xmit_allowed, 'wait', return_value=False):
            with self.assertRaises(MomongaNeedToReopen):
                sm.xmitter(b'\x00', timeout=0)

    def test_it_is_still_tellable_apart_from_a_lost_session(self):
        self.assertTrue(issubclass(MomongaXmitTimeout, MomongaNeedToReopen))
        self.assertFalse(issubclass(MomongaNeedToReopen, MomongaXmitTimeout))


class TestASpentBudgetIsNamedWhereverItRunsOut(TimeBoxedTestCase):

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_a_budget_spent_on_the_send_is_still_a_timeout(self, _sleep):
        sm = _make_sm()  # the gate is open, so the budget runs out in sksendto
        sm.skw.sksendto.side_effect = MomongaNeedToReopen('The module did not respond.')
        with self.assertRaises(MomongaXmitTimeout):
            sm.xmitter(b'\x00', timeout=0)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_a_lost_session_is_not_relabelled_as_a_timeout(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = MomongaNeedToReopen('session gone')
        with self.assertRaises(MomongaNeedToReopen) as caught:
            sm.xmitter(b'\x00', timeout=60)
        self.assertNotIsInstance(caught.exception, MomongaXmitTimeout)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_being_locked_out_with_no_budget_left_is_a_timeout(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = MomongaSkCommandBusy('Another SK command is still running')
        with self.assertRaises(MomongaXmitTimeout):
            sm.xmitter(b'\x00', timeout=0)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_being_locked_out_with_budget_left_keeps_its_own_type(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = MomongaSkCommandBusy('Another SK command is still running')
        with self.assertRaises(MomongaSkCommandBusy):
            sm.xmitter(b'\x00', timeout=60)

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_a_cancelled_command_stays_a_cancellation(self, _sleep):
        sm = _make_sm()
        sm.skw.sksendto.side_effect = MomongaSkCommandCancelled('close() cancelled it')
        with self.assertRaises(MomongaSkCommandCancelled):
            sm.xmitter(b'\x00', timeout=0)


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


class TestASpentBudgetIsNotReportedAsALostSession(TimeBoxedTestCase):

    @patch('momonga.momonga_session_manager.time.sleep')
    def test_a_spent_budget_does_not_advise_reopening(self, _sleep):
        sm = _make_sm()
        sm._xmit_allowed.clear()
        with _CaptureLogs() as logs:
            with patch.object(sm._xmit_allowed, 'wait', return_value=False):
                with self.assertRaises(MomongaXmitTimeout):
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
