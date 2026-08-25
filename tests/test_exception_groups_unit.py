"""
The two names a caller needs, and what each covers.

Handling a failure means deciding one thing: build a session now, wait and try
again, or stop. The hierarchy has to make that decidable without listing every
cause, or a handler grows a line every time a new cause is added - which is
exactly what happened when MomongaIOError arrived and the examples went from
three names to four.

Two groups answer it. MomongaNeedToReopen is a session that was lost, worth
rebuilding at once. MomongaConnectionFailure is a session that was never
established, worth waiting before trying again. Everything else is not a link
problem and is deliberately outside both.

Run:
  python -m unittest tests/test_exception_groups_unit.py -v
"""
import unittest

import momonga
from momonga.momonga_exception import (MomongaConnectionFailure, MomongaError,
                                       MomongaIOError, MomongaKeyError,
                                       MomongaNeedToReopen,
                                       MomongaResponseNotExpected,
                                       MomongaResponseNotPossible,
                                       MomongaRuntimeError,
                                       MomongaSkCommandBusy,
                                       MomongaSkCommandCancelled,
                                       MomongaSkJoinFailure,
                                       MomongaSkScanFailure,
                                       MomongaTimeoutError, MomongaValueError,
                                       MomongaXmitTimeout)
from tests._timebox import TimeBoxedTestCase

# no session yet: wait, then try to connect again
CONNECTING = (MomongaSkScanFailure, MomongaSkJoinFailure, MomongaTimeoutError,
              MomongaIOError)
# a session that has gone: build a new one
LOST = (MomongaXmitTimeout, MomongaSkCommandBusy, MomongaSkCommandCancelled)
# neither: retrying the link does not address these
NOT_A_LINK_PROBLEM = (MomongaResponseNotPossible, MomongaResponseNotExpected,
                      MomongaRuntimeError, MomongaValueError, MomongaKeyError)


class TestConnectingFailuresAreOneGroup(TimeBoxedTestCase):

    def test_each_of_them_is_a_connection_failure(self):
        for exc in CONNECTING:
            with self.subTest(exception=exc.__name__):
                self.assertTrue(issubclass(exc, MomongaConnectionFailure))

    def test_the_group_is_not_the_lost_session_one(self):
        # the two decisions are different, so the two names have to be
        self.assertFalse(issubclass(MomongaConnectionFailure, MomongaNeedToReopen))
        self.assertFalse(issubclass(MomongaNeedToReopen, MomongaConnectionFailure))

    def test_a_lost_session_is_not_a_connection_failure(self):
        for exc in LOST + (MomongaNeedToReopen,):
            with self.subTest(exception=exc.__name__):
                self.assertFalse(issubclass(exc, MomongaConnectionFailure))

    def test_what_is_not_a_link_problem_is_in_neither(self):
        for exc in NOT_A_LINK_PROBLEM:
            with self.subTest(exception=exc.__name__):
                self.assertFalse(issubclass(exc, MomongaConnectionFailure))
                self.assertFalse(issubclass(exc, MomongaNeedToReopen))


class TestOnlyOneOfTheGroupsIsRaisedOnItsOwn(TimeBoxedTestCase):
    """The manual says MomongaConnectionFailure exists only to be caught and
    that MomongaNeedToReopen is also raised directly. Both halves are checked
    against the source, since a `raise MomongaConnectionFailure(...)` added
    later would make the first sentence wrong without anything failing."""

    @staticmethod
    def _raised_directly(name):
        import pathlib as _p
        return sum(src.read_text().count('raise %s(' % name)
                   for src in _p.Path('momonga').glob('*.py'))

    def test_the_connecting_group_is_never_raised_directly(self):
        self.assertEqual(self._raised_directly('MomongaConnectionFailure'), 0)

    def test_the_lost_session_group_is(self):
        self.assertGreater(self._raised_directly('MomongaNeedToReopen'), 0)


class TestLostSessionsAreTheOtherGroup(TimeBoxedTestCase):

    def test_each_of_them_is_a_need_to_reopen(self):
        for exc in LOST:
            with self.subTest(exception=exc.__name__):
                self.assertTrue(issubclass(exc, MomongaNeedToReopen))


class TestTheGroupsDoNotBreakWhatWasThere(TimeBoxedTestCase):
    """Grouping is additive: every exception is still a MomongaError, and the
    ones that carry a builtin still carry it."""

    def test_everything_is_still_a_momonga_error(self):
        for exc in CONNECTING + LOST + NOT_A_LINK_PROBLEM + (MomongaNeedToReopen,):
            with self.subTest(exception=exc.__name__):
                self.assertTrue(issubclass(exc, MomongaError))

    def test_the_builtin_mixins_are_intact(self):
        for exc, builtin in ((MomongaTimeoutError, TimeoutError),
                             (MomongaIOError, OSError),
                             (MomongaRuntimeError, RuntimeError),
                             (MomongaValueError, ValueError),
                             (MomongaKeyError, KeyError)):
            with self.subTest(exception=exc.__name__):
                self.assertTrue(issubclass(exc, builtin))

    def test_the_recovery_loop_still_covers_what_it_did(self):
        # _request_with_recovery catches (MomongaNeedToReopen, OSError)
        covered = (MomongaNeedToReopen, OSError)
        for exc in LOST + (MomongaNeedToReopen, MomongaIOError, MomongaTimeoutError):
            with self.subTest(exception=exc.__name__):
                self.assertTrue(issubclass(exc, covered))

    def test_both_groups_are_exported(self):
        self.assertIs(momonga.MomongaConnectionFailure, MomongaConnectionFailure)
        self.assertIs(momonga.MomongaNeedToReopen, MomongaNeedToReopen)


class TestNeitherGroupIsAboutWhen(TimeBoxedTestCase):
    """The two groups say which layer failed, not whether a session existed
    yet. Both halves of that were once written down the other way round, so
    both are pinned here."""

    def test_open_issues_requests_so_a_lost_session_error_can_come_from_it(self):
        # open() reads the cumulative energy unit, which is a request, so a
        # MomongaNeedToReopen can arrive before the caller ever holds the object
        import queue
        from unittest.mock import MagicMock, patch
        from momonga.momonga import Momonga

        issued = []
        sm = MagicMock()
        sm.recv_q, sm.notif_q = queue.Queue(), queue.Queue()
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.session_manager = sm
        mo.internal_xmit_interval = 0

        def failing(_self, esv, _props):
            issued.append(esv)
            raise MomongaXmitTimeout('no transmit rights')

        with patch.object(Momonga, '_request', failing):
            with self.assertRaises(MomongaXmitTimeout):
                mo.open()

        self.assertEqual(len(issued), 1)

    def test_a_connection_failure_can_arrive_with_a_session_established(self):
        # a dongle pulled mid-session is a MomongaIOError, not something that
        # only happens before connecting
        from unittest.mock import MagicMock
        import serial
        from momonga.momonga_sk_wrapper import MomongaSkWrapper

        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        ser = MagicMock()
        ser.closed = False
        ser.write.side_effect = serial.SerialException('device disconnected')
        skw._ser = ser

        with self.assertRaises(MomongaIOError):
            skw._writeline('SKVER')

        self.assertTrue(issubclass(MomongaIOError, MomongaConnectionFailure))


class TestTwoNamesCoverTheLinkFailures(TimeBoxedTestCase):

    def test_one_tuple_catches_every_link_failure(self):
        handler = (MomongaConnectionFailure, MomongaNeedToReopen)

        for exc in CONNECTING + LOST:
            with self.subTest(exception=exc.__name__):
                self.assertTrue(issubclass(exc, handler))

    def test_and_catches_nothing_else(self):
        handler = (MomongaConnectionFailure, MomongaNeedToReopen)

        for exc in NOT_A_LINK_PROBLEM:
            with self.subTest(exception=exc.__name__):
                self.assertFalse(issubclass(exc, handler))


if __name__ == '__main__':
    unittest.main()
