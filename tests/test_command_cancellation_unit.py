"""
Unit tests for cutting a stuck SK command loose so close() cannot outlast it.

Run:
  python -m unittest tests/test_command_cancellation_unit.py -v
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_exception import MomongaNeedToReopen, MomongaSkCommandCancelled
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper

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


class TestAWaitingCommandIsCutLoose(unittest.TestCase):

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


class TestALockedOutCommandGivesUp(unittest.TestCase):

    def test_a_bounded_command_does_not_wait_out_the_holder(self):
        skw = _make_skw()
        skw._cmd_lock.acquire()  # another thread is mid-command
        try:
            started = time.monotonic()
            with self.assertRaises(MomongaSkCommandCancelled):
                skw.skterm(lock_timeout=0.2)
            self.assertLess(time.monotonic() - started, 5)  # not the command limit
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


class TestACancellationIsNotRetried(unittest.TestCase):

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


class TestCloseDoesNotWaitOutAStuckReceiver(unittest.TestCase):

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
             patch('momonga.momonga_session_manager._SKTERM_LOCK_LIMIT', 0.2):
            started = time.monotonic()
            sm.close()
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5)  # not the three command limits skjoin would take
        self.assertEqual(stuck, ['MomongaSkCommandCancelled'])
        self.assertFalse(sm._rejoin_lock.locked())

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


class TestOpenClearsTheCancellation(unittest.TestCase):

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
