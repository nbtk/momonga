"""
The value a meter sends when it has no value.

ECHONET gives each numeric property a code meaning "no data": 0xFFFFFFFE for
the cumulative energies, 0x7FFFFFFE for instantaneous power, 0x7FFE for each
phase of instantaneous current. Read as a number instead, they are
429,496,729.4 kWh, 2,147,483,646 W and 3,276.6 A.

momonga honoured the code in the one-minute reading and the three historical
series and not in the four others, so the same state - the meter having
nothing to report - arrived as None down one path and as an absurd number
down another. The codes here are the ones the Machine Readable Appendix
(v1.3.2, class 0x0288) gives for this class.

Run:
  python -m unittest tests/test_no_data_unit.py -v
"""
import datetime
import queue
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_echonet_data import EchonetDataParser as Parser
from momonga.momonga_echonet_enum import (CONTROLLER_EOJ, ECHONET_LITE_EHD,
                                          ECHONET_LITE_PORT, EchonetPropertyCode,
                                          SMART_METER_EOJ)
from momonga.momonga_response import SkParsedRxUdp
from tests._timebox import TimeBoxedTestCase

EPC = EchonetPropertyCode
UNIT, COEFFICIENT = 0.1, 1

NO_ENERGY = (0xFFFFFFFE).to_bytes(4, 'big')
NO_POWER = (0x7FFFFFFE).to_bytes(4, 'big')
NO_CURRENT = (0x7FFE).to_bytes(2, 'big')

REAL_ENERGY = (1000).to_bytes(4, 'big')          # 100.0 kWh at unit 0.1
STAMP = b'\x07\xea\x08\x18\x0c\x00\x00'          # 2026-08-24 12:00:00
STAMP_6 = b'\x07\xea\x08\x18\x0c\x00'


class TestEveryEnergyReadingHonoursTheCode(TimeBoxedTestCase):
    """0xFFFFFFFE, across all seven places it can arrive."""

    def test_the_cumulative_reading(self):
        self.assertIsNone(Parser.parse_measured_cumulative_energy(
            NO_ENERGY, UNIT, COEFFICIENT))

    def test_the_reading_at_a_fixed_time(self):
        got = Parser.parse_cumulative_energy_measured_at_fixed_time(
            STAMP + NO_ENERGY, UNIT, COEFFICIENT)

        self.assertIsNone(got['cumulative energy'])
        self.assertEqual(got['timestamp'], datetime.datetime(2026, 8, 24, 12, 0))

    def test_the_one_minute_reading_in_both_directions(self):
        got = Parser.parse_one_minute_measured_cumulative_energy(
            STAMP + NO_ENERGY + NO_ENERGY, UNIT, COEFFICIENT)

        self.assertEqual(got['cumulative energy'],
                         {'normal direction': None, 'reverse direction': None})

    def test_one_direction_missing_does_not_take_the_other(self):
        got = Parser.parse_one_minute_measured_cumulative_energy(
            STAMP + NO_ENERGY + REAL_ENERGY, UNIT, COEFFICIENT)

        self.assertIsNone(got['cumulative energy']['normal direction'])
        self.assertAlmostEqual(got['cumulative energy']['reverse direction'], 100.0)

    def test_series_one(self):
        edt = (0).to_bytes(2, 'big') + NO_ENERGY * 48
        got = Parser.parse_historical_cumulative_energy_1(edt, UNIT, COEFFICIENT)

        self.assertEqual([p['cumulative energy'] for p in got], [None] * 48)

    def test_series_two(self):
        edt = STAMP_6 + b'\x01' + NO_ENERGY + NO_ENERGY
        got = Parser.parse_historical_cumulative_energy_2(edt, UNIT, COEFFICIENT)

        self.assertEqual(got[0]['cumulative energy'],
                         {'normal direction': None, 'reverse direction': None})

    def test_series_three(self):
        edt = STAMP_6 + b'\x01' + NO_ENERGY + NO_ENERGY
        got = Parser.parse_historical_cumulative_energy_3(edt, UNIT, COEFFICIENT)

        self.assertEqual(got[0]['cumulative energy'],
                         {'normal direction': None, 'reverse direction': None})


class TestTheInstantaneousReadingsHonourTheirOwnCodes(TimeBoxedTestCase):
    """Different codes, because these are signed."""

    def test_power(self):
        self.assertIsNone(Parser.parse_instantaneous_power(NO_POWER))

    def test_both_phases_of_current(self):
        self.assertEqual(Parser.parse_instantaneous_current(NO_CURRENT + NO_CURRENT),
                         {'r phase current': None, 't phase current': None})

    def test_one_phase_missing_does_not_take_the_other(self):
        got = Parser.parse_instantaneous_current(
            NO_CURRENT + (-33).to_bytes(2, 'big', signed=True))

        self.assertIsNone(got['r phase current'])
        self.assertAlmostEqual(got['t phase current'], -3.3)


class TestARealReadingIsStillARealReading(TimeBoxedTestCase):
    """The codes sit just inside the range, so an off-by-one here would start
    discarding the largest readings the meter can report."""

    def test_the_value_below_the_energy_code(self):
        below = (0xFFFFFFFD).to_bytes(4, 'big')

        self.assertIsNotNone(Parser.parse_measured_cumulative_energy(
            below, UNIT, COEFFICIENT))

    def test_the_value_below_the_power_code(self):
        self.assertEqual(Parser.parse_instantaneous_power(
            (0x7FFFFFFD).to_bytes(4, 'big')), 0x7FFFFFFD)

    def test_the_value_below_the_current_code(self):
        got = Parser.parse_instantaneous_current(
            (0x7FFD).to_bytes(2, 'big') + (0x7FFD).to_bytes(2, 'big'))

        self.assertAlmostEqual(got['r phase current'], 0x7FFD * 0.1)

    def test_the_most_negative_power_is_kept(self):
        self.assertEqual(Parser.parse_instantaneous_power(
            (-2147483647).to_bytes(4, 'big', signed=True)), -2147483647)

    def test_zero_is_a_reading_not_a_gap(self):
        self.assertEqual(Parser.parse_measured_cumulative_energy(
            (0).to_bytes(4, 'big'), UNIT, COEFFICIENT), 0)
        self.assertEqual(Parser.parse_instantaneous_power((0).to_bytes(4, 'big')), 0)

    def test_an_ordinary_reading_is_unchanged(self):
        self.assertAlmostEqual(Parser.parse_measured_cumulative_energy(
            REAL_ENERGY, UNIT, COEFFICIENT), 100.0)
        self.assertEqual(Parser.parse_instantaneous_power(
            (-250).to_bytes(4, 'big', signed=True)), -250)


def _rx(data: bytes) -> SkParsedRxUdp:
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=ECHONET_LITE_PORT,
                         dst_port=ECHONET_LITE_PORT, src_mac=b'', side=0, sec=2,
                         data=data)


class TestNoneReachesTheCaller(TimeBoxedTestCase):
    """Through the getter, not just the parser."""

    def _momonga(self, edt_for):
        sm = MagicMock()
        sm.recv_q, sm.notif_q = queue.Queue(), queue.Queue()
        sm.smart_meter_addr = 'FE80::1'

        def xmitter(payload, timeout=None):
            tid, opc, epc = payload[2:4], payload[11], payload[12]
            edt = edt_for(epc)
            sm.recv_q.put(_rx(ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                              + bytes([0x72, opc, epc, len(edt)]) + edt))

        sm.xmitter = xmitter
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 1, 0.5, 0
        mo.energy_unit, mo.energy_coefficient = UNIT, COEFFICIENT
        return mo

    def test_get_instantaneous_power(self):
        self.assertIsNone(self._momonga(lambda e: NO_POWER).get_instantaneous_power())

    def test_get_instantaneous_current(self):
        got = self._momonga(lambda e: NO_CURRENT * 2).get_instantaneous_current()

        self.assertEqual(got, {'r phase current': None, 't phase current': None})

    def test_get_measured_cumulative_energy(self):
        mo = self._momonga(lambda e: NO_ENERGY)

        self.assertIsNone(mo.get_measured_cumulative_energy())

    def test_get_cumulative_energy_measured_at_fixed_time(self):
        mo = self._momonga(lambda e: STAMP + NO_ENERGY)

        self.assertIsNone(mo.get_cumulative_energy_measured_at_fixed_time()
                          ['cumulative energy'])

    def test_request_to_get_reports_it_the_same_way(self):
        mo = self._momonga(lambda e: NO_POWER)

        got = mo.request_to_get({EPC.instantaneous_power})

        self.assertIsNone(got[EPC.instantaneous_power])

    def test_a_notification_reports_it_the_same_way(self):
        mo = self._momonga(lambda e: b'')
        frame = (ECHONET_LITE_EHD + b'\x00\x01' + SMART_METER_EOJ + CONTROLLER_EOJ
                 + b'\x73\x01' + bytes([EPC.instantaneous_power, 4]) + NO_POWER)
        mo._route_meter_frame(_rx(frame))

        notif = mo.get_notification(timeout=1)

        self.assertIsNone(notif['properties'][EPC.instantaneous_power])


if __name__ == '__main__':
    unittest.main()
