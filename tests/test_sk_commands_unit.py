"""
The commands the wrapper actually writes to the module.

Each of these is one exec_command call, and coverage found none of them
executed outside the integration tests: the whole open() sequence - SKRESET,
SKSREG, SKSETRBID, SKSETPWD, SKSCAN, SKLL64, SKJOIN - is unreached in CI. A
wrong register name or a length written in the wrong base does not fail
anywhere near itself; it fails as a module that will not join.

exec_command is stubbed, so what is under test is the argument list built for
it and what each wrapper does with the reply - the scan widening its duration,
the join reading EVENT 25 from the last line, the terminate noticing 28.

Run:
  python -m unittest tests/test_sk_commands_unit.py -v
"""
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_device_strategy import BP35A1Strategy, BP35C2Strategy
from momonga.momonga_exception import MomongaSkJoinFailure, MomongaSkScanFailure
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

SCAN_HIT = ['EPANDESC', '  Channel:21', '  Channel Page:09', '  Pan ID:8888',
            '  Addr:AABBCCDDEEFF0011', '  LQI:6E', '  Side:0', '  PairID:12345678']
SCAN_MISS = ['EVENT 22 FE80::1 0']


def _make_skw():
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    return skw


class _WithAStubbedCommand(TimeBoxedTestCase):

    def setUp(self):
        self.skw = _make_skw()
        self.replies = [['OK']]
        self.sent = []

        outer = self

        def exec_command(_self, command, expect=None, *args, **kwargs):
            outer.sent.append(command)
            return outer.replies[min(len(outer.sent) - 1, len(outer.replies) - 1)]

        patcher = patch.object(MomongaSkWrapper, 'exec_command', exec_command)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestTheCommandsAreWrittenAsTheModuleExpects(_WithAStubbedCommand):

    def test_skreset(self):
        self.skw.skreset()

        self.assertEqual(self.sent, [['SKRESET']])

    def test_skver_and_skappver_ask_for_their_own_line(self):
        self.replies = [['EVER 1.2.10'], ['EAPPVER rl7023']]
        self.skw.skver()
        self.skw.skappver()

        self.assertEqual(self.sent, [['SKVER'], ['SKAPPVER']])

    def test_skinfo(self):
        self.replies = [['EINFO FE80::1 AABBCCDDEEFF0011 21 8888 0']]
        self.skw.skinfo()

        self.assertEqual(self.sent, [['SKINFO']])

    def test_sksreg_takes_a_string_as_it_is(self):
        self.skw.sksreg('S02', '21')

        self.assertEqual(self.sent, [['SKSREG', 'S02', '21']])

    def test_sksreg_writes_an_int_as_upper_case_hex(self):
        self.skw.sksreg('S02', 0x21)

        self.assertEqual(self.sent, [['SKSREG', 'S02', '21']])

    def test_sksreg_writes_bytes_as_upper_case_hex(self):
        self.skw.sksreg('S03', b'\xdd\x5b')

        self.assertEqual(self.sent, [['SKSREG', 'S03', 'DD5B']])

    def test_sksetrbid(self):
        self.skw.sksetrbid('00112233445566778899AABBCCDDEEFF')

        self.assertEqual(self.sent, [['SKSETRBID', '00112233445566778899AABBCCDDEEFF']])

    def test_sksetpwd_sends_the_length_in_hex_before_the_password(self):
        self.skw.sksetpwd('0123456789AB')  # 12 characters -> C

        self.assertEqual(self.sent, [['SKSETPWD', 'C', '0123456789AB']])

    def test_skll64_sends_the_mac_as_upper_case_hex(self):
        self.replies = [['FE80:0000:0000:0000:1234:5678:9ABC:DEF0']]
        self.skw.skll64(bytes.fromhex('aabbccddeeff0011'))

        self.assertEqual(self.sent, [['SKLL64', 'AABBCCDDEEFF0011']])


class TestScanningWidensItsWindowBeforeGivingUp(_WithAStubbedCommand):

    def test_a_scan_that_finds_the_pan_stops_there(self):
        self.replies = [SCAN_HIT]
        self.skw.device_strategy = BP35C2Strategy()

        res = self.skw.skscan()

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(res.channel, 0x21)

    def test_each_retry_scans_for_longer(self):
        self.replies = [SCAN_MISS, SCAN_MISS, SCAN_HIT]
        self.skw.device_strategy = BP35C2Strategy()

        self.skw.skscan()

        durations = [c[3] for c in self.sent]  # SKSCAN <mode> <mask> <duration> [side]
        self.assertEqual(durations, ['6', '7', '8'])

    def test_running_out_of_retries_is_a_scan_failure(self):
        self.replies = [SCAN_MISS]
        self.skw.device_strategy = BP35C2Strategy()

        with self.assertRaises(MomongaSkScanFailure):
            self.skw.skscan(retry=2)

        self.assertEqual(len(self.sent), 2)

    def test_a_bp35a1_scan_leaves_the_side_off(self):
        self.replies = [SCAN_HIT]
        self.skw.device_strategy = BP35A1Strategy()

        self.skw.skscan()

        self.assertEqual(len(self.sent[0]), 4)  # SKSCAN mode mask duration


class TestJoiningReadsTheLastEventNotTheFirst(_WithAStubbedCommand):

    def test_a_join_that_succeeds_returns(self):
        self.replies = [['OK', 'EVENT 25 FE80::1 0']]

        self.skw.skjoin('FE80::1')

        self.assertEqual(self.sent, [['SKJOIN', 'FE80::1']])

    def test_a_refused_join_is_retried(self):
        self.replies = [['EVENT 24 FE80::1 0'], ['EVENT 24 FE80::1 0'],
                        ['EVENT 25 FE80::1 0']]

        self.skw.skjoin('FE80::1')

        self.assertEqual(len(self.sent), 3)

    def test_running_out_of_retries_is_a_join_failure(self):
        self.replies = [['EVENT 24 FE80::1 0']]

        with self.assertRaises(MomongaSkJoinFailure):
            self.skw.skjoin('FE80::1', retry=2)

        self.assertEqual(len(self.sent), 2)


class TestTerminatingNoticesThereWasNoSession(_WithAStubbedCommand):

    def _levels(self):
        import logging
        records = []

        class Cap(logging.Handler):
            def emit(self, record):
                records.append(record.levelname)

        lg = logging.getLogger('momonga.momonga_sk_wrapper')
        cap = Cap()
        lg.addHandler(cap)
        try:
            self.skw.skterm()
        finally:
            lg.removeHandler(cap)
        return records

    def test_event_27_is_an_ordinary_terminate(self):
        self.replies = [['OK', 'EVENT 27 FE80::1 0']]

        self.assertNotIn('WARNING', self._levels())
        self.assertEqual(self.sent, [['SKTERM']])

    def test_event_28_says_there_was_nothing_to_terminate(self):
        self.replies = [['OK', 'EVENT 28 FE80::1 0']]

        self.assertIn('WARNING', self._levels())


if __name__ == '__main__':
    unittest.main()
