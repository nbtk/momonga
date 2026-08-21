"""
Unit tests for which failures a reopen is allowed to retry.

Run:
  python -m unittest tests/test_recovery_scope_unit.py -v
"""
import unittest
from unittest.mock import patch

import momonga
from momonga.momonga import EchonetServiceCode


def _make_mo(delays=(256.0, 1024.0)):
    return momonga.Momonga('rbid', 'pwd', '/dev/null', reopen_delays=list(delays))


def _run(mo, request_side_effect, reopen_side_effect=None):
    calls = []

    def counted(_esv, _props):
        calls.append(1)
        outcome = request_side_effect(len(calls))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with patch.object(mo, '_request', side_effect=counted), \
            patch.object(mo, 'reopen', side_effect=reopen_side_effect) as reopen, \
            patch('momonga.momonga.time.sleep'):
        try:
            result = mo._request_with_recovery(EchonetServiceCode.get, [])
            raised = None
        except Exception as e:
            result, raised = None, e
    return result, raised, len(calls), reopen.call_count


class TestAPermanentFailureIsNotRetried(unittest.TestCase):

    def test_an_unsupported_epc_after_a_reopen_reaches_the_caller(self):
        mo = _make_mo()
        _r, raised, requests, reopens = _run(
            mo, lambda n: momonga.MomongaNeedToReopen('session lost') if n == 1
            else momonga.MomongaResponseNotPossible('EPC unsupported'))

        self.assertIsInstance(raised, momonga.MomongaResponseNotPossible)
        self.assertEqual(requests, 2)  # not one for every delay
        self.assertEqual(reopens, 1)

    def test_an_unsupported_epc_is_the_same_error_with_or_without_a_reopen(self):
        mo = _make_mo()
        _r, with_reopen, _c, _o = _run(
            mo, lambda n: momonga.MomongaNeedToReopen('session lost') if n == 1
            else momonga.MomongaResponseNotPossible('EPC unsupported'))
        _r, without_reopen, _c, _o = _run(
            _make_mo(), lambda n: momonga.MomongaResponseNotPossible('EPC unsupported'))

        self.assertIs(type(with_reopen), type(without_reopen))

    def test_a_bad_argument_after_a_reopen_reaches_the_caller(self):
        mo = _make_mo()
        _r, raised, requests, _o = _run(
            mo, lambda n: momonga.MomongaNeedToReopen('session lost') if n == 1
            else momonga.MomongaValueError('bad argument'))

        self.assertIsInstance(raised, momonga.MomongaValueError)
        self.assertEqual(requests, 2)


class TestASessionFailureIsStillRetried(unittest.TestCase):

    def test_a_lost_session_uses_every_delay(self):
        mo = _make_mo()
        _r, raised, requests, reopens = _run(
            mo, lambda n: momonga.MomongaNeedToReopen('still failing'))

        self.assertIsInstance(raised, momonga.MomongaNeedToReopen)
        self.assertEqual(requests, 3)  # the first attempt and one per delay
        self.assertEqual(reopens, 2)

    def test_an_os_error_from_the_first_attempt_is_recoverable_too(self):
        mo = _make_mo()
        result, raised, requests, reopens = _run(
            mo, lambda n: OSError('port gone') if n == 1 else ['ok'])

        self.assertIsNone(raised)
        self.assertEqual(result, ['ok'])
        self.assertEqual(reopens, 1)

    def test_a_reopen_that_cannot_find_the_pan_is_still_retried(self):
        mo = _make_mo()
        _r, raised, _c, reopens = _run(
            mo, lambda n: momonga.MomongaNeedToReopen('session lost'),
            reopen_side_effect=momonga.MomongaSkScanFailure('no PAN'))

        self.assertIsInstance(raised, momonga.MomongaNeedToReopen)
        self.assertEqual(reopens, 2)  # a failing reopen does not stop the schedule

    def test_a_recovered_request_returns_its_result(self):
        mo = _make_mo()
        result, raised, requests, reopens = _run(
            mo, lambda n: momonga.MomongaNeedToReopen('session lost') if n == 1 else ['ok'])

        self.assertIsNone(raised)
        self.assertEqual(result, ['ok'])
        self.assertEqual(requests, 2)


if __name__ == '__main__':
    unittest.main()
