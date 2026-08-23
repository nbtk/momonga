"""
Unit tests for reopen_delays being read from the start on every recovery.

Run:
  python -m unittest tests/test_reopen_delays_replay_unit.py -v
"""
import unittest
from itertools import repeat

from momonga.momonga import Momonga
from tests._timebox import TimeBoxedTestCase


def _schedule_of(reopen_delays, recoveries=3, cap=5):
    # mirrors "for delay in self.reopen_delays" in __request_with_recovery
    mo = Momonga(rbid='', pwd='', dev='', reopen_delays=reopen_delays)

    rounds = []
    for _ in range(recoveries):
        attempts = 0
        for _delay in mo.reopen_delays:
            attempts += 1
            if attempts >= cap:
                break
        rounds.append(attempts)
    return rounds


class TestEveryRecoveryStartsOver(TimeBoxedTestCase):

    def test_list(self):
        self.assertEqual(_schedule_of([600.0, 600.0, 600.0]), [3, 3, 3])

    def test_generator(self):
        self.assertEqual(_schedule_of(600.0 for _ in range(3)), [3, 3, 3])

    def test_iterator_over_a_list(self):
        self.assertEqual(_schedule_of(iter([600.0, 600.0, 600.0])), [3, 3, 3])

    def test_tuple(self):
        self.assertEqual(_schedule_of((600.0, 600.0, 600.0)), [3, 3, 3])

    def test_an_infinite_source_stays_infinite(self):
        self.assertEqual(_schedule_of(repeat(600.0), cap=5), [5, 5, 5])

    def test_a_recovery_that_succeeds_early_does_not_shorten_the_next(self):
        mo = Momonga(rbid='', pwd='', dev='', reopen_delays=(600.0 for _ in range(3)))

        first = 0
        for _delay in mo.reopen_delays:
            first += 1
            if first == 2:
                break  # reconnected on the second attempt

        second = sum(1 for _ in mo.reopen_delays)

        self.assertEqual(first, 2)
        self.assertEqual(second, 3)

    def test_the_delays_themselves_are_preserved(self):
        mo = Momonga(rbid='', pwd='', dev='', reopen_delays=iter([1.0, 2.0, 3.0]))
        self.assertEqual(list(mo.reopen_delays), [1.0, 2.0, 3.0])
        self.assertEqual(list(mo.reopen_delays), [1.0, 2.0, 3.0])

    def test_a_list_is_stored_untouched(self):
        delays = [600.0, 600.0]
        mo = Momonga(rbid='', pwd='', dev='', reopen_delays=delays)
        self.assertIs(mo.reopen_delays, delays)

    def test_none_is_still_none(self):
        mo = Momonga(rbid='', pwd='', dev='', reopen_delays=None)
        self.assertIsNone(mo.reopen_delays)


if __name__ == '__main__':
    unittest.main()
