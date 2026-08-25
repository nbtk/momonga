"""
Unit tests for cutting a stuck SK command loose so close() cannot outlast it.

Run:
  python -m unittest tests/test_command_cancellation_unit.py -v
"""
import logging
import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_exception import (MomongaNeedToReopen, MomongaSkCommandBusy,
                                       MomongaSkCommandCancelled)
from momonga.momonga_session_manager import (MomongaSessionManager,
                                             _STOP_RECEIVER)
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

WRITELINE = '_writeline'


def _silent_module(line, payload=None):
    pass


def _make_skw():
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    return skw


def _make_sm(skw, session_established):
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.session_established = session_established
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = skw
    skw.subscribers['pkt_sbsc_q'] = sm._pkt_sbsc_q
    return sm


class TestAWaitingCommandIsCutLoose(TimeBoxedTestCase):

    def test_a_command_waiting_on_the_module_is_released(self):
        skw = _make_skw()
        outcome = []

        def run():
            try:
                skw.exec_command(['SKJOIN', 'FE80::1'], 'EVENT 25', timeout=300)
            except Exception as e:
                outcome.append(type(e).__name__)

        with patch.object(skw, WRITELINE, _silent_module):
            t = threading.Thread(target=run, daemon=True)
            t.start()
            time.sleep(0.05)
            self.assertTrue(t.is_alive())  # sitting out the command limit

            skw.cancel_commands()
            t.join(5)

        self.assertFalse(t.is_alive())
        self.assertEqual(outcome, ['MomongaSkCommandCancelled'])

    def test_a_later_command_is_refused_before_it_is_written(self):
        skw = _make_skw()
        skw.cancel_commands()

        with patch.object(skw, WRITELINE) as writeline:
            with self.assertRaises(MomongaSkCommandCancelled):
                skw.exec_command(['SKVER'])

        writeline.assert_not_called()

    def test_a_retry_loop_stops_instead_of_running_out_its_retries(self):
        skw = _make_skw()
        attempts = []

        def fake_writeline(line, payload=None):
            attempts.append(line)
            skw.cancel_commands()

        with patch.object(skw, WRITELINE, fake_writeline):
            with self.assertRaises(MomongaSkCommandCancelled):
                skw.skjoin('FE80::1')  # retry=3 by default

        self.assertEqual(len(attempts), 1)


class TestALockedOutCommandGivesUp(TimeBoxedTestCase):

    def test_a_bounded_command_does_not_wait_out_the_holder(self):
        skw = _make_skw()
        skw._cmd_lock.acquire()  # another thread is mid-command
        try:
            started = time.monotonic()
            with self.assertRaises(MomongaSkCommandBusy):
                skw.skterm(lock_timeout=0.2)
            self.assertLess(time.monotonic() - started, 5)  # not the command limit
        finally:
            skw._cmd_lock.release()

    def test_being_locked_out_is_not_reported_as_a_cancellation(self):
        skw = _make_skw()
        skw._cmd_lock.acquire()
        try:
            with self.assertRaises(MomongaSkCommandBusy) as caught:
                skw.skterm(lock_timeout=0.1)
            self.assertNotIsInstance(caught.exception, MomongaSkCommandCancelled)
            self.assertIsInstance(caught.exception, MomongaNeedToReopen)  # handlers still work
        finally:
            skw._cmd_lock.release()

    def test_an_unbounded_command_still_waits_by_default(self):
        skw = _make_skw()
        released = threading.Event()

        skw._cmd_lock.acquire()
        threading.Timer(0.1, lambda: (skw._cmd_lock.release(), released.set())).start()

        with patch.object(skw, WRITELINE, lambda line, payload=None:
                          skw.subscribers['cmd_exec_q'].put('EVENT 27 FE80::1')):
            skw.skterm()

        self.assertTrue(released.is_set())  # it waited for the holder


class TestACancellationIsNotRetried(TimeBoxedTestCase):

    def test_xmitter_gives_up_on_the_first_cancelled_send(self):
        skw = _make_skw()
        sm = _make_sm(skw, session_established=True)
        skw.cancel_commands()  # what close() does

        started = time.monotonic()
        with self.assertRaises(MomongaNeedToReopen):
            sm.xmitter(b'\x00')
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1)  # not three rounds of the retry sleep
        self.assertEqual(skw._ser.write.call_count, 0)


class TestAReceiverWaitingOnSomeoneElsesCommandIsCutLooseToo(TimeBoxedTestCase):
    """The case above has the receiver holding the command lock and waiting for
    the module, which cancel_commands() reaches because that wait is on the
    queue the cancellation arrives on. A receiver waiting *for* the lock is not
    on that queue and never sees it - it sits there until whoever holds the
    lock lets go, which is up to a full command limit, and holds _rejoin_lock
    the whole time. That is the one part of running a command that cancelling
    cannot interrupt, so waiting for the lock is done in slices with a look at
    whether the caller still wants the command at all.
    """

    @staticmethod
    def _let_everything_go(sm, skw, thread):
        """Let the held command lock go before waiting on the receiver, or the
        wait is the very block under test. Cancel first: a mocked port never
        answers the SKJOIN the receiver sends the moment the lock is free."""
        skw.cancel_commands()
        if skw._cmd_lock.locked():
            skw._cmd_lock.release()
        sm._pkt_sbsc_q.put(_STOP_RECEIVER)
        thread.join(5)

    def _rejoining_receiver(self, sm, skw):
        """The receiver, rejoining, with the command lock held by a user
        thread that the module has stopped answering."""
        skw._cmd_lock.acquire()
        thread = threading.Thread(target=sm._receiver, daemon=True)
        thread.start()
        self.addCleanup(self._let_everything_go, sm, skw, thread)
        sm._pkt_sbsc_q.put('EVENT 24 FE80::1 0')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if sm._rejoin_lock.locked():
                return thread
            time.sleep(0.005)
        self.fail('The receiver never started rejoining.')

    def test_close_does_not_wait_for_the_holder_to_let_go(self):
        skw = _make_skw()
        sm = _make_sm(skw, session_established=True)
        self._rejoining_receiver(sm, skw)

        started = time.monotonic()
        sm.close()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5)   # the holder is still holding

    def test_the_rejoin_lets_go_of_its_own_lock(self):
        skw = _make_skw()
        sm = _make_sm(skw, session_established=True)
        self._rejoining_receiver(sm, skw)

        sm.close()

        self.assertFalse(sm._rejoin_lock.locked())

    def test_giving_up_that_way_is_not_reported_as_a_fault(self):
        """The receiver is being closed, not failing."""
        skw = _make_skw()
        sm = _make_sm(skw, session_established=True)
        self._rejoining_receiver(sm, skw)

        sm.close()

        self.assertIsNone(sm.receiver_exception)

    def test_a_rejoin_nobody_is_closing_still_waits_its_turn(self):
        """The slices are a way to look at _closing, not a shorter patience."""
        skw = _make_skw()
        sm = _make_sm(skw, session_established=True)
        self._rejoining_receiver(sm, skw)

        time.sleep(0.6)   # more than one slice

        self.assertTrue(sm._rejoin_lock.locked())


class TestCloseDoesNotWaitOutAStuckReceiver(TimeBoxedTestCase):

    def test_close_finishes_while_the_receiver_sits_in_skjoin(self):
        skw = _make_skw()
        sm = _make_sm(skw, session_established=True)
        rejoining = threading.Event()
        stuck = []

        def stuck_rejoin():
            sm._rejoin_lock.acquire()  # what _receiver() does on EVENT 26
            rejoining.set()
            try:
                with patch.object(skw, WRITELINE, _silent_module):
                    skw.skjoin(sm.smart_meter_addr)
            except Exception as e:
                stuck.append(type(e).__name__)
            finally:
                sm._rejoin_lock.release()

        sm._receiver_th = threading.Thread(target=stuck_rejoin, daemon=True)
        sm._receiver_th.start()
        self.assertTrue(rejoining.wait(5))
        time.sleep(0.05)

        # the real limits are 120 s and 30 s; the shape is what is under test
        with patch('momonga.momonga_session_manager._REJOIN_LOCK_LIMIT', 0.2), \
             patch('momonga.momonga_session_manager._SKTERM_LIMIT', 0.2):
            started = time.monotonic()
            sm.close()
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5)  # not the three command limits skjoin would take
        self.assertEqual(stuck, ['MomongaSkCommandCancelled'])
        self.assertFalse(sm._rejoin_lock.locked())

    def test_close_does_not_need_the_limit_shortened_to_be_quick(self):
        """The test above patches _REJOIN_LOCK_LIMIT down to 0.2 s, so it says
        nothing about the two minutes a caller actually waits. Cancelling does
        reach a receiver sitting on the module, but close() only sends the
        cancellation once it has finished waiting for the lock that receiver is
        holding - which is the wait it would have cut short. Being able to ask
        whether anyone still wants the rejoin closes that circle.
        """
        skw = _make_skw()
        sm = _make_sm(skw, session_established=True)
        skw._writeline = _silent_module
        thread = threading.Thread(target=sm._receiver, daemon=True)
        thread.start()
        self.addCleanup(self._let_go, sm, skw, thread)
        sm._pkt_sbsc_q.put('EVENT 24 FE80::1 0')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not sm._rejoin_lock.locked():
            time.sleep(0.005)
        self.assertTrue(sm._rejoin_lock.locked())

        started = time.monotonic()
        sm.close()

        self.assertLess(time.monotonic() - started, 5)  # not the real 120
        self.assertIsNone(sm.receiver_exception)

    @staticmethod
    def _let_go(sm, skw, thread):
        skw.cancel_commands()
        sm._pkt_sbsc_q.put(_STOP_RECEIVER)
        thread.join(5)

    def test_an_ordinary_close_still_terminates_the_session(self):
        skw = _make_skw()
        sm = _make_sm(skw, session_established=True)
        sent = []

        def answer(line, payload=None):
            sent.append(line)
            skw.subscribers['cmd_exec_q'].put('EVENT 27 FE80::1')

        with patch.object(skw, WRITELINE, answer):
            sm.close()

        self.assertEqual(sent, ['SKTERM'])


class TestCancellingNothingIsNotAWarning(TimeBoxedTestCase):

    def _levels(self, skw):
        records = []

        class Cap(logging.Handler):
            def emit(self, record):
                records.append(record.levelname)

        lg = logging.getLogger('momonga.momonga_sk_wrapper')
        cap = Cap()
        lg.addHandler(cap)
        level = lg.level
        lg.setLevel(logging.DEBUG)
        try:
            skw.cancel_commands()
        finally:
            lg.removeHandler(cap)
            lg.setLevel(level)
        return records

    def test_an_idle_wrapper_is_cancelled_quietly(self):
        skw = _make_skw()
        self.assertNotIn('WARNING', self._levels(skw))

    def test_a_running_command_still_warns(self):
        skw = _make_skw()
        skw._cmd_lock.acquire()  # a command is in flight
        try:
            self.assertIn('WARNING', self._levels(skw))
        finally:
            skw._cmd_lock.release()


class _CancellingQueue(queue.Queue):
    """Cancels while the command is draining, so the sentinel lands mid-drain."""

    def __init__(self, skw):
        super().__init__()
        self._skw = skw
        self._fired = False

    def empty(self):
        if not self._fired:
            self._fired = True
            self._skw.cancel_commands()
        return super().empty()


class TestACancellationSurvivesTheQueueDrain(TimeBoxedTestCase):

    def test_a_cancellation_racing_the_drain_is_not_swallowed(self):
        skw = _make_skw()
        skw.subscribers['cmd_exec_q'] = _CancellingQueue(skw)

        started = time.monotonic()
        with patch.object(skw, WRITELINE) as writeline:
            with self.assertRaises(MomongaSkCommandCancelled):
                skw.exec_command(['SKJOIN', 'FE80::1'], 'EVENT 25', timeout=2)

        self.assertLess(time.monotonic() - started, 1)  # not the command's own timeout
        writeline.assert_not_called()


class TestOpenClearsTheCancellation(TimeBoxedTestCase):

    def test_a_cancelled_wrapper_takes_commands_again_after_open(self):
        skw = _make_skw()
        skw.cancel_commands()
        self.assertTrue(skw._cancelled)

        with patch.object(MomongaSkWrapper, '_clear_buf'), \
             patch.object(MomongaSkWrapper, '_exec_ropt', return_value=1), \
             patch.object(MomongaSkWrapper, 'detect_device'), \
             patch.object(MomongaSkWrapper, 'received_packet_publisher'), \
             patch('momonga.momonga_sk_wrapper.serial.Serial'):
            skw.open()
            skw.close()

        self.assertFalse(skw._cancelled)
        self.assertTrue(skw.subscribers['cmd_exec_q'].empty())  # the sentinel is gone


if __name__ == '__main__':
    unittest.main()
