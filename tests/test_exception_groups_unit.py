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
                                       MomongaSkResponseNotExpected,
                                       MomongaSkScanFailure,
                                       MomongaTimeoutError, MomongaValueError,
                                       MomongaXmitTimeout)
from tests._timebox import TimeBoxedTestCase

# no session yet: wait, then try to connect again
CONNECTING = (MomongaSkScanFailure, MomongaSkJoinFailure, MomongaTimeoutError,
              MomongaIOError, MomongaSkResponseNotExpected, MomongaKeyError)
# a session that has gone: build a new one
LOST = (MomongaXmitTimeout, MomongaSkCommandBusy, MomongaSkCommandCancelled)
# neither: retrying the link does not address these
NOT_A_LINK_PROBLEM = (MomongaResponseNotPossible, MomongaResponseNotExpected,
                      MomongaRuntimeError, MomongaValueError)


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


class TestGroupingClassesAreOnlyForCatching(TimeBoxedTestCase):
    """A class that exists to gather others should not also be a failure in its
    own right - raising it says "something went wrong" and nothing more, when a
    named subclass was available. MomongaError was raised directly twice: for a
    ROPT reply that was neither OK nor a readable FAIL, and for an out of range
    WOPT option, both of which had a fitting subclass already.

    MomongaNeedToReopen is the exception to that, deliberately: eleven places
    raise it because the caller's answer to all of them is the same session
    rebuild and no finer name would be acted on.
    """

    GROUPING = ('MomongaError', 'MomongaConnectionFailure',
                'MomongaSkCommandExecutionFailure')

    @staticmethod
    def _raised_directly(name):
        import pathlib as _p
        return sum(src.read_text().count('raise %s(' % name)
                   for src in _p.Path('momonga').glob('*.py'))

    def test_no_grouping_class_is_raised_on_its_own(self):
        for name in self.GROUPING:
            with self.subTest(exception=name):
                self.assertEqual(self._raised_directly(name), 0)

    def test_each_grouping_class_actually_groups_something(self):
        import momonga
        from momonga import momonga_exception as module
        for name in self.GROUPING:
            with self.subTest(exception=name):
                base = getattr(momonga, name)
                subs = [c for c in vars(module).values()
                        if isinstance(c, type) and issubclass(c, base) and c is not base]
                self.assertGreater(len(subs), 0)

    def test_the_lost_session_group_is_raised_directly_on_purpose(self):
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


class TestEveryExceptionIsInTheManual(TimeBoxedTestCase):
    """An exception a caller can be handed but cannot look up is a gap in the
    manual, and adding one is easy to do without noticing. MomongaValueError
    and MomongaKeyError were both missing: the first is raised by argument
    checks on the public constructor, the second escapes open() when a scan
    response arrives truncated.

    A heading is not required - the six FAIL ERxx subclasses are listed inside
    the entry for the class that groups them - but the name has to be there.
    """

    def test_no_exception_is_left_out(self):
        import pathlib
        import momonga
        from momonga import momonga_exception as module
        manual = pathlib.Path('README.md').read_text()
        for name, cls in vars(module).items():
            if not (isinstance(cls, type) and issubclass(cls, BaseException)
                    and cls.__module__ == module.__name__):
                continue
            with self.subTest(exception=name):
                self.assertIn(name, manual)
                self.assertIs(getattr(momonga, name, None), cls)

if __name__ == '__main__':
    unittest.main()
