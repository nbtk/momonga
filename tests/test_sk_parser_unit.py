"""
Unit tests for parse_sk_line() and the typed event dataclasses.

Run:
  python -m unittest tests/test_sk_parser_unit.py -v
"""
import unittest

from momonga.momonga_device_strategy import BP35C2Strategy, BP35A1Strategy
from momonga.momonga_response import (
    SkEventNum, SkParsedEvent, SkParsedRxUdp, parse_sk_line,
)

C2 = BP35C2Strategy()
A1 = BP35A1Strategy()


def _c2_erxudp(data_hex: str, src_addr: str = 'FE80::1') -> str:
    """Build a minimal BP35C2 ERXUDP line."""
    data_len = '%04X' % (len(data_hex) // 2)
    return ('ERXUDP %s FE80::2 0E1A 0E1A AABBCCDDEEFF '
            '50 00 00 %s %s' % (src_addr, data_len, data_hex))


def _a1_erxudp(data_hex: str) -> str:
    """Build a minimal BP35A1 ERXUDP line."""
    data_len = '%04X' % (len(data_hex) // 2)
    return ('ERXUDP FE80::1 FE80::2 0E1A 0E1A AABBCCDDEEFF '
            '00 %s %s' % (data_len, data_hex))


# ---------------------------------------------------------------------------
# EVENT parsing
# ---------------------------------------------------------------------------

class TestParseSkLineEvent(unittest.TestCase):

    def test_bp35c2_tx_done_with_side_and_param(self):
        result = parse_sk_line('EVENT 21 FE80::1 0 01', C2)
        self.assertIsInstance(result, SkParsedEvent)
        self.assertEqual(result.num, SkEventNum.tx_done)
        self.assertEqual(result.src_addr, 'FE80::1')
        self.assertEqual(result.side, 0)
        self.assertEqual(result.param, 1)

    def test_bp35c2_session_lifetime_no_param(self):
        result = parse_sk_line('EVENT 29 FE80::1 0', C2)
        self.assertIsInstance(result, SkParsedEvent)
        self.assertEqual(result.num, SkEventNum.session_lifetime)
        self.assertIsNone(result.param)

    def test_bp35a1_tx_done_no_side(self):
        result = parse_sk_line('EVENT 21 FE80::1 01', A1)
        self.assertIsInstance(result, SkParsedEvent)
        self.assertEqual(result.num, SkEventNum.tx_done)
        self.assertIsNone(result.side)
        self.assertEqual(result.param, 1)

    def test_bp35a1_session_lifetime_no_side_no_param(self):
        result = parse_sk_line('EVENT 29 FE80::1', A1)
        self.assertIsInstance(result, SkParsedEvent)
        self.assertIsNone(result.side)
        self.assertIsNone(result.param)

    def test_all_named_event_nums_parse(self):
        for ev in SkEventNum:
            line = 'EVENT %02X FE80::1 0' % ev
            result = parse_sk_line(line, C2)
            self.assertIsInstance(result, SkParsedEvent)
            self.assertEqual(result.num, ev)

    def test_event_too_few_parts_returns_none(self):
        self.assertIsNone(parse_sk_line('EVENT 21', C2))

    def test_event_invalid_hex_num_returns_none(self):
        self.assertIsNone(parse_sk_line('EVENT ZZ FE80::1 0', C2))


# ---------------------------------------------------------------------------
# ERXUDP parsing
# ---------------------------------------------------------------------------

class TestParseSkLineErxudp(unittest.TestCase):

    def test_bp35c2_fields(self):
        result = parse_sk_line(_c2_erxudp('1081'), C2)
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
        self.assertIsInstance(result.rssi, float)

    def test_bp35a1_fields(self):
        result = parse_sk_line(_a1_erxudp('1081'), A1)
        self.assertIsInstance(result, SkParsedRxUdp)
        self.assertIsNone(result.lqi)
        self.assertIsNone(result.rssi)
        self.assertIsNone(result.side)
        self.assertEqual(result.data, bytes.fromhex('1081'))

    def test_bp35c2_data_roundtrip(self):
        payload = '1081000102880105FF017301E70400000000'
        result = parse_sk_line(_c2_erxudp(payload), C2)
        self.assertIsInstance(result, SkParsedRxUdp)
        self.assertEqual(result.data, bytes.fromhex(payload))

    def test_bp35c2_too_few_parts_returns_none(self):
        # 10 parts only (needs 11)
        self.assertIsNone(parse_sk_line('ERXUDP x x x x x x x x x x', C2))

    def test_bp35a1_too_few_parts_returns_none(self):
        # 8 parts only (needs 9)
        self.assertIsNone(parse_sk_line('ERXUDP x x x x x x x x', A1))

    def test_invalid_hex_mac_returns_none(self):
        self.assertIsNone(parse_sk_line(
            'ERXUDP FE80::1 FE80::2 0E1A 0E1A ZZZZZZZZZZZZ 50 00 00 0001 10', C2))

    def test_invalid_hex_data_returns_none(self):
        self.assertIsNone(parse_sk_line(
            'ERXUDP FE80::1 FE80::2 0E1A 0E1A AABBCCDDEEFF 50 00 00 0001 ZZ', C2))

    def test_invalid_port_returns_none(self):
        self.assertIsNone(parse_sk_line(
            'ERXUDP FE80::1 FE80::2 ZZZZ 0E1A AABBCCDDEEFF 50 00 00 0001 10', C2))


# ---------------------------------------------------------------------------
# Non-EVENT / non-ERXUDP lines
# ---------------------------------------------------------------------------

class TestParseSkLineOther(unittest.TestCase):

    def test_ok_returns_none(self):
        self.assertIsNone(parse_sk_line('OK', C2))

    def test_fail_returns_none(self):
        self.assertIsNone(parse_sk_line('FAIL ER10', C2))

    def test_epandesc_line_returns_none(self):
        self.assertIsNone(parse_sk_line('  Channel:3A', C2))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_sk_line('', C2))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(parse_sk_line('   ', C2))


if __name__ == '__main__':
    unittest.main()
