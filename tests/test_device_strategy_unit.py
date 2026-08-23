"""
Unit tests for BP35C2Strategy and BP35A1Strategy.

Run:
  python -m unittest tests/test_device_strategy_unit.py -v
"""
import logging
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_device_enum import DeviceType
from momonga.momonga_device_strategy import BP35C2Strategy, BP35A1Strategy
from momonga.momonga_response import SkParsedEvent, SkParsedRxUdp
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

C2 = BP35C2Strategy()
A1 = BP35A1Strategy()


# ---------------------------------------------------------------------------
# device_type attribute
# ---------------------------------------------------------------------------

class TestDeviceType(TimeBoxedTestCase):

    def test_bp35c2_device_type(self):
        self.assertEqual(C2.device_type, DeviceType.BP35C2)

    def test_bp35a1_device_type(self):
        self.assertEqual(A1.device_type, DeviceType.BP35A1)


# ---------------------------------------------------------------------------
# parse_event
# ---------------------------------------------------------------------------

class TestBP35C2ParseEvent(TimeBoxedTestCase):

    def test_with_side_and_param(self):
        result = C2.parse_event(['EVENT', '21', 'FE80::1', '0', '01'])
        self.assertIsInstance(result, SkParsedEvent)
        self.assertEqual(result.num, 0x21)
        self.assertEqual(result.src_addr, 'FE80::1')
        self.assertEqual(result.side, 0)
        self.assertEqual(result.param, 1)

    def test_with_side_no_param(self):
        result = C2.parse_event(['EVENT', '29', 'FE80::1', '0'])
        self.assertIsInstance(result, SkParsedEvent)
        self.assertEqual(result.side, 0)
        self.assertIsNone(result.param)

    def test_no_side_no_param(self):
        result = C2.parse_event(['EVENT', '22', 'FE80::1'])
        self.assertIsInstance(result, SkParsedEvent)
        self.assertIsNone(result.side)
        self.assertIsNone(result.param)

    def test_too_few_parts_returns_none(self):
        self.assertIsNone(C2.parse_event(['EVENT', '21']))

    def test_invalid_hex_num_raises(self):
        with self.assertRaises(ValueError):
            C2.parse_event(['EVENT', 'ZZ', 'FE80::1'])


class TestBP35A1ParseEvent(TimeBoxedTestCase):

    def test_with_param_no_side(self):
        result = A1.parse_event(['EVENT', '21', 'FE80::1', '01'])
        self.assertIsInstance(result, SkParsedEvent)
        self.assertEqual(result.num, 0x21)
        self.assertIsNone(result.side)
        self.assertEqual(result.param, 1)

    def test_no_param_no_side(self):
        result = A1.parse_event(['EVENT', '29', 'FE80::1'])
        self.assertIsInstance(result, SkParsedEvent)
        self.assertIsNone(result.side)
        self.assertIsNone(result.param)

    def test_too_few_parts_returns_none(self):
        self.assertIsNone(A1.parse_event(['EVENT', '21']))


# ---------------------------------------------------------------------------
# parse_erxudp
# ---------------------------------------------------------------------------

_C2_PARTS = ['ERXUDP', 'FE80::1', 'FE80::2', '0E1A', '0E1A',
             'AABBCCDDEEFF', '50', '00', '00', '0002', '1081']

_A1_PARTS = ['ERXUDP', 'FE80::1', 'FE80::2', '0E1A', '0E1A',
             'AABBCCDDEEFF', '00', '0002', '1081']


class TestBP35C2ParseErxudp(TimeBoxedTestCase):

    def test_all_fields(self):
        result = C2.parse_erxudp(_C2_PARTS)
        self.assertIsInstance(result, SkParsedRxUdp)
        self.assertEqual(result.src_addr, 'FE80::1')
        self.assertEqual(result.dst_addr, 'FE80::2')
        self.assertEqual(result.src_port, 0x0E1A)
        self.assertEqual(result.dst_port, 0x0E1A)
        self.assertEqual(result.src_mac, bytes.fromhex('AABBCCDDEEFF'))
        self.assertEqual(result.lqi, 0x50)
        self.assertAlmostEqual(result.rssi, 0.275 * 0x50 - 104.27, places=5)
        self.assertEqual(result.sec, 0)
        self.assertEqual(result.side, 0)
        self.assertEqual(result.data, bytes.fromhex('1081'))

    def test_too_few_parts_returns_none(self):
        self.assertIsNone(C2.parse_erxudp(_C2_PARTS[:10]))

    def test_invalid_mac_raises(self):
        parts = list(_C2_PARTS)
        parts[5] = 'ZZZZZZZZZZZZ'
        with self.assertRaises(ValueError):
            C2.parse_erxudp(parts)

    def test_invalid_data_raises(self):
        parts = list(_C2_PARTS)
        parts[10] = 'ZZ'
        with self.assertRaises(ValueError):
            C2.parse_erxudp(parts)

    def test_invalid_port_raises(self):
        parts = list(_C2_PARTS)
        parts[3] = 'ZZZZ'
        with self.assertRaises(ValueError):
            C2.parse_erxudp(parts)


class TestBP35A1ParseErxudp(TimeBoxedTestCase):

    def test_all_fields(self):
        result = A1.parse_erxudp(_A1_PARTS)
        self.assertIsInstance(result, SkParsedRxUdp)
        self.assertIsNone(result.lqi)
        self.assertIsNone(result.rssi)
        self.assertIsNone(result.side)
        self.assertEqual(result.sec, 0)
        self.assertEqual(result.data, bytes.fromhex('1081'))

    def test_too_few_parts_returns_none(self):
        self.assertIsNone(A1.parse_erxudp(_A1_PARTS[:8]))

    def test_invalid_data_raises(self):
        parts = list(_A1_PARTS)
        parts[8] = 'ZZ'
        with self.assertRaises(ValueError):
            A1.parse_erxudp(parts)


# ---------------------------------------------------------------------------
# skscan_command
# ---------------------------------------------------------------------------

class TestSkscanCommand(TimeBoxedTestCase):

    def test_bp35c2_includes_side_param(self):
        cmd = C2.skscan_command(6)
        self.assertEqual(cmd, ['SKSCAN', '2', 'FFFFFFFF', '6', '0'])

    def test_bp35a1_no_side_param(self):
        cmd = A1.skscan_command(6)
        self.assertEqual(cmd, ['SKSCAN', '2', 'FFFFFFFF', '6'])

    def test_bp35c2_duration_changes(self):
        self.assertEqual(C2.skscan_command(7)[3], '7')

    def test_bp35a1_duration_changes(self):
        self.assertEqual(A1.skscan_command(8)[3], '8')


# ---------------------------------------------------------------------------
# sksendto_args
# ---------------------------------------------------------------------------

class TestSksendtoArgs(TimeBoxedTestCase):

    def test_bp35c2_includes_side(self):
        args = C2.sksendto_args(1, 'FE80::1', 0x0E1A, 2, 0, 10)
        self.assertEqual(args, ['SKSENDTO', '1', 'FE80::1', '0E1A', '2', '0', '000A'])

    def test_bp35a1_no_side(self):
        args = A1.sksendto_args(1, 'FE80::1', 0x0E1A, 2, 0, 10)
        self.assertEqual(args, ['SKSENDTO', '1', 'FE80::1', '0E1A', '2', '000A'])

    def test_bp35c2_length_hex_format(self):
        args = C2.sksendto_args(1, 'FE80::1', 0x0E1A, 2, 0, 256)
        self.assertEqual(args[-1], '0100')

    def test_bp35a1_length_hex_format(self):
        args = A1.sksendto_args(1, 'FE80::1', 0x0E1A, 2, 0, 256)
        self.assertEqual(args[-1], '0100')


# ---------------------------------------------------------------------------
# decode_scan_side
# ---------------------------------------------------------------------------

class TestDecodeScanSide(TimeBoxedTestCase):

    def test_bp35c2_parses_side_from_extract(self):
        result = C2.decode_scan_side(lambda key: 'Side:0')
        self.assertEqual(result, 0)

    def test_bp35c2_side_1(self):
        result = C2.decode_scan_side(lambda key: 'Side:1')
        self.assertEqual(result, 1)

    def test_bp35a1_always_returns_none(self):
        self.assertIsNone(A1.decode_scan_side(lambda key: 'Side:0'))


# ---------------------------------------------------------------------------
# detect_device: which of the two the wrapper actually picks
# ---------------------------------------------------------------------------

class TestDetectDevice(TimeBoxedTestCase):
    """Everything above tests the two strategies. This tests the SKINFO reading
    that chooses between them - a wrong choice here sends every later line to
    the wrong parser, and mutation testing found both branches removable."""

    def _detect(self, side):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        with patch.object(MomongaSkWrapper, 'skinfo',
                          return_value=MagicMock(side=side)):
            skw.detect_device()
        return skw.device_strategy.device_type

    def test_the_sentinel_side_means_bp35a1(self):
        self.assertEqual(self._detect(0xFFFE), DeviceType.BP35A1)

    def test_a_real_side_means_bp35c2(self):
        for side in (0, 1):
            with self.subTest(side=side):
                self.assertEqual(self._detect(side), DeviceType.BP35C2)

    def test_a_side_it_does_not_know_falls_back_to_bp35c2(self):
        self.assertEqual(self._detect(2), DeviceType.BP35C2)

    def _warnings(self, side):
        records = []

        class Cap(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.WARNING:
                    records.append(record.getMessage())

        lg = logging.getLogger('momonga.momonga_sk_wrapper')
        cap = Cap()
        lg.addHandler(cap)
        try:
            self._detect(side)
        finally:
            lg.removeHandler(cap)
        return records

    def test_a_side_it_knows_is_recognised_rather_than_guessed(self):
        # both branches end at BP35C2 for a known side, so the strategy alone
        # cannot tell recognising it from falling back to it
        for side in (0, 1):
            with self.subTest(side=side):
                self.assertEqual(self._warnings(side), [])

    def test_a_side_it_does_not_know_says_so(self):
        self.assertTrue(any('UNKNOWN' in m for m in self._warnings(2)))

    def test_the_sentinel_is_not_read_as_an_unknown_side(self):
        # 0xFFFE is >= 2, so the fallback would swallow it if the sentinel
        # branch were gone - and BP35A1 lines would then go to the C2 parser
        self.assertNotEqual(self._detect(0xFFFE), self._detect(2))


if __name__ == '__main__':
    unittest.main()
