"""
Why the background thread stopped, reachable from the exception.

A publisher or receiver that dies records what killed it, and the
MomongaNeedToReopen raised afterwards put that in its message and nowhere
else - so the reason a dongle was pulled could be read by a person and not by
a program. It is the __cause__ now, which is where an unplugged dongle is
distinguishable from a module that stopped answering.

Run:
  python -m unittest tests/test_cause_chaining_unit.py -v
"""
import unittest
from unittest.mock import MagicMock

import serial

import momonga
from momonga.momonga_exception import MomongaIOError, MomongaNeedToReopen
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase


class TestAPublisherThatDiedSaysWhy(TimeBoxedTestCase):

    @staticmethod
    def _wrapper_with(cause):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw.publisher_exception = cause
        return skw

    def test_the_cause_is_the_recorded_exception_itself(self):
        cause = MomongaIOError('could not read from /dev/ttyUSB0')

        with self.assertRaises(MomongaNeedToReopen) as caught:
            self._wrapper_with(cause)._raise_if_publisher_died()

        self.assertIs(caught.exception.__cause__, cause)

    def test_an_unplugged_dongle_stays_recognisable(self):
        # the whole point: a program can tell this from a meter going quiet
        cause = MomongaIOError('device disconnected')

        with self.assertRaises(MomongaNeedToReopen) as caught:
            self._wrapper_with(cause)._raise_if_publisher_died()

        self.assertIsInstance(caught.exception.__cause__, MomongaIOError)

    def test_something_that_is_not_a_momonga_error_is_carried_too(self):
        cause = serial.SerialException('device disconnected')

        with self.assertRaises(MomongaNeedToReopen) as caught:
            self._wrapper_with(cause)._raise_if_publisher_died()

        self.assertIs(caught.exception.__cause__, cause)

    def test_a_healthy_publisher_raises_nothing(self):
        self._wrapper_with(None)._raise_if_publisher_died()  # must not raise


class TestAReceiverThatDiedSaysWhy(TimeBoxedTestCase):

    @staticmethod
    def _manager_with(cause):
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.receiver_exception = cause
        return sm

    def test_the_cause_is_the_recorded_exception_itself(self):
        cause = RuntimeError('receiver died')

        with self.assertRaises(MomongaNeedToReopen) as caught:
            self._manager_with(cause).raise_if_receiver_died()

        self.assertIs(caught.exception.__cause__, cause)

    def test_a_healthy_receiver_raises_nothing(self):
        self._manager_with(None).raise_if_receiver_died()  # must not raise


class TestTheMessageStillCarriesIt(TimeBoxedTestCase):
    """Chaining is for programs; the message is what ends up in a log line, so
    it keeps naming the cause too."""

    def test_the_publisher_message_names_the_type_and_text(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw.publisher_exception = MomongaIOError('device disconnected')

        with self.assertRaises(MomongaNeedToReopen) as caught:
            skw._raise_if_publisher_died()

        self.assertIn('MomongaIOError', str(caught.exception))
        self.assertIn('device disconnected', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
