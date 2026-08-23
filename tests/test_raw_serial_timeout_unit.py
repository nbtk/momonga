"""
Unit tests for the raw serial reads open() performs before the publisher starts.

Run:
  python -m unittest tests/test_raw_serial_timeout_unit.py -v
"""
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_exception import MomongaTimeoutError
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

ROPT = '_exec_ropt'
WOPT = '_exec_wopt'


def _make_skw():
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    return skw


class TestSerialPortTimeout(TimeBoxedTestCase):

    def test_open_gives_the_port_a_finite_timeout(self):
        # __clear_buf() restores whatever the port was created with
        skw = MomongaSkWrapper('/dev/ttyUSB0')

        with patch('momonga.momonga_sk_wrapper.serial.Serial') as serial_cls:
            with patch.object(skw, ROPT, return_value=1), \
                 patch.object(skw, '_clear_buf'), \
                 patch.object(skw, 'received_packet_publisher'), \
                 patch.object(skw, 'detect_device'):
                skw.open()
            skw.close()

        timeout = serial_cls.call_args.kwargs.get('timeout')
        self.assertIsNotNone(timeout)
        self.assertGreater(timeout, 0)

    def test_clear_buf_restores_the_finite_timeout(self):
        skw = _make_skw()
        skw._ser.timeout = 300
        skw._ser.read.return_value = b''

        skw._clear_buf()

        self.assertEqual(skw._ser.timeout, 300)


class TestALineReadBorrowsTheTimeoutAndGivesItBack(TimeBoxedTestCase):
    """_readline is the publisher's inner loop, called once a second forever.
    It sets the port's timeout for its own read and puts back what was there,
    because _clear_buf and every command read rely on the port's own figure.
    Only _clear_buf was covered, so both halves of this could be dropped with
    the suite green."""

    def _readline_with(self, port_timeout, asked_for):
        skw = _make_skw()
        skw._ser.timeout = port_timeout
        seen = []
        skw._ser.readline.side_effect = lambda: seen.append(skw._ser.timeout) or b'OK\r\n'

        line = skw._readline(timeout=asked_for)

        return skw, line, seen

    def test_the_read_happens_under_the_timeout_it_was_given(self):
        _skw, _line, seen = self._readline_with(300, 1)

        self.assertEqual(seen, [1])

    def test_the_port_is_left_as_it_was_found(self):
        skw, _line, _seen = self._readline_with(300, 1)

        self.assertEqual(skw._ser.timeout, 300)

    def test_it_is_put_back_even_when_nothing_arrives(self):
        skw = _make_skw()
        skw._ser.timeout = 300
        skw._ser.readline.return_value = b''

        self.assertEqual(skw._readline(timeout=1), '')
        self.assertEqual(skw._ser.timeout, 300)

    def test_asking_for_no_timeout_still_leaves_the_port_alone(self):
        skw, _line, seen = self._readline_with(300, None)

        self.assertEqual(seen, [None])
        self.assertEqual(skw._ser.timeout, 300)


class TestRawReadGuards(TimeBoxedTestCase):

    def test_ropt_reports_a_silent_module(self):
        skw = _make_skw()
        skw._ser.read.return_value = b''  # what read() returns once the timeout expires

        with self.assertRaises(MomongaTimeoutError):
            skw._exec_ropt()

    def test_wopt_reports_a_silent_module(self):
        skw = _make_skw()
        skw._ser.read.return_value = b''

        with self.assertRaises(MomongaTimeoutError):
            skw._exec_wopt(1)


if __name__ == '__main__':
    unittest.main()
