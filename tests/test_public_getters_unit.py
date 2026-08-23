"""
Every public getter, against a meter that answers.

Branch coverage said the getters themselves - the largest single block of the
public API - were reached only by the integration tests, which skip without a
dongle and so never run in CI. Four of them were reached by nothing at all.
Each one is three lines, but those three lines pick the EPC, choose between
the forward and reverse property, and hand the energy unit and coefficient to
the parser. The parsers are tested; which parser gets called with what is not.

The fake module here answers whatever it is asked for, so the request the
getter built is what decides the reply, and a getter asking for the wrong
property gets the wrong answer rather than a canned one.

Run:
  python -m unittest tests/test_public_getters_unit.py -v
"""
import datetime
import queue
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_echonet_enum import (CONTROLLER_EOJ, ECHONET_LITE_EHD,
                                          ECHONET_LITE_PORT, EchonetPropertyCode,
                                          SMART_METER_EOJ)
from momonga.momonga_response import SkParsedRxUdp
from tests._timebox import TimeBoxedTestCase

UNIT, COEFFICIENT = 0.1, 1
EPC = EchonetPropertyCode

# What the meter holds, per property. Values are deliberately distinguishable
# so a getter reaching for its neighbour is visible in the result.
METER = {
    EPC.operation_status:            b'\x30',
    EPC.installation_location:       b'\x08',
    EPC.standard_version_information: b'\x00\x00\x46\x00',
    EPC.fault_status:                b'\x42',
    EPC.manufacturer_code:           b'\x00\x00\x16',
    EPC.serial_number:               b'S19Z011823',
    EPC.current_time_setting:        b'\x0c\x22',
    EPC.current_date_setting:        b'\x07\xea\x08\x17',
    EPC.properties_for_status_notification: b'\x02\x80\xd3',
    EPC.properties_to_set_values:    b'\x01\xe5',
    EPC.properties_to_get_values:    b'\x02\xe7\xe8',
    EPC.route_b_id:                  b'\x00' + b'\x00\x00\x16' + bytes(range(12)),
    EPC.one_minute_measured_cumulative_energy:
        b'\x07\xea\x08\x17\x0c\x22\x00' + (100).to_bytes(4, 'big') + (7).to_bytes(4, 'big'),
    EPC.coefficient_for_cumulative_energy: b'\x00\x00\x00\x01',
    EPC.number_of_effective_digits_for_cumulative_energy: b'\x06',
    EPC.measured_cumulative_energy:          (1000).to_bytes(4, 'big'),
    EPC.measured_cumulative_energy_reversed: (2000).to_bytes(4, 'big'),
    EPC.unit_for_cumulative_energy:  b'\x01',
    EPC.historical_cumulative_energy_1:
        (2).to_bytes(2, 'big') + b''.join((i).to_bytes(4, 'big') for i in range(48)),
    EPC.historical_cumulative_energy_1_reversed:
        (2).to_bytes(2, 'big') + b''.join((i + 500).to_bytes(4, 'big') for i in range(48)),
    EPC.day_for_historical_data_1:   b'\x02',
    EPC.instantaneous_power:         (-250).to_bytes(4, 'big', signed=True),
    EPC.instantaneous_current:       (55).to_bytes(2, 'big', signed=True)
                                     + (-33).to_bytes(2, 'big', signed=True),
    EPC.cumulative_energy_measured_at_fixed_time:
        b'\x07\xea\x08\x17\x0c\x00\x00' + (3000).to_bytes(4, 'big'),
    EPC.cumulative_energy_measured_at_fixed_time_reversed:
        b'\x07\xea\x08\x17\x0c\x00\x00' + (4000).to_bytes(4, 'big'),
    EPC.historical_cumulative_energy_2:
        b'\x07\xea\x08\x17\x0c\x00' + b'\x0c'
        + b''.join((i).to_bytes(4, 'big') + (i + 50).to_bytes(4, 'big') for i in range(12)),
    EPC.time_for_historical_data_2:  b'\x07\xea\x08\x17\x0c\x00\x0c',
    EPC.historical_cumulative_energy_3:
        b'\x07\xea\x08\x17\x0c\x05' + b'\x0a'
        + b''.join((i).to_bytes(4, 'big') + (i + 60).to_bytes(4, 'big') for i in range(10)),
    EPC.time_for_historical_data_3:  b'\x07\xea\x08\x17\x0c\x05\x0a',
}

GET, SET_C, GET_RES, SET_RES = 0x62, 0x61, 0x72, 0x71


def _rx(data: bytes) -> SkParsedRxUdp:
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=ECHONET_LITE_PORT,
                         dst_port=ECHONET_LITE_PORT, src_mac=b'', side=0, sec=2,
                         data=data)


class _AMeterThatAnswers(TimeBoxedTestCase):
    """Replies to whatever property the getter actually asked for."""

    def setUp(self):
        sm = MagicMock()
        sm.recv_q = queue.Queue()
        sm.notif_q = queue.Queue()
        sm.smart_meter_addr = 'FE80::1'
        self.asked = []

        def xmitter(payload, timeout=None):
            tid, esv, opc = payload[2:4], payload[10], payload[11]
            answer, cur = b'', 12
            for _ in range(opc):
                epc = payload[cur]
                cur += 2 + payload[cur + 1]  # skip the PDC and any EDT sent with it
                self.asked.append((esv, epc))
                if esv == SET_C:
                    answer += bytes([epc, 0])
                else:
                    edt = METER[epc]
                    answer += bytes([epc, len(edt)]) + edt
            frame = (ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                     + bytes([SET_RES if esv == SET_C else GET_RES, opc]) + answer)
            sm.recv_q.put(_rx(frame))

        sm.xmitter = xmitter
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 1, 0.5, 0
        mo.energy_unit, mo.energy_coefficient = UNIT, COEFFICIENT
        self.mo, self.sm = mo, sm

    def epcs_asked(self, esv=None):
        return [e for s, e in self.asked if esv is None or s == esv]


class TestTheIdentityAndStateGetters(_AMeterThatAnswers):

    def test_operation_status(self):
        self.assertIs(self.mo.get_operation_status(), True)
        self.assertEqual(self.epcs_asked(), [EPC.operation_status])

    def test_installation_location(self):
        self.assertEqual(self.mo.get_installation_location(), 'living room 0')

    def test_standard_version(self):
        self.assertEqual(self.mo.get_standard_version(), 'F.0')

    def test_fault_status(self):
        self.assertIs(self.mo.get_fault_status(), False)

    def test_manufacturer_code(self):
        self.assertEqual(self.mo.get_manufacturer_code(), b'\x00\x00\x16')

    def test_serial_number(self):
        self.assertEqual(self.mo.get_serial_number(), 'S19Z011823')

    def test_current_time_setting(self):
        self.assertEqual(self.mo.get_current_time_setting(), datetime.time(12, 34))

    def test_current_date_setting(self):
        self.assertEqual(self.mo.get_current_date_setting(), datetime.date(2026, 8, 23))

    def test_route_b_id(self):
        self.assertEqual(self.mo.get_route_b_id(),
                         {'manufacturer code': b'\x00\x00\x16',
                          'authentication id': bytes(range(12))})


class TestThePropertyMapGetters(_AMeterThatAnswers):
    """Three getters over one parser; each has to ask for its own map."""

    def test_for_status_notification(self):
        self.assertEqual(self.mo.get_properties_for_status_notification(),
                         {EPC.operation_status, EPC.coefficient_for_cumulative_energy})
        self.assertEqual(self.epcs_asked(), [EPC.properties_for_status_notification])

    def test_to_set_values(self):
        self.assertEqual(self.mo.get_properties_to_set_values(),
                         {EPC.day_for_historical_data_1})
        self.assertEqual(self.epcs_asked(), [EPC.properties_to_set_values])

    def test_to_get_values(self):
        self.assertEqual(self.mo.get_properties_to_get_values(),
                         {EPC.instantaneous_power, EPC.instantaneous_current})
        self.assertEqual(self.epcs_asked(), [EPC.properties_to_get_values])


class TestTheInstantaneousGetters(_AMeterThatAnswers):

    def test_power_keeps_its_sign(self):
        self.assertEqual(self.mo.get_instantaneous_power(), -250)

    def test_current_is_per_phase_in_ampere(self):
        got = self.mo.get_instantaneous_current()

        self.assertAlmostEqual(got['r phase current'], 5.5)
        self.assertAlmostEqual(got['t phase current'], -3.3)


class TestTheEnergyGetters(_AMeterThatAnswers):

    def test_the_unit_is_decoded_from_its_index(self):
        self.assertEqual(self.mo.get_unit_for_cumulative_energy(), 0.1)

    def test_the_coefficient(self):
        self.assertEqual(self.mo.get_coefficient_for_cumulative_energy(), 1)

    def test_the_number_of_effective_digits(self):
        self.assertEqual(self.mo.get_number_of_effective_digits_for_cumulative_energy(), 6)

    def test_cumulative_energy_uses_the_unit_and_coefficient(self):
        self.assertAlmostEqual(self.mo.get_measured_cumulative_energy(), 100.0)

    def test_the_reverse_direction_is_a_different_property(self):
        self.assertAlmostEqual(self.mo.get_measured_cumulative_energy(reverse=True), 200.0)
        self.assertEqual(self.epcs_asked(), [EPC.measured_cumulative_energy_reversed])

    def test_the_one_minute_reading_carries_its_own_timestamp(self):
        got = self.mo.get_one_minute_measured_cumulative_energy()

        self.assertEqual(got['timestamp'], datetime.datetime(2026, 8, 23, 12, 34, 0))
        self.assertAlmostEqual(got['cumulative energy']['normal direction'], 10.0)
        self.assertAlmostEqual(got['cumulative energy']['reverse direction'], 0.7)

    def test_the_fixed_time_reading(self):
        got = self.mo.get_cumulative_energy_measured_at_fixed_time()

        self.assertEqual(got['timestamp'], datetime.datetime(2026, 8, 23, 12, 0))
        self.assertAlmostEqual(got['cumulative energy'], 300.0)

    def test_the_fixed_time_reading_in_reverse_is_a_different_property(self):
        got = self.mo.get_cumulative_energy_measured_at_fixed_time(reverse=True)

        self.assertAlmostEqual(got['cumulative energy'], 400.0)
        self.assertEqual(self.epcs_asked(),
                         [EPC.cumulative_energy_measured_at_fixed_time_reversed])


class TestTheHistoricalSeriesGetters(_AMeterThatAnswers):

    def test_series_one_returns_a_full_day(self):
        points = self.mo.get_historical_cumulative_energy_1(day=2)

        self.assertEqual(len(points), 48)
        self.assertAlmostEqual(points[10]['cumulative energy'], 1.0)
        self.assertEqual(self.epcs_asked(SET_C), [EPC.day_for_historical_data_1])
        self.assertEqual(self.epcs_asked(GET), [EPC.historical_cumulative_energy_1])

    def test_series_one_in_reverse_is_a_different_property(self):
        points = self.mo.get_historical_cumulative_energy_1(day=2, reverse=True)

        self.assertAlmostEqual(points[0]['cumulative energy'], 50.0)
        self.assertEqual(self.epcs_asked(GET),
                         [EPC.historical_cumulative_energy_1_reversed])

    def test_series_two_sets_the_time_before_reading_it(self):
        when = datetime.datetime(2026, 8, 23, 12, 0)
        points = self.mo.get_historical_cumulative_energy_2(when)

        self.assertEqual(len(points), 12)
        self.assertEqual(points[0]['timestamp'], when)
        self.assertAlmostEqual(points[1]['cumulative energy']['normal direction'], 0.1)
        self.assertEqual(self.epcs_asked(SET_C), [EPC.time_for_historical_data_2])
        self.assertEqual(self.epcs_asked(GET), [EPC.historical_cumulative_energy_2])

    def test_series_three_sets_the_time_before_reading_it(self):
        when = datetime.datetime(2026, 8, 23, 12, 5)
        points = self.mo.get_historical_cumulative_energy_3(when)

        self.assertEqual(len(points), 10)
        self.assertEqual(points[0]['timestamp'], when)
        self.assertEqual(self.epcs_asked(SET_C), [EPC.time_for_historical_data_3])
        self.assertEqual(self.epcs_asked(GET), [EPC.historical_cumulative_energy_3])

    def test_the_day_and_times_read_back(self):
        self.assertEqual(self.mo.get_day_for_historical_data_1(), 2)
        self.assertEqual(self.mo.get_time_for_historical_data_2(),
                         {'timestamp': datetime.datetime(2026, 8, 23, 12, 0),
                          'number of data points': 12})
        self.assertEqual(self.mo.get_time_for_historical_data_3(),
                         {'timestamp': datetime.datetime(2026, 8, 23, 12, 5),
                          'number of data points': 10})


if __name__ == '__main__':
    unittest.main()
