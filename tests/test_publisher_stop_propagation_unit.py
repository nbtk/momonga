"""
Unit tests for a dead publisher reaching everyone waiting on it.

Run:
  python -m unittest tests/test_publisher_stop_propagation_unit.py -v
"""
import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import serial

from momonga.momonga import Momonga
from momonga.momonga_exception import MomongaNeedToReopen
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper, PUBLISHER_STOPPED

WRITELINE = '_writeline'


def _make_skw():
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    return skw


def _make_sm(skw):
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
    sm.smart_meter_addr = 'FE80::1'
    sm.skw = skw
    skw.subscribers['pkt_sbsc_q'] = sm._pkt_sbsc_q
    return sm


class TestPublisherTellsItsSubscribers(unittest.TestCase):

    def test_every_subscriber_queue_is_woken(self):
        skw = _make_skw()
        sm = _make_sm(skw)
        skw._ser.readline.side_effect = serial.SerialException('device disconnected')

        skw.received_packet_publisher()

        self.assertIs(sm._pkt_sbsc_q.get_nowait(), PUBLISHER_STOPPED)
        self.assertIs(skw.subscribers['cmd_exec_q'].get_nowait(), PUBLISHER_STOPPED)


class TestReceiverStopsWithIt(unittest.TestCase):

    def test_receiver_records_the_stop_and_ends_the_session(self):
        skw = _make_skw()
        sm = _make_sm(skw)
        sm._pkt_sbsc_q.put(PUBLISHER_STOPPED)

        sm._receiver()

        self.assertIsInstance(sm.receiver_exception, MomongaNeedToReopen)
        self.assertFalse(sm.notif_q.empty())  # a waiting reader is released

    def test_a_blocked_notification_reader_is_released(self):
        skw = _make_skw()
        sm = _make_sm(skw)
        mo = Momonga(rbid='', pwd='', dev='')
        mo.is_open = True
        mo.session_manager = sm
        result = []

        receiver = threading.Thread(target=sm._receiver, daemon=True)
        reader = threading.Thread(target=lambda: result.append(mo.get_notification(timeout=None)),
                                  daemon=True)
        receiver.start()
        reader.start()
        time.sleep(0.05)
        self.assertTrue(receiver.is_alive())
        self.assertTrue(reader.is_alive())

        skw._ser.readline.side_effect = serial.SerialException('device disconnected')
        publisher = threading.Thread(target=skw.received_packet_publisher, daemon=True)
        publisher.start()

        publisher.join(5)
        receiver.join(5)
        reader.join(5)

        self.assertFalse(receiver.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertEqual(result, [None])


class TestCommandsFailAtOnce(unittest.TestCase):

    def test_a_waiting_command_does_not_sit_out_the_limit(self):
        skw = _make_skw()
        cmd_exec_q = skw.subscribers['cmd_exec_q']

        def fake_writeline(line, payload=None):
            skw.publisher_exception = serial.SerialException('device disconnected')
            cmd_exec_q.put(PUBLISHER_STOPPED)

        with patch.object(skw, WRITELINE, fake_writeline):
            started = time.monotonic()
            with self.assertRaises(MomongaNeedToReopen):
                skw.exec_command(['SKVER'], timeout=30)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0)

    def test_the_sentinel_alone_ends_the_command(self):
        skw = _make_skw()
        cmd_exec_q = skw.subscribers['cmd_exec_q']

        def fake_writeline(line, payload=None):
            cmd_exec_q.put(PUBLISHER_STOPPED)  # nothing recorded in publisher_exception

        with patch.object(skw, WRITELINE, fake_writeline):
            with self.assertRaises(MomongaNeedToReopen):
                skw.exec_command(['SKVER'], timeout=30)


class TestAPublisherThatLostThePortStaysQuiet(unittest.TestCase):
    """close() bounds the join, so a stuck publisher can outlive the session
    it belonged to and must not speak for the one that replaced it."""

    @staticmethod
    def _start_wedged(skw, release):
        def wedged():
            release.wait(30)
            raise serial.SerialException('the port went away')
        skw._ser.readline.side_effect = wedged
        th = threading.Thread(target=skw.received_packet_publisher, daemon=True)
        skw._publisher_th = th
        th.start()
        time.sleep(0.1)
        return th

    def _outlive_a_close(self):
        skw = _make_skw()
        release = threading.Event()
        th = self._start_wedged(skw, release)
        with patch('momonga.momonga_sk_wrapper._PUBLISHER_JOIN_LIMIT', 0.2):
            skw.close()
        self.assertTrue(th.is_alive())  # it outlived the close
        skw._ser = MagicMock()          # what the next open() does first
        skw._ser.closed = False
        skw.publisher_exception = None
        skw._publisher_th_breaker = False
        return skw, release, th

    def test_its_death_is_not_blamed_on_the_next_session(self):
        skw, release, th = self._outlive_a_close()
        release.set()
        th.join(5)
        self.assertIsNone(skw.publisher_exception)

    def test_it_does_not_queue_a_stop_for_the_next_session(self):
        skw, release, th = self._outlive_a_close()
        release.set()
        th.join(5)
        self.assertTrue(skw.subscribers['cmd_exec_q'].empty())

    def test_a_command_run_afterwards_is_not_refused(self):
        skw, release, th = self._outlive_a_close()
        release.set()
        th.join(5)
        skw._raise_if_publisher_died()  # must not raise

    def test_it_stops_reading_once_another_publisher_owns_the_port(self):
        skw = _make_skw()
        reads = []
        skw._ser.readline.side_effect = lambda: reads.append(1) or b'EVENT 21 FE80::1 0 00\r\n'
        th = threading.Thread(target=skw.received_packet_publisher, daemon=True)
        skw._publisher_th = th
        th.start()
        time.sleep(0.1)

        skw._ser = MagicMock()  # a new port takes over
        th.join(5)

        self.assertFalse(th.is_alive())  # the old one let go


class TestTheCurrentPublisherStillReports(unittest.TestCase):

    def test_a_publisher_that_still_owns_the_port_reports_its_death(self):
        skw = _make_skw()
        skw._ser.readline.side_effect = serial.SerialException('device disconnected')
        th = threading.Thread(target=skw.received_packet_publisher, daemon=True)
        skw._publisher_th = th
        th.start()
        th.join(5)

        self.assertIsInstance(skw.publisher_exception, serial.SerialException)
        self.assertIs(skw.subscribers['cmd_exec_q'].get_nowait(), PUBLISHER_STOPPED)


class _DyingQueue(queue.Queue):
    """Kills the publisher while the command is draining, so its news lands mid-drain."""

    def __init__(self, skw):
        super().__init__()
        self._skw = skw
        self._fired = False

    def empty(self):
        if not self._fired:
            self._fired = True
            self._skw.publisher_exception = serial.SerialException('device disconnected')
            self.put(PUBLISHER_STOPPED)
        return super().empty()


class TestADeadPublisherSurvivesTheQueueDrain(unittest.TestCase):

    def test_a_death_racing_the_drain_is_not_swallowed(self):
        skw = _make_skw()
        skw.subscribers['cmd_exec_q'] = _DyingQueue(skw)

        started = time.monotonic()
        with patch.object(skw, WRITELINE) as writeline:
            with self.assertRaises(MomongaNeedToReopen):
                skw.exec_command(['SKVER'], timeout=2)

        self.assertLess(time.monotonic() - started, 1)  # not the command's own timeout
        writeline.assert_not_called()


if __name__ == '__main__':
    unittest.main()
