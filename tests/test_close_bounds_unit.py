"""
Unit tests for close() finishing even when the parts it waits on do not.

Run:
  python -m unittest tests/test_close_bounds_unit.py -v
"""
import logging
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase


class _CaptureLogs(logging.Handler):

    def __init__(self, name):
        super().__init__()
        self.name_ = name
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        self._logger = logging.getLogger(self.name_)
        self._level = self._logger.level
        self._logger.addHandler(self)
        self._logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self)
        self._logger.setLevel(self._level)

    def at(self, level):
        return [r.getMessage() for r in self.records if r.levelname == level]


def _stuck_publisher(skw):
    skw._ser.readline.side_effect = lambda: (time.sleep(60), b'')[1]
    skw._publisher_th = threading.Thread(target=skw.received_packet_publisher, daemon=True)
    skw._publisher_th.start()
    time.sleep(0.1)


class TestTheWrapperCloseIsBounded(TimeBoxedTestCase):

    def _make_skw(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.closed = False
        return skw

    def test_a_stuck_serial_read_does_not_hold_close(self):
        skw = self._make_skw()
        _stuck_publisher(skw)

        started = time.monotonic()
        with patch('momonga.momonga_sk_wrapper._PUBLISHER_JOIN_LIMIT', 0.2):
            skw.close()
        self.assertLess(time.monotonic() - started, 5)  # not the stuck read

    def test_a_stuck_publisher_is_reported(self):
        skw = self._make_skw()
        _stuck_publisher(skw)

        with _CaptureLogs('momonga.momonga_sk_wrapper') as logs:
            with patch('momonga.momonga_sk_wrapper._PUBLISHER_JOIN_LIMIT', 0.2):
                skw.close()
        self.assertEqual(len(logs.at('WARNING')), 1)

    def test_the_port_is_closed_even_when_the_publisher_will_not_stop(self):
        skw = self._make_skw()
        _stuck_publisher(skw)

        with patch('momonga.momonga_sk_wrapper._PUBLISHER_JOIN_LIMIT', 0.2):
            skw.close()
        skw._ser.close.assert_called_once()

    def test_an_ordinary_close_reports_nothing(self):
        skw = self._make_skw()
        skw._ser.readline.return_value = b''
        skw._publisher_th = threading.Thread(target=skw.received_packet_publisher, daemon=True)
        skw._publisher_th.start()
        time.sleep(0.1)

        with _CaptureLogs('momonga.momonga_sk_wrapper') as logs:
            skw.close()
        self.assertEqual(logs.at('WARNING'), [])
        skw._ser.close.assert_called_once()


class TestAModuleIgnoringSktermDoesNotHoldClose(TimeBoxedTestCase):
    """_SKTERM_LIMIT used to bound only the wait for the command lock. The wait
    for the module's own EVENT 27 ran on the SK command limit, so a module that
    simply never answers held close() for five minutes."""

    def _make_sm(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._ser.closed = False
        skw._writeline = lambda line, payload=None: None   # SKTERM goes unanswered
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        return sm

    def test_close_gives_up_on_the_terminate_and_finishes(self):
        sm = self._make_sm()
        started = time.monotonic()
        with patch('momonga.momonga_session_manager._SKTERM_LIMIT', 0.3):
            sm.close()
        self.assertLess(time.monotonic() - started, 5)  # not the sk command limit

    def test_the_limit_is_what_decides_how_long(self):
        sm = self._make_sm()
        started = time.monotonic()
        with patch('momonga.momonga_session_manager._SKTERM_LIMIT', 1.0):
            sm.close()
        self.assertGreaterEqual(time.monotonic() - started, 0.9)

    def test_an_answered_terminate_is_still_quick(self):
        sm = self._make_sm()
        sm.skw._writeline = lambda line, payload=None: (
            sm.skw.subscribers['cmd_exec_q'].put('EVENT 27 FE80::1 0'))
        started = time.monotonic()
        sm.close()
        self.assertLess(time.monotonic() - started, 1)


class TestARejoinLockCloseIsNotAnError(TimeBoxedTestCase):

    def _make_sm(self):
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.skw = MagicMock()
        sm.smart_meter_addr = 'FE80::1'
        return sm

    def test_a_lock_held_by_someone_else_is_a_warning_not_an_error(self):
        sm = self._make_sm()
        sm._rejoin_lock.acquire()  # a rejoin is under way and will not finish
        try:
            with _CaptureLogs('momonga.momonga_session_manager') as logs:
                with patch('momonga.momonga_session_manager._REJOIN_LOCK_LIMIT', 0.2):
                    sm.close()
            self.assertEqual(logs.at('ERROR'), [])
            self.assertIn('Failed to acquire "_rejoin_lock".', logs.at('WARNING'))
        finally:
            sm._rejoin_lock.release()

    def test_an_ordinary_close_reports_no_error(self):
        sm = self._make_sm()
        with _CaptureLogs('momonga.momonga_session_manager') as logs:
            sm.close()
        self.assertEqual(logs.at('ERROR'), [])


if __name__ == '__main__':
    unittest.main()


class TestCloseCanBeCalledOnAClosedSession(TimeBoxedTestCase):
    """What close()'s docstring promises: calling it again is not an error."""

    def test_a_session_that_was_never_opened_can_be_closed(self):
        mo = Momonga('id', 'pw', '/dev/ttyUSB0')
        mo.close()

    def test_closing_twice_raises_nothing(self):
        mo = Momonga('id', 'pw', '/dev/ttyUSB0')
        mo.close()
        mo.close()

    def test_an_established_session_can_be_closed_twice(self):
        sm = MomongaSessionManager('id', 'pw', '/dev/ttyUSB0')
        sm.session_established = True
        sm.skw = MagicMock()
        sm.close()
        self.assertFalse(sm.session_established)
        self.assertEqual(sm.skw.skterm.call_count, 1)
        sm.close()
        self.assertEqual(sm.skw.skterm.call_count, 1)  # not sent to a closed session
