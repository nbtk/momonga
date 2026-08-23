"""
Every AsyncMomonga method against the Momonga method it stands for.

The wrapper is one line per call, and coverage found that line unexecuted for
24 of them - only the integration tests reach them, and those skip without a
dongle. One line is exactly where a wrong name or a dropped argument hides: a
getter delegating to its neighbour returns a plausible value and nothing says
otherwise.

Rather than 24 near-identical tests, the sync class is the specification: the
async surface has to mirror it, name for name and argument for argument.

Run:
  python -m unittest tests/test_async_delegation_unit.py -v
"""
import asyncio
import inspect
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga
from tests._timebox import TimeBoxedAsyncTestCase

# lifecycle and the notification reader are not plain delegations; they have
# their own tests in test_async_pools and test_async_cancellation
NOT_A_PLAIN_DELEGATION = {'open', 'close', 'reopen', 'get_notification', 'notifications'}


def _public(cls):
    # functions only - Momonga also carries the nested argument classes
    # (DayForHistoricalData1 and friends), which are callable but not methods
    return {n for n, m in inspect.getmembers(cls, inspect.isfunction)
            if not n.startswith('_')}


def _delegating_methods():
    shared = _public(Momonga) & _public(AsyncMomonga)
    return sorted(shared - NOT_A_PLAIN_DELEGATION)


class TestTheAsyncSurfaceMirrorsTheSyncOne(unittest.TestCase):

    def test_every_sync_call_has_an_async_one(self):
        missing = _public(Momonga) - _public(AsyncMomonga)

        self.assertEqual(missing, set())

    def test_each_one_takes_the_same_arguments(self):
        for name in _delegating_methods():
            with self.subTest(method=name):
                sync = inspect.signature(getattr(Momonga, name))
                asyn = inspect.signature(getattr(AsyncMomonga, name))
                self.assertEqual(list(asyn.parameters), list(sync.parameters))

    def test_each_one_is_awaitable(self):
        for name in _delegating_methods():
            with self.subTest(method=name):
                self.assertTrue(inspect.iscoroutinefunction(getattr(AsyncMomonga, name)))


class TestEachCallReachesItsOwnSyncMethod(TimeBoxedAsyncTestCase):

    async def asyncSetUp(self):
        self.amo = AsyncMomonga('', '', '/dev/ttyUSB0')
        self.amo._sync = MagicMock(spec=Momonga)
        self.addCleanup(lambda: [ex.shutdown(wait=False) for ex in
                                 (self.amo._executor, self.amo._notif_executor,
                                  self.amo._life_executor)])

    @staticmethod
    def _arguments_for(name):
        """One positional argument per parameter the method declares."""
        import datetime
        sample = {'reverse': True, 'day': 3, 'num_of_data_points': 5,
                  'timestamp': datetime.datetime(2026, 8, 23, 12, 0),
                  'properties': set(),
                  'day_for_historical_data_1': None,
                  'time_for_historical_data_2': None,
                  'time_for_historical_data_3': None}
        params = list(inspect.signature(getattr(Momonga, name)).parameters)[1:]
        return [sample[p] for p in params]

    async def test_it_calls_the_method_of_the_same_name(self):
        for name in _delegating_methods():
            with self.subTest(method=name):
                self.amo._sync.reset_mock()
                args = self._arguments_for(name)

                await getattr(self.amo, name)(*args)

                target = getattr(self.amo._sync, name)
                target.assert_called_once_with(*args)
                # and nothing else on the sync object was touched
                called = [c for c in self.amo._sync.method_calls
                          if not c[0].startswith('%s.' % name)]
                self.assertEqual([c[0] for c in called], [name])

    async def test_the_result_comes_back_to_the_caller(self):
        self.amo._sync.get_instantaneous_power.return_value = -1234

        self.assertEqual(await self.amo.get_instantaneous_power(), -1234)

    async def test_an_error_comes_back_to_the_caller(self):
        self.amo._sync.get_instantaneous_power.side_effect = ValueError('from the meter')

        with self.assertRaises(ValueError):
            await self.amo.get_instantaneous_power()


class TestTheSettingsAndStatePassThrough(TimeBoxedAsyncTestCase):

    async def test_every_sync_property_is_readable_on_the_wrapper(self):
        amo = AsyncMomonga('', '', '/dev/ttyUSB0')
        self.addCleanup(lambda: [ex.shutdown(wait=False) for ex in
                                 (amo._executor, amo._notif_executor, amo._life_executor)])
        sync_props = {n for n, v in inspect.getmembers(Momonga, lambda o: isinstance(o, property))
                      if not n.startswith('_')}
        async_props = {n for n, v in inspect.getmembers(AsyncMomonga,
                                                        lambda o: isinstance(o, property))
                       if not n.startswith('_')}

        self.assertEqual(sync_props - async_props, set())


if __name__ == '__main__':
    unittest.main()
