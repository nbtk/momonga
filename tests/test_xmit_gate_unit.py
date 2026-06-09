"""
Unit tests for the two-gate transmission backpressure mechanism.

The session manager has two independent gates:
  - session gate: closed by EVENT 27/28/29, opened by EVENT 25
  - rate gate:    closed by EVENT 32,       opened by EVENT 33

Transmission is allowed only when BOTH gates are open.

Run:
  python -m unittest tests/test_xmit_gate_unit.py -v
"""
import queue
import threading
import unittest
from unittest.mock import MagicMock

from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_session_manager import MomongaSessionManager


def _make_sm():
    sm = object.__new__(MomongaSessionManager)
    sm.pkt_sbsc_q = queue.Queue()
    sm.recv_q = queue.Queue()
    sm.notif_q = queue.Queue()
    sm.gate_lock = threading.Lock()
    sm.session_available = True
    sm.rate_ok = True
    sm.xmit_allowed = threading.Event()
    sm.xmit_allowed.set()
    sm.session_established = True
    sm.receiver_exception = None
    sm.smart_meter_addr = 'FE80::1'
    sm.on_meter_frame = None
    sm.rejoin_lock = threading.Lock()
    sm.skw = MagicMock()
    sm.skw.device_strategy = BP35C2Strategy()
    return sm


def _run(sm, *events):
    """Run receiver in a thread, push events then close."""
    th = threading.Thread(target=sm.receiver, daemon=True)
    th.start()
    for ev in events:
        sm.pkt_sbsc_q.put(ev)
    sm.pkt_sbsc_q.put('__CLOSE__')
    th.join(timeout=2)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestXmitGateInitial(unittest.TestCase):

    def test_initially_not_restricted(self):
        sm = _make_sm()
        self.assertFalse(sm.is_restricted_to_xmit())


# ---------------------------------------------------------------------------
# Session gate
# ---------------------------------------------------------------------------

class TestSessionGate(unittest.TestCase):

    def test_session_lifetime_blocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())

    def test_session_closed_blocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 27 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())

    def test_no_session_blocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 28 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())

    def test_rejoined_unblocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 25 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())

    def test_double_session_block_single_unblock_sufficient(self):
        # Boolean gate: second block is idempotent, one unblock is enough.
        # With the old counter design this would leave cnt=1 and stay blocked.
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 29 FE80::1 0', 'EVENT 25 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())

    def test_spurious_rejoined_is_safe(self):
        sm = _make_sm()
        _run(sm, 'EVENT 25 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())


# ---------------------------------------------------------------------------
# Rate gate
# ---------------------------------------------------------------------------

class TestRateGate(unittest.TestCase):

    def test_rate_limit_exceeded_blocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())

    def test_rate_limit_released_unblocks(self):
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 33 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())

    def test_double_rate_block_single_unblock_sufficient(self):
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 32 FE80::1 0', 'EVENT 33 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())

    def test_spurious_rate_released_is_safe(self):
        sm = _make_sm()
        _run(sm, 'EVENT 33 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())


# ---------------------------------------------------------------------------
# Interleaved session + rate events
# Both gates must be open before transmission is allowed.
# ---------------------------------------------------------------------------

class TestXmitGateInterleaving(unittest.TestCase):

    def test_32_29_still_blocked_after_33(self):
        # Rate released but session still blocked.
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 29 FE80::1 0', 'EVENT 33 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())

    def test_32_29_still_blocked_after_25(self):
        # Session restored but rate still limited.
        sm = _make_sm()
        _run(sm, 'EVENT 32 FE80::1 0', 'EVENT 29 FE80::1 0', 'EVENT 25 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())

    def test_29_32_still_blocked_after_25(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 32 FE80::1 0', 'EVENT 25 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())

    def test_29_32_still_blocked_after_33(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 32 FE80::1 0', 'EVENT 33 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())

    def test_32_29_33_25_fully_unblocked(self):
        sm = _make_sm()
        _run(sm,
             'EVENT 32 FE80::1 0',
             'EVENT 29 FE80::1 0',
             'EVENT 33 FE80::1 0',
             'EVENT 25 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())

    def test_32_29_25_33_fully_unblocked(self):
        sm = _make_sm()
        _run(sm,
             'EVENT 32 FE80::1 0',
             'EVENT 29 FE80::1 0',
             'EVENT 25 FE80::1 0',
             'EVENT 33 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())

    def test_29_32_33_25_fully_unblocked(self):
        sm = _make_sm()
        _run(sm,
             'EVENT 29 FE80::1 0',
             'EVENT 32 FE80::1 0',
             'EVENT 33 FE80::1 0',
             'EVENT 25 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())

    def test_29_32_25_33_fully_unblocked(self):
        sm = _make_sm()
        _run(sm,
             'EVENT 29 FE80::1 0',
             'EVENT 32 FE80::1 0',
             'EVENT 25 FE80::1 0',
             'EVENT 33 FE80::1 0')
        self.assertFalse(sm.is_restricted_to_xmit())


# ---------------------------------------------------------------------------
# Force open (used by close())
# ---------------------------------------------------------------------------

class TestForceOpenGates(unittest.TestCase):

    def test_force_clears_session_and_rate(self):
        sm = _make_sm()
        _run(sm, 'EVENT 29 FE80::1 0', 'EVENT 32 FE80::1 0')
        self.assertTrue(sm.is_restricted_to_xmit())
        sm.force_open_gates()
        self.assertFalse(sm.is_restricted_to_xmit())

    def test_force_on_already_open_is_safe(self):
        sm = _make_sm()
        sm.force_open_gates()
        self.assertFalse(sm.is_restricted_to_xmit())


if __name__ == '__main__':
    unittest.main()
