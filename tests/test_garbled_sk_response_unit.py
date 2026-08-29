"""A reply from the module that arrives damaged.

Every SK response is parsed with int(x, 16) and bytes.fromhex(x) over whatever
the serial line handed back. Those raise ValueError, and the field lookup
raises LookupError, so a line lost to noise left the library through a public
entry point as a bare ValueError - not a MomongaError at all, and so outside
every handler the manual tells anyone to write.

Reading it is a link failure like any other and clears on the next try, so the
whole family is MomongaConnectionFailure now. MomongaKeyError keeps its
KeyError mixin for the case where a field is genuinely absent; a field that is
present but unreadable is not a KeyError and does not claim to be one.

A scan is the one place that retries by itself, because it is already in a
retry loop and has just spent a minute of radio time earning that answer.

Run:
  python -m unittest tests/test_garbled_sk_response_unit.py -v
"""
import unittest

from unittest.mock import patch

import momonga

from momonga.momonga_device import BP35C2Strategy
from momonga.momonga_response import (SkInfoResponse,
                                      SkScanResponse,
                                      SkVerResponse)
from momonga.momonga_sk_wrapper import MomongaSkWrapper

from tests._timebox import TimeBoxedTestCase

EPANDESC = ['EPANDESC', '  Channel:21', '  Channel Page:09', '  Pan ID:8888',
            '  Addr:1234567890ABCDEF', '  LQI:E1', '  Side:0',
            '  PairID:00112233']


def _replacing(prefix, line):
    return [line if l.startswith(prefix) else l for l in EPANDESC]


class TestAScanDescriptionThatCannotBeRead(TimeBoxedTestCase):

    def _scan(self, lines):
        return SkScanResponse(lines, BP35C2Strategy().decode_scan_side)

    def test_a_complete_one_still_reads(self):
        res = self._scan(EPANDESC)

        self.assertEqual((res.channel, res.pan_id), (0x21, b'\x88\x88'))

    def test_every_way_it_can_arrive_damaged_is_a_connection_failure(self):
        for label, lines in (
                ('the last line never came', EPANDESC[:-1]),
                ('a line in the middle was lost', EPANDESC[:1] + EPANDESC[2:]),
                ('a value is empty', _replacing('  Channel:', '  Channel:')),
                ('a value is not hex', _replacing('  LQI:', '  LQI:ZZ')),
                ('a value lost a digit', _replacing('  Pan ID:', '  Pan ID:888')),
                ('a strategy field is empty', _replacing('  Side:', '  Side:'))):
            with self.subTest(damage=label):
                with self.assertRaises(momonga.MomongaConnectionFailure):
                    self._scan(lines)

    def test_a_missing_field_is_still_a_KeyError(self):
        with self.assertRaises(KeyError):
            self._scan(EPANDESC[:-1])

    def test_an_unreadable_value_does_not_pretend_to_be_one(self):
        with self.assertRaises(momonga.MomongaSkResponseNotExpected) as caught:
            self._scan(_replacing('  LQI:', '  LQI:ZZ'))

        self.assertNotIsInstance(caught.exception, KeyError)

    def test_what_broke_is_kept_as_the_cause(self):
        with self.assertRaises(momonga.MomongaSkResponseNotExpected) as caught:
            self._scan(_replacing('  LQI:', '  LQI:ZZ'))

        self.assertIsInstance(caught.exception.__cause__, ValueError)


class TestTheOtherRepliesAreParsedTheSameWay(TimeBoxedTestCase):

    def test_none_of_them_leaks_a_bare_error(self):
        for cls, lines, label in (
                (SkInfoResponse, ['EINFO FE80::1 ZZZZ 21 8888 0'], 'EINFO, bad MAC'),
                (SkInfoResponse, ['EINFO FE80::1'], 'EINFO, cut short'),
                (SkVerResponse, ['EVER'], 'EVER, no version')):
            with self.subTest(reply=label):
                with self.assertRaises(momonga.MomongaConnectionFailure):
                    cls(lines)

    def test_a_good_one_is_untouched(self):
        res = SkInfoResponse(['EINFO FE80::1 1234567890ABCDEF 21 8888 0'])

        self.assertEqual((res.channel, res.side), (0x21, 0))


class TestScanningTriesAgainRatherThanGivingUp(TimeBoxedTestCase):

    NOT_FOUND = ['EVENT 22']
    DAMAGED = EPANDESC[:-1]

    def _scan(self, replies, retry=3):
        skw = MomongaSkWrapper.__new__(MomongaSkWrapper)
        skw.device_strategy = BP35C2Strategy()
        seq = iter(replies)
        with patch.object(MomongaSkWrapper, 'exec_command',
                          lambda _self, *a, **k: next(seq)):
            return skw.skscan(retry=retry)

    def test_a_damaged_description_costs_one_attempt_not_the_scan(self):
        res = self._scan([self.DAMAGED, EPANDESC, EPANDESC])

        self.assertEqual(res.channel, 0x21)

    def test_it_keeps_going_to_the_last_attempt(self):
        res = self._scan([self.DAMAGED, self.DAMAGED, EPANDESC])

        self.assertEqual(res.channel, 0x21)

    def test_running_out_is_a_scan_failure_like_any_other(self):
        with self.assertRaises(momonga.MomongaSkScanFailure):
            self._scan([self.DAMAGED] * 3)

    def test_and_says_a_PAN_was_there(self):
        """"Could not find the specified PAN" would send anyone to the aerial."""
        with self.assertRaises(momonga.MomongaSkScanFailure) as caught:
            self._scan([self.DAMAGED] * 3)

        self.assertIn('Found a PAN', str(caught.exception))

    def test_finding_nothing_still_says_so(self):
        with self.assertRaises(momonga.MomongaSkScanFailure) as caught:
            self._scan([self.NOT_FOUND] * 3)

        self.assertIn('Could not find', str(caught.exception))

    def test_a_clean_scan_does_not_retry(self):
        res = self._scan([EPANDESC], retry=3)

        self.assertEqual(res.pan_id, b'\x88\x88')


if __name__ == '__main__':
    unittest.main()
