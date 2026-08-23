"""
Unit tests for serializing session recovery across threads.

Run:
  python -m unittest tests/test_recovery_serialization_unit.py -v
"""
import threading
import time
import unittest
from unittest.mock import patch

from momonga.momonga import Momonga
from momonga.momonga_exception import MomongaNeedToReopen, MomongaRuntimeError
from tests._timebox import TimeBoxedTestCase

REQUEST = '_request'
# these tests patch time.sleep on the time module itself
REAL_SLEEP = time.sleep


def _make_mo(reopen_delays):
    mo = Momonga(rbid='', pwd='', dev='', reopen_delays=reopen_delays)
    mo.is_open = True
    return mo


class TestConcurrentRecovery(TimeBoxedTestCase):

    def test_a_session_someone_else_rebuilt_is_not_rebuilt_again(self):
        mo = _make_mo([0.0])
        calls = []
        stale = mo.session_manager
        mo.session_manager = object()  # another thread got there first

        with patch.object(mo, 'reopen', lambda: calls.append(1)):
            mo._reopen_once(stale)

        self.assertEqual(calls, [])

    def test_a_session_that_is_still_the_failed_one_is_rebuilt(self):
        mo = _make_mo([0.0])
        calls = []

        with patch.object(mo, 'reopen', lambda: calls.append(1)):
            mo._reopen_once(mo.session_manager)

        self.assertEqual(calls, [1])

    def test_a_rebuild_during_the_delay_is_noticed(self):
        mo = _make_mo([0.0])
        calls = []

        def another_thread_rebuilds_it(_seconds):
            mo.session_manager = object()

        with patch.object(Momonga, REQUEST, side_effect=MomongaNeedToReopen('down')), \
             patch.object(mo, 'reopen', lambda: calls.append(1)), \
             patch('momonga.momonga.time.sleep', another_thread_rebuilds_it):
            with self.assertRaises(MomongaNeedToReopen):
                mo.get_instantaneous_power()

        self.assertEqual(calls, [])

    def test_reopen_is_never_entered_twice_at_once(self):
        mo = _make_mo([0.0] * 4)
        inside = []
        overlapped = []

        def slow_reopen():
            inside.append(1)
            if len(inside) > 1:
                overlapped.append(1)
            REAL_SLEEP(0.05)
            mo.session_manager = object()
            inside.pop()

        def run():
            try:
                mo.get_instantaneous_power()
            except MomongaNeedToReopen:
                pass

        with patch.object(Momonga, REQUEST, side_effect=MomongaNeedToReopen('down')), \
             patch.object(mo, 'reopen', slow_reopen), \
             patch('momonga.momonga.time.sleep'):
            threads = [threading.Thread(target=run) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)

        self.assertEqual(overlapped, [])


class TestRecoveryDoesNotRecurse(TimeBoxedTestCase):

    def test_requests_made_while_reopening_do_not_recover(self):
        mo = _make_mo([0.0, 0.0, 0.0])
        depth = {'now': 0, 'max': 0}

        real_reopen = mo.reopen

        def counting_reopen():
            depth['now'] += 1
            depth['max'] = max(depth['max'], depth['now'])
            if depth['now'] > 3:
                raise RuntimeError('runaway recursion')
            try:
                real_reopen()
            finally:
                depth['now'] -= 1

        with patch.object(Momonga, REQUEST, side_effect=MomongaNeedToReopen('down')), \
             patch.object(mo, 'reopen', counting_reopen), \
             patch('momonga.momonga.MomongaSessionManager'), \
             patch('momonga.momonga.time.sleep'):
            with self.assertRaises(MomongaNeedToReopen):
                mo.get_instantaneous_power()

        self.assertEqual(depth['max'], 1)

    def test_the_flag_is_cleared_when_reopen_fails(self):
        mo = _make_mo([0.0])

        with patch.object(mo, 'close'), \
             patch('momonga.momonga.MomongaSessionManager'), \
             patch.object(mo, 'open', side_effect=OSError('no device')):
            with self.assertRaises(OSError):
                mo.reopen()

        self.assertFalse(getattr(mo._local, 'reopening', False))


class TestRequestsDuringAReopen(TimeBoxedTestCase):

    def test_a_request_hands_the_reopen_to_the_recovery_loop(self):
        mo = _make_mo(None)
        mo.is_open = False
        mo._reopen_done.clear()  # a reopen is in flight

        try:
            with self.assertRaises(MomongaNeedToReopen):
                mo._request(None, [])
        finally:
            mo._reopen_done.set()

    def test_a_closed_momonga_is_still_refused_as_a_runtime_error(self):
        mo = _make_mo(None)
        mo.is_open = False

        started = time.monotonic()
        with self.assertRaises(MomongaRuntimeError):
            mo._request(None, [])
        self.assertLess(time.monotonic() - started, 1)  # not waited out

    def test_the_reopening_thread_is_not_refused_by_its_own_reopen(self):
        mo = _make_mo(None)
        mo.is_open = False
        mo._reopen_done.clear()
        outcome = []

        def run():
            mo._local.reopening = True  # thread local, so it has to be set in here
            try:
                mo._request(None, [])
            except Exception as e:
                outcome.append(type(e).__name__)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(2)
        mo._reopen_done.set()

        self.assertEqual(outcome, ['MomongaRuntimeError'])

    def test_a_session_rebuilt_before_the_request_is_not_rebuilt_again(self):
        mo = _make_mo([0.0])
        calls = []
        rebuilt = object()

        def fail_then_swap(*_args):
            mo.session_manager = rebuilt  # another thread finished a reopen
            raise MomongaNeedToReopen('a reopen is in progress')

        with patch.object(Momonga, REQUEST, side_effect=fail_then_swap), \
             patch.object(mo, 'reopen', lambda: calls.append(1)), \
             patch('momonga.momonga.time.sleep'):
            with self.assertRaises(MomongaNeedToReopen):
                mo.get_instantaneous_power()

        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
