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

from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_exception import MomongaNeedToReopen, MomongaSkCommandUnsupported
from momonga.momonga_sk_wrapper import MomongaSkWrapper

WRITELINE = '_MomongaSkWrapper__writeline'


def _make_skw():
    skw = object.__new__(MomongaSkWrapper)
    skw.subscribers = {'cmd_exec_q': queue.Queue()}
    skw._cmd_lock = threading.Lock()
    skw.device_strategy = BP35C2Strategy()
    skw.ser = MagicMock()
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
            # the second command must not reach the serial port yet.
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
        # the lock would be held indefinitely otherwise
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


if __name__ == '__main__':
    unittest.main()
