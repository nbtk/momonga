"""
A meter that declares a shorter PDC than the property needs.

The release already handles a short EDT on the notification path by keeping
the raw bytes and warning, and on request_to_get by raising - but both look
for the parser to raise, and most parsers did not. int.from_bytes of a short
slice is a number: three bytes of a four-byte cumulative energy came back 256
times small, and a truncated historical series came back padded with readings
of zero, through every public path, with no exception and no warning.

Truncating a good EDT one byte at a time is what found it, so that is what is
kept here.

Run:
  python -m unittest tests/test_short_edt_unit.py -v
"""
import datetime
import logging
import queue
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_echonet_data import EchonetDataParser as Parser
from momonga.momonga_echonet_enum import (CONTROLLER_EOJ, ECHONET_LITE_EHD,
                                          ECHONET_LITE_PORT, EchonetPropertyCode,
                                          SMART_METER_EOJ)
from momonga.momonga_exception import MomongaResponseNotExpected
from momonga.momonga_response import SkParsedRxUdp
from tests._timebox import TimeBoxedTestCase

UNIT, COEFFICIENT = 0.1, 1
EPC = EchonetPropertyCode

# a well-formed EDT for each parser, and the arguments it takes
WELL_FORMED = {
    'operation_status': (Parser.parse_operation_status, b'\x30', ()),
    'installation_location': (Parser.parse_installation_location, b'\x08', ()),
    'standard_version': (Parser.parse_standard_version_information, b'\x00\x00\x46\x00', ()),
    'fault_status': (Parser.parse_fault_status, b'\x42', ()),
    'manufacturer_code': (Parser.parse_manufacturer_code, b'\x00\x00\x16', ()),
    'serial_number': (Parser.parse_serial_number, b'S19Z011823  ', ()),
    'route_b_id': (Parser.parse_route_b_id, b'\x00\x00\x00\x16' + bytes(range(12)), ()),
    'current_time': (Parser.parse_current_time_setting, b'\x0c\x22', ()),
    'current_date': (Parser.parse_current_date_setting, b'\x07\xea\x08\x17', ()),
    'property_map_listed': (Parser.parse_property_map, b'\x02\x80\xd3', ()),
    'property_map_bitmap': (Parser.parse_property_map, bytes([16]) + bytes([0x01] * 16), ()),
    'one_minute': (Parser.parse_one_minute_measured_cumulative_energy,
                   b'\x07\xea\x08\x17\x0c\x22\x00'
                   + (100).to_bytes(4, 'big') + (7).to_bytes(4, 'big'), (UNIT, COEFFICIENT)),
    'coefficient': (Parser.parse_coefficient_for_cumulative_energy, b'\x00\x00\x00\x01', ()),
    'digits': (Parser.parse_number_of_effective_digits_for_cumulative_energy, b'\x06', ()),
    'cumulative_energy': (Parser.parse_measured_cumulative_energy,
                          (1000).to_bytes(4, 'big'), (UNIT, COEFFICIENT)),
    'unit': (Parser.parse_unit_for_cumulative_energy, b'\x01', ()),
    'historical_1': (Parser.parse_historical_cumulative_energy_1,
                     (2).to_bytes(2, 'big')
                     + b''.join((i + 1).to_bytes(4, 'big') for i in range(48)),
                     (UNIT, COEFFICIENT)),
    'day_for_historical_1': (Parser.parse_day_for_historical_data_1, b'\x02', ()),
    'instantaneous_power': (Parser.parse_instantaneous_power,
                            (-250).to_bytes(4, 'big', signed=True), ()),
    'instantaneous_current': (Parser.parse_instantaneous_current,
                              (55).to_bytes(2, 'big', signed=True)
                              + (-33).to_bytes(2, 'big', signed=True), ()),
    'fixed_time': (Parser.parse_cumulative_energy_measured_at_fixed_time,
                   b'\x07\xea\x08\x17\x0c\x00\x00' + (3000).to_bytes(4, 'big'),
                   (UNIT, COEFFICIENT)),
    'historical_2': (Parser.parse_historical_cumulative_energy_2,
                     b'\x07\xea\x08\x17\x0c\x00\x0c'
                     + b''.join((i + 1).to_bytes(4, 'big') + (i + 51).to_bytes(4, 'big')
                                for i in range(12)), (UNIT, COEFFICIENT)),
    'time_for_historical_2': (Parser.parse_time_for_historical_data_2,
                              b'\x07\xea\x08\x17\x0c\x00\x0c', ()),
    'historical_3': (Parser.parse_historical_cumulative_energy_3,
                     b'\x07\xea\x08\x17\x0c\x05\x0a'
                     + b''.join((i + 1).to_bytes(4, 'big') + (i + 61).to_bytes(4, 'big')
                                for i in range(10)), (UNIT, COEFFICIENT)),
    'time_for_historical_3': (Parser.parse_time_for_historical_data_3,
                              b'\x07\xea\x08\x17\x0c\x05\x0a', ()),
}

# nothing left without a length to check: the two that looked variable are
# fixed in the MRA - serial number at 12 bytes, route B id at 16 - and the
# meter on the test rig returns exactly 12 for the one it supports
NO_LENGTH_TO_CHECK = set()


class TestNoParserInventsAValue(TimeBoxedTestCase):

    def test_a_truncated_edt_is_refused_rather_than_read(self):
        for name, (parse, edt, extra) in WELL_FORMED.items():
            for cut in range(len(edt)):
                with self.subTest(parser=name, bytes=cut):
                    with self.assertRaises(MomongaResponseNotExpected):
                        parse(edt[:cut], *extra)

    def test_a_well_formed_edt_still_parses(self):
        for name, (parse, edt, extra) in WELL_FORMED.items():
            with self.subTest(parser=name):
                parse(edt, *extra)  # must not raise

    def test_nothing_is_left_without_a_length_to_check(self):
        self.assertEqual(NO_LENGTH_TO_CHECK, set())


class TestTheDeclaredCountHasToMatchTheData(TimeBoxedTestCase):
    """The series say how many points they carry; the EDT has to hold them."""

    def test_series_two_declaring_more_points_than_it_sent(self):
        edt = (b'\x07\xea\x08\x17\x0c\x00\x0c'
               + b''.join((i).to_bytes(4, 'big') + (i).to_bytes(4, 'big') for i in range(3)))

        with self.assertRaises(MomongaResponseNotExpected):
            Parser.parse_historical_cumulative_energy_2(edt, UNIT, COEFFICIENT)

    def test_series_three_declaring_more_points_than_it_sent(self):
        edt = (b'\x07\xea\x08\x17\x0c\x05\x0a'
               + b''.join((i).to_bytes(4, 'big') + (i).to_bytes(4, 'big') for i in range(2)))

        with self.assertRaises(MomongaResponseNotExpected):
            Parser.parse_historical_cumulative_energy_3(edt, UNIT, COEFFICIENT)

    def test_a_property_map_declaring_more_codes_than_it_sent(self):
        with self.assertRaises(MomongaResponseNotExpected):
            Parser.parse_property_map(b'\x05\x80\xd3')

    def test_a_short_series_is_not_padded_with_readings_of_zero(self):
        short = (2).to_bytes(2, 'big') + b''.join((i + 1).to_bytes(4, 'big') for i in range(6))

        with self.assertRaises(MomongaResponseNotExpected):
            Parser.parse_historical_cumulative_energy_1(short, UNIT, COEFFICIENT)


def _rx(data: bytes) -> SkParsedRxUdp:
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=ECHONET_LITE_PORT,
                         dst_port=ECHONET_LITE_PORT, src_mac=b'', side=0, sec=2, data=data)


class TestTheShortReadingDoesNotReachTheCaller(TimeBoxedTestCase):
    """The same three bytes, down each of the three public paths."""

    SHORT = (1234567).to_bytes(4, 'big')[:3]   # reads 482.2 kWh instead of 123456.7

    def _momonga(self, edt):
        sm = MagicMock()
        sm.recv_q, sm.notif_q = queue.Queue(), queue.Queue()
        sm.smart_meter_addr = 'FE80::1'

        def xmitter(payload, timeout=None):
            tid, opc, epc = payload[2:4], payload[11], payload[12]
            sm.recv_q.put(_rx(ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                              + bytes([0x72, opc, epc, len(edt)]) + edt))

        sm.xmitter = xmitter
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 1, 0.5, 0
        mo.energy_unit, mo.energy_coefficient = UNIT, COEFFICIENT
        return mo

    def test_a_getter_raises_rather_than_returning_it(self):
        with self.assertRaises(MomongaResponseNotExpected):
            self._momonga(self.SHORT).get_measured_cumulative_energy()

    def test_request_to_get_raises_rather_than_returning_it(self):
        with self.assertRaises(MomongaResponseNotExpected):
            self._momonga(self.SHORT).request_to_get({EPC.measured_cumulative_energy})

    def test_a_notification_keeps_the_raw_bytes_and_says_so(self):
        records = []

        class Cap(logging.Handler):
            def emit(self, record):
                records.append(record.levelname)

        lg = logging.getLogger('momonga.momonga')
        cap = Cap()
        lg.addHandler(cap)
        try:
            kept = self._momonga(b'')._parse_or_keep_raw(
                EPC.measured_cumulative_energy, self.SHORT)
        finally:
            lg.removeHandler(cap)

        self.assertEqual(kept, self.SHORT)   # the reader loop survives it
        self.assertIn('WARNING', records)

    def test_a_full_length_reading_still_comes_through(self):
        self.assertAlmostEqual(
            self._momonga((1234567).to_bytes(4, 'big')).get_measured_cumulative_energy(),
            123456.7)


if __name__ == '__main__':
    unittest.main()
