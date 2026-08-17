"""
Unit tests for the raw serial reads open() performs before the publisher starts.

Run:
  python -m unittest tests/test_raw_serial_timeout_unit.py -v
"""
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_exception import MomongaTimeoutError
from momonga.momonga_sk_wrapper import MomongaSkWrapper

ROPT = '_exec_ropt'
WOPT = '_exec_wopt'


def _make_skw():
    skw = object.__new__(MomongaSkWrapper)
    skw.dev = '/dev/ttyUSB0'
    skw.baudrate = 115200
    skw.ser = MagicMock()
    return skw


class TestSerialPortTimeout(unittest.TestCase):

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
        skw.ser.timeout = 300
        skw.ser.read.return_value = b''

        skw._clear_buf()

        self.assertEqual(skw.ser.timeout, 300)


class TestRawReadGuards(unittest.TestCase):

    def test_ropt_reports_a_silent_module(self):
        skw = _make_skw()
        skw.ser.read.return_value = b''  # what read() returns once the timeout expires

        with self.assertRaises(MomongaTimeoutError):
            skw._exec_ropt()

    def test_wopt_reports_a_silent_module(self):
        skw = _make_skw()
        skw.ser.read.return_value = b''

        with self.assertRaises(MomongaTimeoutError):
            skw._exec_wopt(1)


if __name__ == '__main__':
    unittest.main()
