"""What leaving the with block does when saying goodbye also fails.

close() sends SKTERM, so it can fail on its own - and it runs at the moment an
exception is already on its way out. Letting that second failure win loses the
first one, which is the one the caller needs: MomongaNeedToReopen turns into a
MomongaSkCommandFailedToExecute that belongs to neither group, so a loop built
on the two group names ends instead of rebuilding.

reopen() has guarded its own close() from the start. __exit__ had not, so the
same hazard reached anyone using the context manager.

A close that fails on the way out of a clean block still raises. Nothing is
being masked there, and a session that would not shut down is worth hearing
about.

Run:
  python -m unittest tests/test_exit_masking_unit.py -v
"""
import unittest

import momonga

from unittest.mock import patch

from momonga.momonga import Momonga

from tests._timebox import TimeBoxedAsyncTestCase, TimeBoxedTestCase


class _SessionManager:
    """Stands in for the real one: opens, and fails to close if told to."""

    def __init__(self, close_error=None):
        self.close_error = close_error
        self.closed = False

    def open(self):
        pass

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class _ExitTestCase(TimeBoxedTestCase):

    def _momonga(self, close_error=None):
        mo = Momonga.__new__(Momonga)
        mo.is_open = False
        mo.session_manager = _SessionManager(close_error)
        # the real open() goes on to read properties off a meter
        self.enterContext(patch.object(
            Momonga, 'open', lambda self: (self.session_manager.open(),
                                           setattr(self, 'is_open', True), self)[-1]))
        return mo

    def _leave(self, mo, raising=None):
        """Run the with block, returning whatever came out of it."""
        try:
            with mo:
                if raising is not None:
                    raise raising
        except BaseException as e:
            return e
        return None


class TestACloseFailureDoesNotReplaceTheRealOne(_ExitTestCase):

    def test_the_lost_session_survives_a_failed_goodbye(self):
        lost = momonga.MomongaNeedToReopen('the session is gone')
        mo = self._momonga(momonga.MomongaSkCommandFailedToExecute('ER10'))

        self.assertIs(self._leave(mo, raising=lost), lost)

    def test_so_a_loop_on_the_group_names_still_catches_it(self):
        mo = self._momonga(momonga.MomongaSkCommandFailedToExecute('ER10'))

        out = self._leave(mo, raising=momonga.MomongaNeedToReopen('gone'))

        self.assertIsInstance(out, (momonga.MomongaNeedToReopen,
                                    momonga.MomongaConnectionFailure))

    def test_the_port_is_let_go_even_so(self):
        mo = self._momonga(momonga.MomongaIOError('the dongle was pulled'))

        self._leave(mo, raising=momonga.MomongaNeedToReopen('gone'))

        self.assertTrue(mo.session_manager.closed)

    def test_every_kind_of_close_failure_is_kept_quiet(self):
        for error in (momonga.MomongaSkCommandFailedToExecute('ER10'),
                      momonga.MomongaIOError('the dongle was pulled'),
                      momonga.MomongaTimeoutError('no answer'),
                      RuntimeError('something nobody predicted')):
            with self.subTest(close_failure=type(error).__name__):
                original = momonga.MomongaNeedToReopen('gone')

                self.assertIs(self._leave(self._momonga(error),
                                          raising=original), original)


class TestNothingElseChanges(_ExitTestCase):

    def test_a_clean_block_still_reports_a_close_that_failed(self):
        mo = self._momonga(momonga.MomongaSkCommandFailedToExecute('ER10'))

        out = self._leave(mo)

        self.assertIsInstance(out, momonga.MomongaSkCommandFailedToExecute)

    def test_a_clean_block_with_a_clean_close_raises_nothing(self):
        self.assertIsNone(self._leave(self._momonga()))

    def test_the_session_is_closed_on_the_way_out(self):
        mo = self._momonga()

        self._leave(mo)

        self.assertTrue(mo.session_manager.closed)


class TestTheAsyncWrapperKeepsItToo(TimeBoxedAsyncTestCase):
    """AsyncMomonga runs the same close() on a thread, so it inherits both the
    hazard and the guard. Its executors have to be let go either way."""

    async def _momonga(self, close_error=None):
        from momonga.momonga_async import AsyncMomonga
        amo = AsyncMomonga('', '', '/dev/ttyUSB0')
        amo._sync = Momonga.__new__(Momonga)
        amo._sync.is_open = True
        amo._sync.session_manager = _SessionManager(close_error)
        self.enterContext(patch.object(
            Momonga, 'open', lambda self: setattr(self, 'is_open', True)))
        return amo

    async def _leave(self, amo, raising=None):
        try:
            async with amo:
                if raising is not None:
                    raise raising
        except BaseException as e:
            return e
        return None

    async def test_the_lost_session_survives_a_failed_goodbye(self):
        amo = await self._momonga(momonga.MomongaSkCommandFailedToExecute('ER10'))
        lost = momonga.MomongaNeedToReopen('the session is gone')

        self.assertIs(await self._leave(amo, raising=lost), lost)

    async def test_a_clean_block_still_reports_a_close_that_failed(self):
        amo = await self._momonga(momonga.MomongaSkCommandFailedToExecute('ER10'))

        self.assertIsInstance(await self._leave(amo),
                              momonga.MomongaSkCommandFailedToExecute)

    async def test_the_executors_are_shut_down_whatever_happened(self):
        amo = await self._momonga(momonga.MomongaSkCommandFailedToExecute('ER10'))

        await self._leave(amo, raising=momonga.MomongaNeedToReopen('gone'))

        for executor in (amo._executor, amo._notif_executor, amo._life_executor):
            self.assertTrue(executor._shutdown)


if __name__ == '__main__':
    unittest.main()
