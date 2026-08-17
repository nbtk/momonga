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
from momonga.momonga_exception import MomongaNeedToReopen

REQUEST = '_Momonga__request'
# these tests patch time.sleep on the time module itself
REAL_SLEEP = time.sleep


def _make_mo(reopen_delays):
    mo = Momonga(rbid='', pwd='', dev='', reopen_delays=reopen_delays)
    mo.is_open = True
    return mo


class TestConcurrentRecovery(unittest.TestCase):

    def test_a_session_someone_else_rebuilt_is_not_rebuilt_again(self):
        mo = _make_mo([0.0])
        calls = []
        stale = mo.session_manager
        mo.session_manager = object()  # another thread got there first

        with patch.object(mo, 'reopen', lambda: calls.append(1)):
            getattr(mo, '_Momonga__reopen_once')(stale)

        self.assertEqual(calls, [])

    def test_a_session_that_is_still_the_failed_one_is_rebuilt(self):
        mo = _make_mo([0.0])
        calls = []

        with patch.object(mo, 'reopen', lambda: calls.append(1)):
            getattr(mo, '_Momonga__reopen_once')(mo.session_manager)

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


class TestRecoveryDoesNotRecurse(unittest.TestCase):

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


if __name__ == '__main__':
    unittest.main()
