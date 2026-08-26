"""
Unit tests for the time a single request may spend waiting to transmit.

Run:
  python -m unittest tests/test_xmit_timeout_unit.py -v
"""
import threading
import momonga
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga
from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_exception import MomongaNeedToReopen, MomongaXmitTimeout
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase


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


class TestOneBudgetPerRequest(TimeBoxedTestCase):

    def test_the_budget_bounds_the_request_not_each_retry(self):
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout = 1

        started = time.monotonic()
        with self.assertRaises(MomongaXmitTimeout):
            mo.get_instantaneous_power()
        elapsed = time.monotonic() - started

        # unbounded the first gate wait alone is 60 s; per retry it would be 12 s
        self.assertLess(elapsed, 3)

    def test_what_each_retry_is_handed_is_what_the_last_one_left(self):
        # the wall clock above only separates one budget from twelve by a
        # hair when xmit_retries is small, so read the budgets themselves:
        # not subtracting what an attempt spent gives every one the full
        # figure, and the request quietly runs for retries x xmit_timeout
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout, mo.xmit_retries = 1, 3
        handed = []

        SPENT = 0.1

        def record(payload, timeout=None):
            handed.append(timeout)
            time.sleep(SPENT)  # the send goes out; no reply ever comes back

        sm.xmitter = record
        with self.assertRaises(MomongaNeedToReopen):
            mo.get_instantaneous_power()

        self.assertEqual(len(handed), 3)
        for spent_before, left in zip(handed, handed[1:]):
            self.assertLessEqual(left, spent_before - SPENT)

    def test_the_longest_budget_restores_the_full_gate_wait(self):
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout = 3600   # the schedule xmitter ran before it had a budget
        waits = []
        with patch.object(sm._xmit_allowed, 'wait',
                          lambda timeout=None: waits.append(timeout) or False):
            with self.assertRaises(MomongaNeedToReopen):
                mo.get_instantaneous_power()
        self.assertEqual(waits[0], 60)  # the whole 60 s, not a slice of a budget

    def test_with_the_longest_budget_a_reopening_gate_repeats_the_schedule(self):
        mo, sm = _make_mo(gate_open=False)
        mo.xmit_timeout = 3600
        waits = []

        def opens_on_the_last_wait(timeout=None):
            waits.append(timeout)
            return len(waits) % 60 == 0

        with patch.object(sm._xmit_allowed, 'wait', opens_on_the_last_wait):
            with self.assertRaises(MomongaNeedToReopen):
                mo.get_instantaneous_power()

        self.assertEqual(len(waits), 60 * mo.xmit_retries)  # what the budget is for


class TestTheLockWaitAndTheCommandWaitShareOneBudget(TimeBoxedTestCase):
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

    def test_every_send_is_given_a_deadline_now(self):
        """There is no longer a setting that leaves one off. Without a deadline
        the SK command waits on the command lock with no bound at all, which is
        what xmit_timeout=None used to do underneath its promise of no ceiling."""
        mo, sm = _make_mo(gate_open=True)
        mo.xmit_timeout = 3600
        try:
            mo.get_instantaneous_power()
        except MomongaNeedToReopen:
            pass
        self.assertIsNotNone(sm.skw.sksendto.call_args.kwargs.get('deadline'))


class TestTheBudgetCoversTransmittingOnly(TimeBoxedTestCase):
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

    def test_the_same_holds_with_the_longest_budget(self):
        self.assertEqual(self._attempts(3600), 6)

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


class TestTheBudgetReachesRecovery(TimeBoxedTestCase):

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


class TestTheResponseWaitIsUntouched(TimeBoxedTestCase):

    def test_an_open_gate_still_gives_the_documented_figure(self):
        mo, sm = _make_mo(gate_open=True)
        mo.xmit_retries, mo.recv_timeout = 3, 0.2

        started = time.monotonic()
        with self.assertRaises(MomongaNeedToReopen):
            mo.get_instantaneous_power()
        elapsed = time.monotonic() - started

        self.assertAlmostEqual(elapsed, 3 * 0.2, delta=0.5)


class TestTheSettingIsReachableFromAsync(TimeBoxedTestCase):

    def test_async_reads_and_writes_it(self):
        mo = AsyncMomonga('', '', '/dev/ttyUSB0')
        try:
            self.assertEqual(mo.xmit_timeout, 300)
            mo.xmit_timeout = 30
            self.assertEqual(mo._sync.xmit_timeout, 30)
        finally:
            mo._executor.shutdown(wait=False)


class TestThereIsNoLongerAValueThatMeansNoCeiling(TimeBoxedTestCase):
    """None was documented as no ceiling and was not one. The gate wait ran its
    own schedule of sixty waits of a minute and then raised MomongaNeedToReopen
    rather than MomongaXmitTimeout, and the SK command underneath was handed no
    deadline, so it waited on the command lock with no bound - the one wait that
    really was unbounded, sitting behind the setting that promised to remove
    every bound.

    3600 runs the identical schedule with neither surprise, so nothing was lost
    by taking the value away.
    """

    def _momonga(self):
        with patch.object(MomongaSkWrapper, '__init__', lambda s, *a, **k: None):
            return momonga.Momonga('id', 'pw', '/dev/ttyUSB0')

    def test_none_is_refused(self):
        mo = self._momonga()

        with self.assertRaises(momonga.MomongaValueError):
            mo.xmit_timeout = None

    def test_the_refusal_says_what_to_write_instead(self):
        mo = self._momonga()

        with self.assertRaises(momonga.MomongaValueError) as caught:
            mo.xmit_timeout = None

        self.assertIn('3600', str(caught.exception))

    def test_a_negative_budget_is_refused_too(self):
        mo = self._momonga()

        with self.assertRaises(momonga.MomongaValueError):
            mo.xmit_timeout = -1

    def test_zero_is_still_allowed_because_it_means_do_not_wait(self):
        mo = self._momonga()

        mo.xmit_timeout = 0

        self.assertEqual(mo.xmit_timeout, 0)

    def test_the_default_is_unchanged(self):
        self.assertEqual(self._momonga().xmit_timeout, 300)

    def test_a_refused_value_leaves_the_old_one_in_place(self):
        mo = self._momonga()
        mo.xmit_timeout = 45

        with self.assertRaises(momonga.MomongaValueError):
            mo.xmit_timeout = None

        self.assertEqual(mo.xmit_timeout, 45)

    def test_the_async_wrapper_refuses_it_as_well(self):
        amo = momonga.AsyncMomonga('id', 'pw', '/dev/ttyUSB0')
        self.addCleanup(lambda: [ex.shutdown(wait=False) for ex in
                                 (amo._executor, amo._notif_executor,
                                  amo._life_executor)])

        with self.assertRaises(momonga.MomongaValueError):
            amo.xmit_timeout = None


if __name__ == '__main__':
    unittest.main()
