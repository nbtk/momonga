"""
Unit tests for reopening a session manager that a dead receiver left behind.

Run:
  python -m unittest tests/test_session_reuse_unit.py -v
"""
import unittest
from unittest.mock import MagicMock

from momonga.momonga_exception import MomongaNeedToReopen
from momonga.momonga_session_manager import MomongaSessionManager
from tests._timebox import TimeBoxedTestCase


def _make_sm():
    sm = MomongaSessionManager('', '', '/dev/ttyUSB0', reset_dev=False)
    sm.skw = MagicMock()
    sm.skw.subscribers = {}
    sm.skw.skscan.return_value = MagicMock(mac_addr=b'\x00' * 8, channel='21', pan_id='1234')
    return sm


def _kill_the_receiver(sm):
    sm._pkt_sbsc_q.put(object())  # not a str; parse_sk_line will fail on it
    sm._receiver()


class TestReceiverExceptionIsNotCarriedOver(TimeBoxedTestCase):

    def test_a_dead_receiver_does_not_outlive_its_session(self):
        sm = _make_sm()
        _kill_the_receiver(sm)
        self.assertIsNotNone(sm.receiver_exception)

        sm.close()
        sm.open()

        self.assertIsNone(sm.receiver_exception)
        sm.raise_if_receiver_died()  # must not raise

    def test_a_receiver_that_died_is_reported_until_then(self):
        sm = _make_sm()
        _kill_the_receiver(sm)

        with self.assertRaises(MomongaNeedToReopen):
            sm.raise_if_receiver_died()


if __name__ == '__main__':
    unittest.main()
