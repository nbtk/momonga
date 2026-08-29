"""Closing a session while the module announces it has rejoined.

close() marks the session gone, then keeps the receiver running so SKTERM's
own events can be read, and only afterwards stops it. Anything the receiver
writes in that window outlives the close, and EVENT 25 writes the one flag
close() had just cleared - so a closed session manager reported an established
session and passed every check xmitter makes before sending.

The gates never had this problem because close() reopens them after the
receiver has stopped rather than before. session_established was the one piece
of state set on the near side of that line.

A lock alone does not fix it: close() releases _rejoin_lock before it stops the
receiver, so an EVENT 25 that arrives after the release still lands. What
settles it is that a close in progress says so, and the receiver leaves the
flag alone.

Reaching the window is a matter of timing on real hardware. Here the event is
injected from inside close() itself, at a point that is by definition after the
flag was cleared and before the receiver is asked to stop, so the test either
demonstrates the fault or does not - it never sometimes does.

Run:
  python -m unittest tests/test_close_race_unit.py -v
"""
import queue
import threading
import time
import unittest

from unittest.mock import MagicMock

import momonga

from momonga.momonga_device import BP35C2Strategy
from momonga.momonga_session_manager import (MomongaSessionManager,
                                             _STOP_RECEIVER)

from tests._timebox import TimeBoxedTestCase

REJOINED = 'EVENT 25 FE80::1 0'
LIFETIME_EXPIRED = 'EVENT 29 FE80::1 0'

_PATIENCE = 5.0


class _SessionManagerTestCase(TimeBoxedTestCase):

    def _session_manager(self, **skw):
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = MagicMock()
        sm.skw.device_strategy = BP35C2Strategy()
        for name, value in skw.items():
            getattr(sm.skw, name).side_effect = value
        return sm

    def _start_receiver(self, sm):
        thread = threading.Thread(target=sm._receiver, daemon=True)
        thread.start()
        self.addCleanup(self._stop_receiver, sm, thread)
        return thread

    def _stop_receiver(self, sm, thread):
        if thread.is_alive():
            sm._pkt_sbsc_q.put(_STOP_RECEIVER)
            thread.join(timeout=_PATIENCE)

    def _wait_until(self, predicate, what):
        deadline = time.monotonic() + _PATIENCE
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail('Waited %s seconds for %s.' % (_PATIENCE, what))


class TestARejoinDuringCloseIsIgnored(_SessionManagerTestCase):

    def _close_with_a_rejoin_in_the_window(self, sm):
        """Deliver EVENT 25 after close() cleared the flag, before it stops
        the receiver. cancel_commands() is called exactly between the two."""
        delivered = threading.Event()

        def cancel_commands():
            sm._pkt_sbsc_q.put(REJOINED)
            sm._pkt_sbsc_q.put(LIFETIME_EXPIRED)   # a marker, queued behind it
            self._wait_until(lambda: not sm._session_available,
                             'the receiver to work through the rejoin')
            delivered.set()

        sm.skw.cancel_commands.side_effect = cancel_commands
        sm.close()

        self.assertTrue(delivered.is_set())

    def test_the_session_stays_closed(self):
        sm = self._session_manager()
        self._start_receiver(sm)

        self._close_with_a_rejoin_in_the_window(sm)

        self.assertFalse(sm.session_established)

    def test_a_closed_manager_does_not_look_ready_to_transmit(self):
        """The three things xmitter checks before it sends anything."""
        sm = self._session_manager()
        self._start_receiver(sm)

        self._close_with_a_rejoin_in_the_window(sm)

        self.assertFalse(sm.session_established and sm._xmit_allowed.is_set()
                         and sm.receiver_exception is None)

    def test_the_receiver_reports_no_trouble_of_its_own(self):
        sm = self._session_manager()
        self._start_receiver(sm)

        self._close_with_a_rejoin_in_the_window(sm)

        self.assertIsNone(sm.receiver_exception)

    def test_the_session_end_still_reaches_the_caller(self):
        sm = self._session_manager()
        self._start_receiver(sm)

        self._close_with_a_rejoin_in_the_window(sm)

        self.assertIs(sm.notif_q.get_nowait(), momonga.momonga_session_manager.SESSION_ENDED)


class TestARejoinHeldUpByCloseIsIgnoredToo(_SessionManagerTestCase):
    """close() holds _rejoin_lock across SKTERM, so a rejoin arriving then
    waits on it and is handled after the release - later than the window
    above, and still no reason to revive the session."""

    def test_it_does_not_revive_the_session(self):
        holding = threading.Event()
        sm = self._session_manager(skterm=lambda *a, **k: (holding.set(),
                                                           time.sleep(0.2)))
        self._start_receiver(sm)
        closing = threading.Thread(target=sm.close)
        closing.start()
        self.addCleanup(closing.join, _PATIENCE)

        self._wait_until(holding.is_set, 'close() to reach SKTERM')
        sm._pkt_sbsc_q.put(REJOINED)
        closing.join(timeout=_PATIENCE)

        self.assertFalse(closing.is_alive())
        self.assertFalse(sm.session_established)


class TestRejoiningStillWorksWhenNothingIsClosing(_SessionManagerTestCase):
    """The guard must not cost the event its ordinary job: the meter drops the
    session on EVENT 29 and the module wins it back on EVENT 25."""

    def test_a_rejoin_marks_the_session_established_again(self):
        sm = self._session_manager()
        self._start_receiver(sm)
        sm.session_established = False

        sm._pkt_sbsc_q.put(REJOINED)

        self._wait_until(lambda: sm.session_established, 'the rejoin')

    def test_a_lifetime_expiry_shuts_the_gate_and_a_rejoin_opens_it(self):
        sm = self._session_manager()
        self._start_receiver(sm)

        sm._pkt_sbsc_q.put(LIFETIME_EXPIRED)
        self._wait_until(lambda: not sm._session_available, 'the gate to shut')
        sm._pkt_sbsc_q.put(REJOINED)

        self._wait_until(lambda: sm._session_available, 'the gate to open')

    def test_the_flag_survives_a_rejoin_after_a_close_and_a_fresh_open(self):
        """open() clears the closing mark, or the object never works again."""
        sm = self._session_manager()
        self._start_receiver(sm)
        sm.close()

        sm._closing = False          # what open() does before it starts work
        sm.session_established = False
        self._start_receiver(sm)
        sm._pkt_sbsc_q.put(REJOINED)

        self._wait_until(lambda: sm.session_established, 'the rejoin')


if __name__ == '__main__':
    unittest.main()
