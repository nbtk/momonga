"""
Unit tests for the time a single request may spend waiting to transmit.

Run:
  python -m unittest tests/test_xmit_timeout_unit.py -v
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga
from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_exception import MomongaNeedToReopen, MomongaXmitTimeout
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper


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


class TestTheLockWaitAndTheCommandWaitShareOneBudget(unittest.TestCase):
    """exec_command restarts its own clock once it holds the lock, so passing the
    same figure as both waits would let one send spend the budget twice."""

    def _timed_send(self, budget, lock_held_for):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._writeline = lambda line, payload=None: None   # the module never answers
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        skw._cmd_lock.acquire()
        threading.Timer(lock_held_for, skw._cmd_lock.release).start()
        started = time.monotonic()
        try:
            sm.xmitter(b'\x00', timeout=budget)
        except MomongaNeedToReopen:
            pass
        return time.monotonic() - started

    def test_a_send_does_not_spend_its_budget_twice(self):
        elapsed = self._timed_send(budget=1.5, lock_held_for=1.0)
        self.assertLess(elapsed, 2.2)  # not 1.0 waiting plus 1.5 more

    def test_the_budget_still_covers_a_lock_that_frees_in_time(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        answered = []

        def answer(line, payload=None):
            answered.append(line)
            skw.subscribers['cmd_exec_q'].put('OK')

        skw._writeline = answer
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        skw._cmd_lock.acquire()
        threading.Timer(0.3, skw._cmd_lock.release).start()

        sm.xmitter(b'\x00', timeout=5)  # must not raise
        self.assertEqual(len(answered), 1)

    def test_no_budget_leaves_the_command_limit_in_charge(self):
        mo, sm = _make_mo(gate_open=True)
        mo.xmit_timeout = None
        try:
            mo.get_instantaneous_power()
        except MomongaNeedToReopen:
            pass
        self.assertIsNone(sm.skw.sksendto.call_args.kwargs.get('deadline'))


class TestTheBudgetCoversTransmittingOnly(unittest.TestCase):
    """The budget is for getting the packet out. Waiting for the meter to answer
    is what recv_timeout and xmit_retries are for, and must not spend it."""

    def _attempts(self, xmit_timeout):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        sent = []

        def ack(line, payload=None):        # the module answers, the meter does not
            sent.append(line)
            threading.Timer(0.02, lambda: skw.subscribers['cmd_exec_q'].put('OK')).start()

        skw._writeline = ack
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 6, 0.3, 0
        mo.xmit_timeout = xmit_timeout
        with self.assertRaises(MomongaNeedToReopen):
            mo.get_instantaneous_power()
        return len(sent)

    def test_every_retry_happens_though_the_budget_is_short(self):
        # 6 x 0.3 s of listening is well past a 1 s budget, but the gate was open
        # and the module answered each time, so nothing of the budget was used
        self.assertEqual(self._attempts(1), 6)

    def test_the_same_holds_with_no_budget_at_all(self):
        self.assertEqual(self._attempts(None), 6)

    def test_a_spent_budget_is_not_what_ends_it(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._writeline = lambda line, payload=None: threading.Timer(
            0.02, lambda: skw.subscribers['cmd_exec_q'].put('OK')).start()
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 6, 0.3, 0
        mo.xmit_timeout = 1
        with self.assertRaises(MomongaNeedToReopen) as caught:
            mo.get_instantaneous_power()
        self.assertNotIsInstance(caught.exception, MomongaXmitTimeout)


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
