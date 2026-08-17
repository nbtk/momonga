"""
Unit tests for MomongaSkWrapper.exec_command() serialization and command limit.

Run:
  python -m unittest tests/test_sk_wrapper_unit.py -v
"""
import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import serial

from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_exception import MomongaNeedToReopen, MomongaSkCommandUnsupported
from momonga.momonga_sk_wrapper import MomongaSkWrapper

WRITELINE = '_writeline'


def _make_skw():
    skw = object.__new__(MomongaSkWrapper)
    skw.subscribers = {'cmd_exec_q': queue.Queue()}
    skw._cmd_lock = threading.Lock()
    skw.device_strategy = BP35C2Strategy()
    skw.ser = MagicMock()
    skw.publisher_th_breaker = False
    skw.publisher_exception = None
    return skw


class TestExecCommandSerialization(unittest.TestCase):

    def test_second_caller_waits_for_the_first(self):
        skw = _make_skw()
        cmd_exec_q = skw.subscribers['cmd_exec_q']
        first_inside = threading.Event()
        release_first = threading.Event()
        written = []

        def fake_writeline(line, payload=None):
            written.append(line)
            if line == 'FIRST':
                first_inside.set()
                release_first.wait(5)
            cmd_exec_q.put('OK')

        with patch.object(skw, WRITELINE, fake_writeline):
            first_th = threading.Thread(target=skw.exec_command, args=(['FIRST'],))
            first_th.start()
            self.assertTrue(first_inside.wait(5))

            second_th = threading.Thread(target=skw.exec_command, args=(['SECOND'],))
            second_th.start()
            time.sleep(0.05)
            self.assertEqual(written, ['FIRST'])

            release_first.set()
            first_th.join(5)
            second_th.join(5)

        self.assertEqual(written, ['FIRST', 'SECOND'])
        self.assertFalse(first_th.is_alive())
        self.assertFalse(second_th.is_alive())

    def test_each_caller_gets_its_own_response(self):
        skw = _make_skw()
        cmd_exec_q = skw.subscribers['cmd_exec_q']
        results = {}

        def fake_writeline(line, payload=None):
            cmd_exec_q.put('OK %s' % line)

        def run(name):
            with patch.object(skw, WRITELINE, fake_writeline):
                results[name] = skw.exec_command([name], 'OK')

        threads = [threading.Thread(target=run, args=('CMD%d' % i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

        self.assertEqual(len(results), 8)
        for name, res in results.items():
            self.assertEqual(res, ['OK %s' % name])

    def test_lock_is_released_after_a_failure(self):
        skw = _make_skw()
        cmd_exec_q = skw.subscribers['cmd_exec_q']

        def fake_writeline(line, payload=None):
            cmd_exec_q.put('FAIL ER04')

        with patch.object(skw, WRITELINE, fake_writeline):
            with self.assertRaises(MomongaSkCommandUnsupported):
                skw.exec_command(['SKVER'])

        self.assertFalse(skw._cmd_lock.locked())


class TestExecCommandLimit(unittest.TestCase):

    def test_silent_module_raises_need_to_reopen(self):
        skw = _make_skw()

        with patch.object(skw, WRITELINE, lambda line, payload=None: None):
            with self.assertRaises(MomongaNeedToReopen):
                skw.exec_command(['SKVER'], timeout=0.05)

    def test_lock_is_released_after_the_limit(self):
        skw = _make_skw()

        with patch.object(skw, WRITELINE, lambda line, payload=None: None):
            with self.assertRaises(MomongaNeedToReopen):
                skw.exec_command(['SKVER'], timeout=0.05)

        self.assertFalse(skw._cmd_lock.locked())

    def test_chatter_does_not_extend_the_limit(self):
        skw = _make_skw()
        cmd_exec_q = skw.subscribers['cmd_exec_q']
        stop = threading.Event()

        def noise():
            while not stop.is_set():
                cmd_exec_q.put('EVENT 02 FE80::1 0')
                time.sleep(0.002)

        noise_th = threading.Thread(target=noise, daemon=True)
        with patch.object(skw, WRITELINE, lambda line, payload=None: noise_th.start()):
            started = time.monotonic()
            with self.assertRaises(MomongaNeedToReopen):
                skw.exec_command(['SKVER'], timeout=0.2)
            elapsed = time.monotonic() - started
        stop.set()
        noise_th.join(5)

        self.assertLess(elapsed, 2.0)

    def test_a_response_within_the_limit_succeeds(self):
        skw = _make_skw()
        cmd_exec_q = skw.subscribers['cmd_exec_q']

        def fake_writeline(line, payload=None):
            cmd_exec_q.put('EVENT 21 FE80::1 0 00')
            cmd_exec_q.put('OK')

        with patch.object(skw, WRITELINE, fake_writeline):
            res = skw.exec_command(['SKSENDTO'], timeout=5)

        self.assertEqual(res, ['EVENT 21 FE80::1 0 00', 'OK'])


class TestPublisherSurvival(unittest.TestCase):

    def test_non_utf8_bytes_do_not_kill_the_publisher(self):
        # an ERXUDP payload in binary mode (WOPT 00) reaching decode()
        skw = _make_skw()
        lines = [b'ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D1290 1 0 0004 \x10\x81\x00\x01\r\n',
                 b'OK\r\n']

        def readline():
            if lines:
                return lines.pop(0)
            skw.publisher_th_breaker = True
            return b''

        skw.ser.readline.side_effect = readline
        skw.received_packet_publisher()

        self.assertIsNone(skw.publisher_exception)
        self.assertEqual(skw.subscribers['cmd_exec_q'].qsize(), 2)

    def test_serial_failure_is_recorded_instead_of_vanishing(self):
        skw = _make_skw()
        skw.ser.readline.side_effect = serial.SerialException('device disconnected')

        skw.received_packet_publisher()

        self.assertIsInstance(skw.publisher_exception, serial.SerialException)


class TestPublisherDeathIsReported(unittest.TestCase):

    def test_command_fails_immediately_when_publisher_is_dead(self):
        skw = _make_skw()
        skw.publisher_exception = serial.SerialException('device disconnected')
        written = []

        with patch.object(skw, WRITELINE, lambda line, payload=None: written.append(line)):
            with self.assertRaises(MomongaNeedToReopen):
                skw.exec_command(['SKVER'])

        self.assertEqual(written, [])  # must not wait out the limit first

    def test_publisher_death_during_a_command_is_reported(self):
        skw = _make_skw()

        def fake_writeline(line, payload=None):
            skw.publisher_exception = serial.SerialException('device disconnected')

        with patch.object(skw, WRITELINE, fake_writeline):
            with self.assertRaises(MomongaNeedToReopen) as ctx:
                skw.exec_command(['SKVER'], timeout=0.05)

        self.assertIn('publisher', str(ctx.exception))

    def test_lock_is_released_when_publisher_is_dead(self):
        skw = _make_skw()
        skw.publisher_exception = serial.SerialException('device disconnected')

        with patch.object(skw, WRITELINE, lambda line, payload=None: None):
            with self.assertRaises(MomongaNeedToReopen):
                skw.exec_command(['SKVER'])

        self.assertFalse(skw._cmd_lock.locked())


if __name__ == '__main__':
    unittest.main()
