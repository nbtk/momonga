"""
The Wi-SUN module's own replies: the FAIL codes and the SK* responses.

Coverage put this whole layer at 70% - it is reached only by the integration
tests, which skip without a dongle. The FAIL mapping turned out to raise a
bare ValueError for anything that was not exactly "FAIL ERnn", so a garbled
line came back as something outside the library's error hierarchy, past the
recovery loop, to the caller.

Run:
  python -m unittest tests/test_sk_responses_unit.py -v
"""
import unittest
from unittest.mock import MagicMock

from momonga.momonga_device import BP35A1Strategy, BP35C2Strategy
from momonga.momonga_exception import (MomongaError, MomongaKeyError,
                                       MomongaSkCommandFailedToExecute,
                                       MomongaSkCommandInvalidArgument,
                                       MomongaSkCommandInvalidSyntax,
                                       MomongaSkCommandSerialInputError,
                                       MomongaSkCommandUnknownError,
                                       MomongaSkCommandUnsupported)
from momonga.momonga_response import (SkAppVerResponse, SkInfoResponse, SkLl64Response,
                                      SkScanResponse, SkVerResponse)
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

# what the module documents, and what each one has to become
FAIL_CODES = {
    'FAIL ER01': MomongaSkCommandUnknownError,
    'FAIL ER02': MomongaSkCommandUnknownError,
    'FAIL ER03': MomongaSkCommandUnknownError,
    'FAIL ER04': MomongaSkCommandUnsupported,
    'FAIL ER05': MomongaSkCommandInvalidArgument,
    'FAIL ER06': MomongaSkCommandInvalidSyntax,
    'FAIL ER07': MomongaSkCommandUnknownError,
    'FAIL ER08': MomongaSkCommandUnknownError,
    'FAIL ER09': MomongaSkCommandSerialInputError,
    'FAIL ER10': MomongaSkCommandFailedToExecute,
}
GARBLED = ('FAIL', 'FAIL ', 'FAIL ER', 'FAIL ERxx', 'FAIL ER-1', 'FAIL nonsense')


def _make_skw():
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    return skw


class TestEveryFailCodeHasItsOwnError(TimeBoxedTestCase):

    def test_the_documented_codes_map_where_they_should(self):
        skw = _make_skw()
        for line, expected in FAIL_CODES.items():
            with self.subTest(line=line):
                with self.assertRaises(expected):
                    skw._raise_fail_response('SKVER', line)

    def test_a_code_outside_the_range_is_still_an_error(self):
        skw = _make_skw()
        for line in ('FAIL ER00', 'FAIL ER11', 'FAIL ER99'):
            with self.subTest(line=line):
                with self.assertRaises(MomongaSkCommandUnknownError):
                    skw._raise_fail_response('SKVER', line)

    def test_a_garbled_failure_stays_inside_the_hierarchy(self):
        # int(r[7:10]) on anything else is a ValueError, which _request_with_recovery
        # does not catch and the caller is not told to expect
        skw = _make_skw()
        for line in GARBLED:
            with self.subTest(line=line):
                with self.assertRaises(MomongaError):
                    skw._raise_fail_response('SKVER', line)

    def test_the_failing_command_is_named_in_the_message(self):
        skw = _make_skw()
        with self.assertRaises(MomongaError) as caught:
            skw._raise_fail_response('SKSCAN 2 FFFFFFFF 6 0', 'FAIL ER05')

        self.assertIn('SKSCAN', str(caught.exception))


class TestTheModuleResponsesDecode(TimeBoxedTestCase):

    def test_skver(self):
        self.assertEqual(SkVerResponse(['OK', 'EVER 1.2.10']).stack_ver, '1.2.10')

    def test_skappver(self):
        self.assertEqual(SkAppVerResponse(['EAPPVER rl7023-c']).app_ver, 'rl7023-c')

    def test_skinfo(self):
        res = SkInfoResponse(['EINFO FE80:0000:0000:0000:1234:5678:9ABC:DEF0'
                              ' AABBCCDDEEFF0011 21 8888 0'])

        self.assertEqual(res.ip6_addr, 'FE80:0000:0000:0000:1234:5678:9ABC:DEF0')
        self.assertEqual(res.mac_addr, bytes.fromhex('AABBCCDDEEFF0011'))
        self.assertEqual(res.channel, 0x21)
        self.assertEqual(res.pan_id, bytes.fromhex('8888'))
        self.assertEqual(res.side, 0)

    def test_skll64(self):
        res = SkLl64Response(['FE80:0000:0000:0000:1234:5678:9ABC:DEF0'])

        self.assertEqual(res.ip6_addr, 'FE80:0000:0000:0000:1234:5678:9ABC:DEF0')

    def _scan_lines(self):
        return ['EPANDESC', '  Channel:21', '  Channel Page:09', '  Pan ID:8888',
                '  Addr:AABBCCDDEEFF0011', '  LQI:6E', '  Side:0',
                '  PairID:12345678']

    def test_skscan_on_a_bp35c2(self):
        res = SkScanResponse(self._scan_lines(), BP35C2Strategy().decode_scan_side)

        self.assertEqual(res.channel, 0x21)
        self.assertEqual(res.channel_page, 0x09)
        self.assertEqual(res.pan_id, bytes.fromhex('8888'))
        self.assertEqual(res.mac_addr, bytes.fromhex('AABBCCDDEEFF0011'))
        self.assertEqual(res.lqi, 0x6E)
        self.assertAlmostEqual(res.rssi, 0.275 * 0x6E - 104.27)
        self.assertEqual(res.side, 0)
        self.assertEqual(res.pair_id, bytes.fromhex('12345678'))

    def test_skscan_on_a_bp35a1_has_no_side(self):
        res = SkScanResponse(self._scan_lines(), BP35A1Strategy().decode_scan_side)

        self.assertIsNone(res.side)
        self.assertEqual(res.channel, 0x21)


class TestAMissingFieldIsNamed(TimeBoxedTestCase):

    def test_asking_for_something_that_is_not_there(self):
        with self.assertRaises(MomongaKeyError):
            SkVerResponse(['OK'])

    def test_the_last_occurrence_wins(self):
        # the module echoes the command back before answering, so an earlier
        # line carrying the same key must not be the one that is read
        res = SkVerResponse(['SKVER', 'EVER 1.2.9', 'EVER 1.2.10'])

        self.assertEqual(res.stack_ver, '1.2.10')


if __name__ == '__main__':
    unittest.main()
