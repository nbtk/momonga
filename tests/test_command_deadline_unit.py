"""A command that has no time left, told apart from one that has no lock.

close(), a rejoin and every packet send give their SK command a deadline. When
that deadline has already passed there is nothing useful left to do, and the
command is not run - which is right. What was wrong was the reason given for
it: MomongaSkCommandBusy, whose entry in the manual says another SK command is
running. Nothing had to be running. Reading the log meant looking for a
contention that was not there.

The two halves behaved differently as well. A caller that could be told to
stop never touched the lock and was told it was busy. A caller that could not
acquired the lock, and then sent the command with a timeout of zero - bytes out
of the port with no time to read the answer, leaving a reply in the buffer for
whatever ran next to trip over.

Run:
  python -m unittest tests/test_command_deadline_unit.py -v
"""
import threading
import time
import unittest

import momonga

from momonga.momonga_sk_wrapper import MomongaSkWrapper

from tests._timebox import TimeBoxedTestCase

PASSED = -1.0        # a deadline already behind us
PLENTY = 5.0


class _Module(MomongaSkWrapper):
    """A wrapper whose module answers instantly, and says what it was asked."""

    def __init__(self, answer_after=0.0):
        self._cmd_lock = threading.Lock()
        self._answer_after = answer_after
        self.asked = []

    def _exec_command_locked(self, command, wait_until, timeout, payload, should_stop):
        self.asked.append((' '.join(c for c in command if c is not None), timeout))
        if self._answer_after:
            time.sleep(self._answer_after)
        return ['EVENT 24 FE80::1 0']


class TestRunningOutOfTimeSaysSo(TimeBoxedTestCase):

    def _expired(self, **kwargs):
        module = _Module()
        with self.assertRaises(momonga.MomongaSkCommandDeadlineExceeded) as caught:
            module.exec_command(['SKVER'], 'EVER',
                                deadline=time.monotonic() + PASSED, **kwargs)
        return module, caught.exception

    def test_a_caller_that_can_be_told_to_stop_hears_the_real_reason(self):
        _module, error = self._expired(should_stop=lambda: False)

        self.assertIn('Ran out of time', str(error))

    def test_so_does_one_that_cannot(self):
        _module, error = self._expired()

        self.assertIn('Ran out of time', str(error))

    def test_the_command_is_not_sent_either_way(self):
        for kwargs in ({}, {'should_stop': lambda: False}):
            with self.subTest(should_stop='should_stop' in kwargs):
                module, _error = self._expired(**kwargs)

                self.assertEqual(module.asked, [])

    def test_the_lock_is_not_left_held(self):
        module, _error = self._expired()

        self.assertFalse(module._cmd_lock.locked())

    def test_it_names_the_command_with_its_secrets_hidden(self):
        module = _Module()

        with self.assertRaises(momonga.MomongaSkCommandDeadlineExceeded) as caught:
            module.exec_command(['SKSETPWD', 'C', 'a real password'],
                                deadline=time.monotonic() + PASSED)

        self.assertIn('SKSETPWD', str(caught.exception))
        self.assertNotIn('a real password', str(caught.exception))


class TestABusyLockStillSaysBusy(TimeBoxedTestCase):
    """The distinction is only worth making if the other name still means what
    the manual says it means."""

    def test_a_lock_someone_else_holds_is_reported_as_busy(self):
        module = _Module()
        module._cmd_lock.acquire()

        with self.assertRaises(momonga.MomongaSkCommandBusy):
            module.exec_command(['SKVER'], 'EVER', lock_timeout=0.05)

    def test_that_is_true_for_a_caller_that_can_be_told_to_stop_too(self):
        module = _Module()
        module._cmd_lock.acquire()

        with self.assertRaises(momonga.MomongaSkCommandBusy):
            module.exec_command(['SKVER'], 'EVER', lock_timeout=0.05,
                                should_stop=lambda: False)

    def test_a_deadline_that_has_not_passed_still_waits_for_the_lock(self):
        module = _Module()
        module._cmd_lock.acquire()

        with self.assertRaises(momonga.MomongaSkCommandBusy):
            module.exec_command(['SKVER'], 'EVER',
                                deadline=time.monotonic() + 0.05)


class TestTimeThatIsLeftIsStillUsed(TimeBoxedTestCase):

    def test_a_command_with_time_to_spare_runs(self):
        module = _Module()

        result = module.exec_command(['SKVER'], 'EVENT 24',
                                     deadline=time.monotonic() + PLENTY)

        self.assertEqual(result, ['EVENT 24 FE80::1 0'])

    def test_what_is_left_becomes_the_command_timeout(self):
        module = _Module()

        module.exec_command(['SKVER'], 'EVENT 24',
                            deadline=time.monotonic() + PLENTY)

        self.assertAlmostEqual(module.asked[0][1], PLENTY, delta=0.2)

    def test_a_deadline_never_lengthens_a_shorter_timeout(self):
        module = _Module()

        module.exec_command(['SKVER'], 'EVENT 24', timeout=0.5,
                            deadline=time.monotonic() + PLENTY)

        self.assertAlmostEqual(module.asked[0][1], 0.5, delta=0.2)

    def test_no_deadline_at_all_is_unchanged(self):
        module = _Module()

        module.exec_command(['SKVER'], 'EVENT 24', timeout=7)

        self.assertEqual(module.asked[0][1], 7)


class TestARetryLoopStopsWhenItsTimeIsUp(TimeBoxedTestCase):
    """skjoin hands every attempt the same deadline, so the second one starts
    with nothing left. That is where the wrong name showed up in practice."""

    def test_skjoin_gives_up_rather_than_running_out_its_retries(self):
        module = _Module(answer_after=0.3)

        with self.assertRaises(momonga.MomongaSkCommandDeadlineExceeded):
            module.skjoin('FE80::1', retry=3,
                          deadline=time.monotonic() + 0.2,
                          should_stop=lambda: False)

        self.assertEqual(len(module.asked), 1)

    def test_it_still_reaches_every_attempt_when_there_is_time(self):
        module = _Module()

        with self.assertRaises(momonga.MomongaSkJoinFailure):
            module.skjoin('FE80::1', retry=3, deadline=time.monotonic() + PLENTY)

        self.assertEqual(len(module.asked), 3)


if __name__ == '__main__':
    unittest.main()
