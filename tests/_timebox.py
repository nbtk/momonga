"""A test that cannot finish should fail, not hang.

The suite is full of "this call comes back quickly" tests written as

    started = time.monotonic()
    sm.close()
    self.assertLess(time.monotonic() - started, 5)

which bounds nothing: if close() never returns, the assertion is never
reached. Mutation testing kept turning up bounds that could be removed with
the suite reporting no failure at all - it just stopped, and CI would have
hit its job timeout with nothing to point at.

Deriving from TimeBoxedTestCase runs each test method on a worker and fails
it by name once MAX_SECONDS is up. Tests keep their own assertions on how
long a call took; this is only there so a call that never returns is a red
test rather than a dead run.
"""
import asyncio
import threading
import unittest

_DEFAULT_MAX_SECONDS = 30


class TimeBoxedTestCase(unittest.TestCase):
    """A TestCase whose test methods have to finish."""

    MAX_SECONDS = _DEFAULT_MAX_SECONDS

    def _callTestMethod(self, method):
        outcome = {}

        def run():
            try:
                method()
            except BaseException as e:  # carried back to the reporting thread
                outcome['error'] = e

        worker = threading.Thread(target=run, daemon=True,
                                  name='timebox-%s' % self._testMethodName)
        worker.start()
        worker.join(self.MAX_SECONDS)
        if worker.is_alive():
            raise AssertionError('did not finish within %g s - something it '
                                 'called is not coming back' % self.MAX_SECONDS)
        if 'error' in outcome:
            raise outcome['error']


class TimeBoxedAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    """The same for the async cases, which hang by awaiting something that
    never resolves rather than by blocking a thread."""

    MAX_SECONDS = _DEFAULT_MAX_SECONDS

    def _callTestMethod(self, method):
        limit = self.MAX_SECONDS

        async def bounded():
            try:
                await asyncio.wait_for(method(), limit)
            except asyncio.TimeoutError:
                raise AssertionError('did not finish within %g s - something it '
                                     'awaited is not coming back' % limit) from None

        super()._callTestMethod(bounded)
