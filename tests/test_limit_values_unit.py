"""
The numbers the limits are set to, not just the machinery that reads them.

Every test that exercises a limit patches it down to something small, so the
mechanism is covered and the value is not: mutation testing showed
_SKTERM_LIMIT, _PUBLISHER_JOIN_LIMIT and _BUF_CLEAR_LIMIT could all be set to
nonsense with the whole suite still green.

Asserting the numbers themselves would only mirror the source. What is worth
holding is why each one is where it is - a limit that outlasts the thing it
was meant to bound is not a limit, and one shorter than the work it covers
breaks a healthy call.

Run:
  python -m unittest tests/test_limit_values_unit.py -v
"""
import unittest

from momonga.momonga import _INFC_RES_XMIT_LIMIT
from momonga.momonga_async import (_DEFAULT_MAX_WORKERS, _NOTIFICATION_POLL,
                                   _RESERVED_WORKERS)
from momonga.momonga_session_manager import (_RECEIVER_JOIN_LIMIT,
                                             _REJOIN_LOCK_LIMIT, _SKTERM_LIMIT)
from momonga.momonga_sk_wrapper import (_BUF_CLEAR_LIMIT, _PUBLISHER_JOIN_LIMIT,
                                        _SK_COMMAND_LIMIT)
from tests._timebox import TimeBoxedTestCase

# what the module is documented to take, from the estimates in skscan/skjoin
LONGEST_SCAN = 69.1
LONGEST_JOIN = 40
# the per-read timeouts the loops below them use
PUBLISHER_READ = 1      # _readline(timeout=1) in received_packet_publisher
CLEAR_BUF_READ = 2      # self._ser.timeout = 2 in _clear_buf


class TestACloseLimitOutlivesNothing(TimeBoxedTestCase):
    """These exist so close() comes back. A command already gives up after
    _SK_COMMAND_LIMIT, so a limit at or above it bounds nothing that was not
    already bounded - which is precisely how close() came to wait 300 s for a
    SKTERM nobody answered."""

    def test_skterm_gives_up_before_the_command_would(self):
        self.assertLess(_SKTERM_LIMIT, _SK_COMMAND_LIMIT)

    def test_the_publisher_join_gives_up_before_the_command_would(self):
        self.assertLess(_PUBLISHER_JOIN_LIMIT, _SK_COMMAND_LIMIT)

    def test_the_receiver_join_gives_up_before_the_command_would(self):
        self.assertLess(_RECEIVER_JOIN_LIMIT, _SK_COMMAND_LIMIT)

    def test_clearing_the_buffer_gives_up_before_the_command_would(self):
        self.assertLess(_BUF_CLEAR_LIMIT, _SK_COMMAND_LIMIT)


class TestALimitCoversTheWorkBeneathIt(TimeBoxedTestCase):
    """The other direction: shorter than the work it covers and a healthy call
    starts failing."""

    def test_a_command_outlasts_the_longest_scan_and_join(self):
        self.assertGreater(_SK_COMMAND_LIMIT, max(LONGEST_SCAN, LONGEST_JOIN))

    def test_the_publisher_join_outlasts_one_read(self):
        self.assertGreater(_PUBLISHER_JOIN_LIMIT, PUBLISHER_READ)

    def test_clearing_the_buffer_outlasts_one_read(self):
        self.assertGreater(_BUF_CLEAR_LIMIT, CLEAR_BUF_READ)

    def test_the_rejoin_lock_outlasts_a_terminate(self):
        # close() takes the rejoin lock and then terminates; waiting less for
        # the lock than the terminate can take would make the order pointless
        self.assertGreaterEqual(_REJOIN_LOCK_LIMIT, _SKTERM_LIMIT)

    def test_the_whole_close_comes_back_before_one_command_would(self):
        # every wait close() can sit through, back to back. A cleanup that
        # outlasts a single command is worse than what it is cleaning up after,
        # and the rejoin lock is the largest term in it
        self.assertLess(_REJOIN_LOCK_LIMIT + _SKTERM_LIMIT + _RECEIVER_JOIN_LIMIT
                        + _PUBLISHER_JOIN_LIMIT + _BUF_CLEAR_LIMIT, _SK_COMMAND_LIMIT)


class TestTheReplyBudgetSitsBetweenTwoCosts(TimeBoxedTestCase):
    """Long enough to ride out the transmission gate, short enough that a
    notification is not held behind it. Ten months of logs put the gate at a
    median of 2 s and a worst case of 42 s."""

    OBSERVED_GATE_MEDIAN = 2

    def test_it_covers_a_typical_gate_closure(self):
        self.assertGreater(_INFC_RES_XMIT_LIMIT, self.OBSERVED_GATE_MEDIAN)

    def test_it_does_not_dominate_the_read_it_delays(self):
        self.assertLess(_INFC_RES_XMIT_LIMIT, _SKTERM_LIMIT)


class TestThePoolsCanDoWhatTheyPromise(TimeBoxedTestCase):

    def test_a_dedicated_pool_keeps_a_spare(self):
        # one to run the call, one so a call nobody is waiting for any more
        # cannot keep the next one from starting
        self.assertGreaterEqual(_RESERVED_WORKERS, 2)

    def test_the_general_pool_holds_a_request_and_something_else(self):
        self.assertGreaterEqual(_DEFAULT_MAX_WORKERS, 2)

    def test_a_cancelled_read_is_not_left_for_long(self):
        self.assertLessEqual(_NOTIFICATION_POLL, 1)


if __name__ == '__main__':
    unittest.main()
