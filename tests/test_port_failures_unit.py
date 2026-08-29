"""
A serial port that has gone, seen from outside the library.

The exception hierarchy is documented as MomongaError and its subclasses, and
pyserial's SerialException is not one of them. Nor is the FileNotFoundError or
PermissionError the OS gives back for a device path that is missing or not
readable. All three used to reach the caller unchanged from open(), from a
getter and from close(), so anyone handling them had to import pyserial and
guess which of the three to name.

They are MomongaIOError now, which is also an OSError - so code that already
caught OSError keeps working, and _request_with_recovery, which catches OSError,
still recovers from them. The original is kept as __cause__.

Run:
  python -m unittest tests/test_port_failures_unit.py -v
"""
import unittest
from unittest.mock import MagicMock, patch

import serial

import momonga
from momonga.momonga import Momonga
from momonga.momonga_device import BP35C2Strategy
from momonga.momonga_exception import MomongaError, MomongaIOError
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

# what a port that has gone gives back
FAULTS = (serial.SerialException('device disconnected'),
          FileNotFoundError(2, 'No such file or directory', '/dev/ttyUSB0'),
          PermissionError(13, 'Permission denied', '/dev/ttyUSB0'))


def _wrapper(**ser_kwargs):
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    ser = MagicMock()
    ser.closed = False
    for name, value in ser_kwargs.items():
        getattr(ser, name).side_effect = value
    skw._ser = ser
    return skw


class TestOpeningTheDeviceThatIsNotThere(TimeBoxedTestCase):

    def test_every_way_the_port_can_refuse_becomes_one_error(self):
        for fault in FAULTS:
            with self.subTest(fault=type(fault).__name__):
                with patch('momonga.momonga_sk_wrapper.serial.Serial',
                           side_effect=fault):
                    with self.assertRaises(MomongaIOError):
                        momonga.Momonga('', '', '/dev/ttyUSB0').open()

    def test_the_original_is_kept_as_the_cause(self):
        fault = FAULTS[0]
        with patch('momonga.momonga_sk_wrapper.serial.Serial', side_effect=fault):
            with self.assertRaises(MomongaIOError) as caught:
                momonga.Momonga('', '', '/dev/ttyUSB0').open()

        self.assertIs(caught.exception.__cause__, fault)

    def test_the_device_path_is_in_the_message(self):
        with patch('momonga.momonga_sk_wrapper.serial.Serial', side_effect=FAULTS[0]):
            with self.assertRaises(MomongaIOError) as caught:
                momonga.Momonga('', '', '/dev/ttyUSB0').open()

        self.assertIn('/dev/ttyUSB0', str(caught.exception))


class TestAPortThatDiesWhileInUse(TimeBoxedTestCase):

    def test_reading_a_line(self):
        skw = _wrapper(readline=FAULTS[0])

        with self.assertRaises(MomongaIOError):
            skw._readline(timeout=1)

    def test_writing_a_line(self):
        skw = _wrapper(write=FAULTS[0])

        with self.assertRaises(MomongaIOError):
            skw._writeline('SKVER')

    def test_clearing_the_buffer(self):
        skw = _wrapper(write=FAULTS[0])

        with self.assertRaises(MomongaIOError):
            skw._clear_buf()

    def test_the_ropt_exchange(self):
        skw = _wrapper(read=FAULTS[0])

        with self.assertRaises(MomongaIOError):
            skw._exec_ropt()

    def test_the_wopt_exchange(self):
        skw = _wrapper(write=FAULTS[0])

        with self.assertRaises(MomongaIOError):
            skw._exec_wopt(1)

    def test_closing_it(self):
        skw = _wrapper(close=FAULTS[0])

        with self.assertRaises(MomongaIOError):
            skw.close()

    def test_a_request_reports_a_momonga_error(self):
        skw = _wrapper(write=FAULTS[0])
        skw.device_strategy = BP35C2Strategy()
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.session_established = True
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = skw
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 1, 0.2, 0
        mo.xmit_timeout = 2

        with self.assertRaises(MomongaError):
            mo.get_instantaneous_power()


class TestWhereItSitsInTheHierarchy(TimeBoxedTestCase):

    def test_it_is_a_momonga_error(self):
        self.assertTrue(issubclass(MomongaIOError, MomongaError))

    def test_it_is_also_an_os_error(self):
        # so callers already catching OSError are not broken by the change
        self.assertTrue(issubclass(MomongaIOError, OSError))

    def test_the_recovery_loop_still_covers_it(self):
        # _request_with_recovery catches (MomongaNeedToReopen, OSError)
        self.assertTrue(issubclass(
            MomongaIOError, (momonga.MomongaNeedToReopen, OSError)))

    def test_it_is_exported(self):
        self.assertIs(momonga.MomongaIOError, MomongaIOError)

    def test_it_is_one_of_the_connection_failures(self):
        # so a caller waiting to reconnect names one thing, not four
        self.assertTrue(issubclass(MomongaIOError,
                                   momonga.MomongaConnectionFailure))

    def test_a_momonga_error_is_not_wrapped_again(self):
        # the guard that stops _port double wrapping what it already produced
        skw = _wrapper(readline=MomongaIOError('already ours'))

        with self.assertRaises(MomongaIOError) as caught:
            skw._readline(timeout=1)

        self.assertIsNone(caught.exception.__cause__)


if __name__ == '__main__':
    unittest.main()
