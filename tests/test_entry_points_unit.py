"""
The ways in and the one-time device setup.

`with Momonga(...) as mo` is how the README opens every example, and coverage
found the __enter__/__exit__ of all three classes unexecuted. The same for the
ROPT/WOPT exchange that open() performs once per module - it decides whether
the module returns UDP payloads as ASCII, and getting it wrong makes every
later frame unreadable.

Run:
  python -m unittest tests/test_entry_points_unit.py -v
"""
import datetime
import logging
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga
from momonga.momonga_exception import (MomongaError, MomongaSkCommandUnsupported,
                                       MomongaSkJoinFailure, MomongaSkScanFailure,
                                       MomongaTimeoutError)
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

WRAPPER = 'momonga.momonga_sk_wrapper.MomongaSkWrapper'


class TestTheContextManagersOpenAndClose(TimeBoxedTestCase):

    def test_momonga_opens_on_entry_and_closes_on_exit(self):
        mo = Momonga('', '', '/dev/ttyUSB0')
        with patch.object(Momonga, 'open', return_value=mo) as opened, \
             patch.object(Momonga, 'close') as closed:
            with mo as entered:
                self.assertIs(entered, mo)
                closed.assert_not_called()

        opened.assert_called_once_with()
        closed.assert_called_once_with()

    def test_momonga_closes_even_when_the_body_raises(self):
        mo = Momonga('', '', '/dev/ttyUSB0')
        with patch.object(Momonga, 'open', return_value=mo), \
             patch.object(Momonga, 'close') as closed:
            with self.assertRaises(ValueError):
                with mo:
                    raise ValueError('from the body')

        closed.assert_called_once_with()

    def test_the_session_manager_opens_and_closes(self):
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        with patch.object(MomongaSessionManager, 'open', return_value=sm) as opened, \
             patch.object(MomongaSessionManager, 'close') as closed:
            with sm as entered:
                self.assertIs(entered, sm)

        opened.assert_called_once_with()
        closed.assert_called_once_with()

    def test_the_wrapper_opens_and_closes(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        with patch.object(MomongaSkWrapper, 'open', return_value=skw) as opened, \
             patch.object(MomongaSkWrapper, 'close') as closed:
            with skw as entered:
                self.assertIs(entered, skw)

        opened.assert_called_once_with()
        closed.assert_called_once_with()

    def test_the_wrapper_reports_the_device_it_detected(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)

        self.assertEqual(skw.device_type, skw.device_strategy.device_type)


class TestTheAsyncSettingsReadBack(TimeBoxedTestCase):

    def test_every_tunable_reads_what_was_written(self):
        amo = AsyncMomonga('', '', '/dev/ttyUSB0')
        self.addCleanup(lambda: [ex.shutdown(wait=False) for ex in
                                 (amo._executor, amo._notif_executor, amo._life_executor)])
        for name, value in (('xmit_retries', 7), ('recv_timeout', 9),
                            ('xmit_timeout', 11), ('internal_xmit_interval', 13)):
            with self.subTest(setting=name):
                setattr(amo, name, value)

                self.assertEqual(getattr(amo, name), value)
                self.assertEqual(getattr(amo._sync, name), value)


class TestTheOneTimeAsciiSetup(TimeBoxedTestCase):
    """open() reads ROPT and writes WOPT only if the module is not already in
    ASCII mode - WOPT can be run a limited number of times, so doing it every
    open would eventually brick the setting."""

    def _open(self, skw):
        with patch.object(MomongaSkWrapper, '_clear_buf'), \
             patch.object(MomongaSkWrapper, 'detect_device'), \
             patch.object(MomongaSkWrapper, 'received_packet_publisher'), \
             patch('momonga.momonga_sk_wrapper.serial.Serial'):
            skw.open()
            skw.close()

    def test_a_module_already_in_ascii_mode_is_left_alone(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        with patch.object(MomongaSkWrapper, '_exec_ropt', return_value=1), \
             patch.object(MomongaSkWrapper, '_exec_wopt') as wopt:
            self._open(skw)

        wopt.assert_not_called()

    def test_a_module_in_binary_mode_is_switched_once(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        with patch.object(MomongaSkWrapper, '_exec_ropt', return_value=0), \
             patch.object(MomongaSkWrapper, '_exec_wopt') as wopt:
            self._open(skw)

        wopt.assert_called_once_with(1)

    def test_hardware_without_ropt_is_assumed_to_be_ascii(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        with patch.object(MomongaSkWrapper, '_exec_ropt',
                          side_effect=MomongaSkCommandUnsupported('no ROPT')), \
             patch.object(MomongaSkWrapper, '_exec_wopt') as wopt:
            self._open(skw)   # must not raise

        wopt.assert_not_called()

    def test_a_ropt_the_module_refuses_is_a_momonga_error(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.read.side_effect = [bytes([c]) for c in b'FAIL ER04\r']

        with self.assertRaises(MomongaSkCommandUnsupported):
            skw._exec_ropt()

    def test_a_ropt_answer_that_is_neither_is_still_a_momonga_error(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.read.side_effect = [bytes([c]) for c in b'FAIL something\r']

        with self.assertRaises(MomongaError):
            skw._exec_ropt()

    def test_wopt_refuses_an_option_the_module_has_no_use_for(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()

        with self.assertRaises(MomongaError):
            skw._exec_wopt(99)

    def test_wopt_stops_when_the_module_stops_answering(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.read.return_value = b''

        with self.assertRaises(MomongaTimeoutError):
            skw._exec_wopt(1)

    def test_wopt_returns_once_the_module_says_ok(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.read.side_effect = [bytes([c]) for c in b'OK\r']

        skw._exec_wopt(1)  # must not raise


class TestOpeningTellsTheUserWhatToCheck(TimeBoxedTestCase):
    """A scan or join that never succeeds is the most common first-run
    failure, and the message is the only thing the user has to go on."""

    @staticmethod
    def _sm():
        sm = MomongaSessionManager('rbid', 'pwd', '/dev/ttyUSB0', reset_dev=False)
        sm.skw = MagicMock()
        sm.skw.subscribers = {}
        return sm

    def test_a_pan_that_cannot_be_found_says_where_to_look(self):
        sm = self._sm()
        sm.skw.skscan.side_effect = MomongaSkScanFailure('no PAN')

        with self.assertRaises(MomongaSkScanFailure) as caught:
            sm.open()

        self.assertIn('Route-B ID', str(caught.exception))

    def test_a_session_that_cannot_be_established_says_the_same(self):
        sm = self._sm()
        sm.skw.skscan.return_value = MagicMock(mac_addr=b'\x00' * 8, channel=0x21,
                                               pan_id=b'\xdd\x5b')
        sm.skw.skll64.return_value = MagicMock(ip6_addr='FE80::1')
        sm.skw.skjoin.side_effect = MomongaSkJoinFailure('refused')

        with self.assertRaises(MomongaSkJoinFailure) as caught:
            sm.open()

        self.assertIn('password', str(caught.exception))


class TestTheHistoricalDefaultsAreNow(TimeBoxedTestCase):

    def test_series_two_and_three_default_to_the_current_time(self):
        for getter, setter in (('get_historical_cumulative_energy_2',
                                'set_time_for_historical_data_2'),
                               ('get_historical_cumulative_energy_3',
                                'set_time_for_historical_data_3')):
            with self.subTest(getter=getter):
                mo = Momonga('', '', '/dev/ttyUSB0')
                asked = []
                with patch.object(Momonga, setter,
                                  lambda self, ts, n, _a=asked: _a.append(ts)), \
                     patch.object(Momonga, '_request_to_get',
                                  return_value=[MagicMock(edt=b'')]), \
                     patch('momonga.momonga.EchonetDataParser'):
                    getattr(mo, getter)()

                self.assertEqual(len(asked), 1)
                self.assertIsInstance(asked[0], datetime.datetime)
                self.assertLess(abs((datetime.datetime.now() - asked[0]).total_seconds()), 5)


if __name__ == '__main__':
    unittest.main()
