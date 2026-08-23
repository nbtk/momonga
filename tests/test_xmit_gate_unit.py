"""
Unit tests for the two-gate transmission backpressure mechanism.

The session manager has two independent gates:
  - session gate: closed by EVENT 27/28/29, opened by EVENT 25
  - rate gate:    closed by EVENT 32,       opened by EVENT 33

Transmission is allowed only when BOTH gates are open.

Run:
  python -m unittest tests/test_xmit_gate_unit.py -v
"""
import threading
import unittest
from unittest.mock import MagicMock

from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_session_manager import MomongaSessionManager, _STOP_RECEIVER
from tests._timebox import TimeBoxedTestCase


def _make_sm():
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.session_established = True
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = MagicMock()
    sm.skw.device_strategy = BP35C2Strategy()
    return sm


def _run(sm, *events):
    """Run receiver in a thread, push events then close."""
    th = threading.Thread(target=sm._receiver, daemon=True)
    th.start()
    for ev in events:
        sm._pkt_sbsc_q.put(ev)
    sm._pkt_sbsc_q.put(_STOP_RECEIVER)
    th.join(timeout=2)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestXmitGateInitial(TimeBoxedTestCase):

    def test_initially_not_restricted(self):
        sm = _make_sm()
        self.assertFalse(sm._is_restricted_to_xmit())


# ---------------------------------------------------------------------------
# Session gate
# ---------------------------------------------------------------------------

class TestSessionGate(TimeBoxedTestCase):

    def test_session_lifetime_blocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())

    def test_session_closed_blocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 27 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())

    def test_no_session_blocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 28 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())

    def test_rejoined_unblocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 25 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_double_session_block_single_unblock_sufficient(self):
        # Boolean gate: second block is idempotent, one unblock is enough.
        # With the old counter design this would leave cnt=1 and stay blocked.
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 29 FE80::1 0', 'EVENT 25 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_spurious_rejoined_is_safe(self):
        sm = _make_sm()
        _run(sm, 'EVENT 25 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())


# ---------------------------------------------------------------------------
# Rate gate
# ---------------------------------------------------------------------------

class TestRateGate(TimeBoxedTestCase):

    def test_rate_limit_exceeded_blocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())

    def test_rate_limit_released_unblocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 33 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_double_rate_block_single_unblock_sufficient(self):
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 32 FE80::1 0', 'EVENT 33 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_spurious_rate_released_is_safe(self):
        sm = _make_sm()
        _run(sm, 'EVENT 33 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())


# ---------------------------------------------------------------------------
# Interleaved session + rate events
# Both gates must be open before transmission is allowed.
# ---------------------------------------------------------------------------

class TestXmitGateInterleaving(TimeBoxedTestCase):

    def test_32_29_still_blocked_after_33(self):
        # Rate released but session still blocked.
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 29 FE80::1 0', 'EVENT 33 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())

    def test_32_29_still_blocked_after_25(self):
        # Session restored but rate still limited.
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 29 FE80::1 0', 'EVENT 25 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())

    def test_29_32_still_blocked_after_25(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 32 FE80::1 0', 'EVENT 25 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())

    def test_29_32_still_blocked_after_33(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 32 FE80::1 0', 'EVENT 33 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())

    def test_32_29_33_25_fully_unblocked(self):
        sm = _make_sm()
        _run(sm,
             'EVENT 32 FE80::1 0',
             'EVENT 29 FE80::1 0',
             'EVENT 33 FE80::1 0',
             'EVENT 25 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_32_29_25_33_fully_unblocked(self):
        sm = _make_sm()
        _run(sm,
             'EVENT 32 FE80::1 0',
             'EVENT 29 FE80::1 0',
             'EVENT 25 FE80::1 0',
             'EVENT 33 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_29_32_33_25_fully_unblocked(self):
        sm = _make_sm()
        _run(sm,
             'EVENT 29 FE80::1 0',
             'EVENT 32 FE80::1 0',
             'EVENT 33 FE80::1 0',
             'EVENT 25 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_29_32_25_33_fully_unblocked(self):
        sm = _make_sm()
        _run(sm,
             'EVENT 29 FE80::1 0',
             'EVENT 32 FE80::1 0',
             'EVENT 25 FE80::1 0',
             'EVENT 33 FE80::1 0')
        self.assertFalse(sm._is_restricted_to_xmit())


# ---------------------------------------------------------------------------
# Force open (used by close())
# ---------------------------------------------------------------------------

class TestForceOpenGates(TimeBoxedTestCase):

    def test_force_clears_session_and_rate(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 32 FE80::1 0')
        self.assertTrue(sm._is_restricted_to_xmit())
        sm._force_open_gates()
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_force_on_already_open_is_safe(self):
        sm = _make_sm()
        sm._force_open_gates()
        self.assertFalse(sm._is_restricted_to_xmit())


class TestClosingLetsGoOfWhoeverIsWaiting(TimeBoxedTestCase):
    """Every test above reads the gates through _is_restricted_to_xmit, so all
    of them pass with close() no longer forcing them open - and a sender parked
    on the gate when the session ends waits out its whole budget for a session
    that is gone."""

    def test_a_sender_parked_on_a_shut_gate_is_woken_by_close(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0')     # session gate shut
        self.assertTrue(sm._is_restricted_to_xmit())
        woken = threading.Event()

        def waiting_to_send():
            sm._xmit_allowed.wait(20)
            woken.set()

        threading.Thread(target=waiting_to_send, daemon=True).start()
        sm.session_established = False
        sm.close()

        self.assertTrue(woken.wait(5))
        self.assertFalse(sm._is_restricted_to_xmit())

    def test_closing_reopens_both_gates_not_just_one(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 32 FE80::1 0')
        sm.session_established = False

        sm.close()

        self.assertTrue(sm._session_available)
        self.assertTrue(sm._rate_ok)


class TestClosingLeavesNoSubscriberBehind(TimeBoxedTestCase):

    def test_the_receiver_queue_is_unregistered(self):
        sm = _make_sm()
        sm.skw.subscribers = {}
        sm.skw.subscribers['pkt_sbsc_q'] = sm._pkt_sbsc_q
        sm.session_established = False

        sm.close()

        self.assertNotIn('pkt_sbsc_q', sm.skw.subscribers)


class TestAShutGateSwallowsTheTransmitEvents(TimeBoxedTestCase):
    """EVENT 21 and 02 are answers to a send. With the gate shut there is no
    send they can belong to, so letting them through leaves a stale result in
    recv_q for whatever request runs next."""

    def test_a_tx_result_arriving_on_a_shut_gate_is_dropped(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 21 FE80::1 0 00')

        self.assertTrue(sm.recv_q.empty())

    def test_a_neighbor_advertisement_on_a_shut_gate_is_dropped(self):
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 02 FE80::1 0')

        self.assertTrue(sm.recv_q.empty())

    def test_the_same_events_are_kept_while_the_gate_is_open(self):
        sm = _make_sm()
        _run(sm, 'EVENT 21 FE80::1 0 00')

        self.assertFalse(sm.recv_q.empty())


class TestClosingLetsGoOfTheReceiver(TimeBoxedTestCase):

    def test_the_thread_is_not_still_referenced_afterwards(self):
        sm = _make_sm()
        sm.session_established = False
        sm._receiver_th = threading.Thread(target=lambda: None, daemon=True)
        sm._receiver_th.start()

        sm.close()

        self.assertIsNone(sm._receiver_th)


if __name__ == '__main__':
    unittest.main()
