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
from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_exception import MomongaNeedToReopen
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper, PUBLISHER_STOPPED

WRITELINE = '_MomongaSkWrapper__writeline'


def _make_skw():
    skw = object.__new__(MomongaSkWrapper)
    skw.subscribers = {'cmd_exec_q': queue.Queue()}
    skw._cmd_lock = threading.Lock()
    skw.device_strategy = BP35C2Strategy()
    skw.ser = MagicMock()
    skw.publisher_th_breaker = False
    skw.publisher_exception = None
    return skw


def _make_sm(skw):
    sm = object.__new__(MomongaSessionManager)
    sm.notif_q = queue.Queue()
    sm.recv_q = queue.Queue()
    sm.pkt_sbsc_q = queue.Queue()
    sm.receiver_exception = None
    sm.smart_meter_addr = 'FE80::1'
    sm.on_meter_frame = None
    sm.skw = skw
    skw.subscribers['pkt_sbsc_q'] = sm.pkt_sbsc_q
    return sm


class TestPublisherTellsItsSubscribers(unittest.TestCase):

    def test_every_subscriber_queue_is_woken(self):
        skw = _make_skw()
        sm = _make_sm(skw)
        skw.ser.readline.side_effect = serial.SerialException('device disconnected')

        skw.received_packet_publisher()

        self.assertIs(sm.pkt_sbsc_q.get_nowait(), PUBLISHER_STOPPED)
        self.assertIs(skw.subscribers['cmd_exec_q'].get_nowait(), PUBLISHER_STOPPED)


class TestReceiverStopsWithIt(unittest.TestCase):

    def test_receiver_records_the_stop_and_ends_the_session(self):
        skw = _make_skw()
        sm = _make_sm(skw)
        sm.pkt_sbsc_q.put(PUBLISHER_STOPPED)

        sm.receiver()

        self.assertIsInstance(sm.receiver_exception, MomongaNeedToReopen)
        self.assertFalse(sm.notif_q.empty())  # a waiting reader is released

    def test_a_blocked_notification_reader_is_released(self):
        skw = _make_skw()
        sm = _make_sm(skw)
        mo = Momonga(rbid='', pwd='', dev='')
        mo.is_open = True
        mo.session_manager = sm
        result = []

        receiver = threading.Thread(target=sm.receiver, daemon=True)
        reader = threading.Thread(target=lambda: result.append(mo.get_notification(timeout=None)),
                                  daemon=True)
        receiver.start()
        reader.start()
        time.sleep(0.05)
        self.assertTrue(receiver.is_alive())
        self.assertTrue(reader.is_alive())

        skw.ser.readline.side_effect = serial.SerialException('device disconnected')
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


if __name__ == '__main__':
    unittest.main()
