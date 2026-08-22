"""
Unit tests for the time a single request may spend waiting to transmit.

Run:
  python -m unittest tests/test_xmit_timeout_unit.py -v
"""
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga
from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_exception import MomongaNeedToReopen, MomongaXmitTimeout
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import _SK_COMMAND_LIMIT


def _make_mo(gate_open=True):
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.session_established = True
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.device_strategy = BP35C2Strategy()
    if not gate_open:
        sm._xmit_allowed.clear()
    mo = Momonga('', '', '/dev/ttyUSB0')
    mo.is_open = True
    mo.session_manager = sm
    mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 12, 0.05, 0
    return mo, sm


class TestOneBudgetPerRequest(unittest.TestCase):

    def test_the_budget_bounds_the_request_not_each_retry(self):
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout = 1

        started = time.monotonic()
        with self.assertRaises(MomongaXmitTimeout):
            mo.get_instantaneous_power()
        elapsed = time.monotonic() - started

        # unbounded the first gate wait alone is 60 s; per retry it would be 12 s
        self.assertLess(elapsed, 3)

    def test_no_budget_restores_the_full_gate_wait(self):
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout = None
        waits = []
        with patch.object(sm._xmit_allowed, 'wait',
                          lambda timeout=None: waits.append(timeout) or False):
            with self.assertRaises(MomongaNeedToReopen):
                mo.get_instantaneous_power()
        self.assertEqual(waits[0], 60)  # the whole 60 s, not a slice of a budget

    def test_without_a_budget_a_reopening_gate_repeats_the_whole_schedule(self):
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout = None
        waits = []

        def opens_on_the_last_wait(timeout=None):
            waits.append(timeout)
            return len(waits) % 60 == 0

        with patch.object(sm._xmit_allowed, 'wait', opens_on_the_last_wait):
            with self.assertRaises(MomongaNeedToReopen):
                mo.get_instantaneous_power()

        self.assertEqual(len(waits), 60 * mo.xmit_retries)  # what the budget is for


class TestTheBudgetIsSplitBetweenTheTwoWaits(unittest.TestCase):

    def _send_kwargs(self, xmit_timeout):
        mo, sm = _make_mo(gate_open=True)
        mo.xmit_timeout = xmit_timeout
        try:
            mo.get_instantaneous_power()
        except MomongaNeedToReopen:
            pass
        return sm.skw.sksendto.call_args.kwargs

    def test_the_lock_wait_is_not_capped_by_the_command_limit(self):
        # the receiver holds _cmd_lock while it rejoins, and the budget is what
        # says how long a request may wait for it - not the per-command limit
        kwargs = self._send_kwargs(900)
        self.assertGreater(kwargs['lock_timeout'], _SK_COMMAND_LIMIT)

    def test_the_response_wait_is_capped_by_the_command_limit(self):
        kwargs = self._send_kwargs(900)
        self.assertEqual(kwargs['timeout'], _SK_COMMAND_LIMIT)

    def test_a_budget_under_the_command_limit_shortens_both(self):
        kwargs = self._send_kwargs(30)
        self.assertLessEqual(kwargs['timeout'], 30)
        self.assertLessEqual(kwargs['lock_timeout'], 30)


class TestTheBudgetReachesRecovery(unittest.TestCase):

    def test_a_spent_budget_is_caught_by_the_reopen_handler(self):
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout = 0
        with self.assertRaises(MomongaNeedToReopen):  # what reopen_delays waits on
            mo.get_instantaneous_power()

    def test_a_spent_budget_triggers_an_automatic_reopen(self):
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout = 0
        mo.reopen_delays = [0]
        reopened = []
        mo.reopen = lambda: reopened.append(1)
        with self.assertRaises(MomongaNeedToReopen):
            mo.get_instantaneous_power()
        self.assertEqual(len(reopened), 1)


class TestTheResponseWaitIsUntouched(unittest.TestCase):

    def test_an_open_gate_still_gives_the_documented_figure(self):
        mo, sm = _make_mo(gate_open=True)
        mo.xmit_retries, mo.recv_timeout = 3, 0.2

        started = time.monotonic()
        with self.assertRaises(MomongaNeedToReopen):
            mo.get_instantaneous_power()
        elapsed = time.monotonic() - started

        self.assertAlmostEqual(elapsed, 3 * 0.2, delta=0.5)


class TestTheSettingIsReachableFromAsync(unittest.TestCase):

    def test_async_reads_and_writes_it(self):
        mo = AsyncMomonga('', '', '/dev/ttyUSB0')
        try:
            self.assertEqual(mo.xmit_timeout, 300)
            mo.xmit_timeout = 30
            self.assertEqual(mo._sync.xmit_timeout, 30)
        finally:
            mo._executor.shutdown(wait=False)


if __name__ == '__main__':
    unittest.main()
