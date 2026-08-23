"""
Unit tests for a property whose EDT is too short for the type it claims.

_property_block_is_complete only checks that the declared PDC bytes are there.
A meter can still declare a length its own property cannot be read from.

Run:
  python -m unittest tests/test_malformed_property_unit.py -v
"""
import queue
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_echonet_enum import EchonetPropertyCode as EPC
from momonga.momonga_exception import MomongaResponseNotExpected, MomongaRuntimeError
from momonga.momonga_echonet_data import EchonetPropertyWithData
from momonga.momonga_response import SkParsedRxUdp
from tests._timebox import TimeBoxedTestCase

FIXED_TIME_OK = b'\x07\xea\x08\x16\x11\x00\x00\x00\x03\x92\xbe'


def _notification(epc, edt):
    data = (b'\x10\x81\x00\x01' + b'\x02\x88\x01' + b'\x05\xff\x01'
            + b'\x73' + b'\x01' + bytes([epc, len(edt)]) + edt)
    return SkParsedRxUdp(src_addr='', dst_addr='', src_port=0, dst_port=0,
                         src_mac=b'', side=0, sec=0, data=data)


def _read(epc, edt):
    mo = Momonga('', '', '/dev/ttyUSB0')
    mo.is_open = True
    sm = MagicMock()
    sm.notif_q = queue.Queue()
    sm.raise_if_receiver_died.return_value = None
    mo.session_manager = sm
    sm.notif_q.put(_notification(epc, edt))
    return mo.get_notification(timeout=1)


class TestAShortNotificationDoesNotKillTheReader(TimeBoxedTestCase):

    def test_a_property_too_short_to_read_comes_back_raw(self):
        result = _read(EPC.cumulative_energy_measured_at_fixed_time, b'\x07')
        self.assertEqual(result['properties'][EPC.cumulative_energy_measured_at_fixed_time],
                         b'\x07')

    def test_the_reader_keeps_the_rest_of_the_notification(self):
        result = _read(EPC.current_time_setting, b'\x08')
        self.assertEqual(result['esv'].name, 'inf')

    def test_a_well_formed_property_still_parses(self):
        result = _read(EPC.cumulative_energy_measured_at_fixed_time, FIXED_TIME_OK)
        value = result['properties'][EPC.cumulative_energy_measured_at_fixed_time]
        self.assertIn('cumulative energy', value)

    def test_an_epc_with_no_parser_still_comes_back_raw(self):
        result = _read(0x01, b'\xab\xcd')
        self.assertEqual(result['properties'][0x01], b'\xab\xcd')

    def test_a_failure_that_is_not_a_momonga_error_is_caught_too(self):
        # the length checks in the parsers raise MomongaResponseNotExpected, so
        # narrowing this catch to MomongaError still handles a short EDT. What
        # it stops handling is everything else: a month of 13 is a ValueError
        # out of datetime, and it would take the reader loop down with it
        full_length_but_impossible = b'\x07\xea\x0d\x16\x11\x00\x00\x00\x03\x92\xbe'

        result = _read(EPC.cumulative_energy_measured_at_fixed_time,
                       full_length_but_impossible)

        self.assertEqual(result['properties'][EPC.cumulative_energy_measured_at_fixed_time],
                         full_length_but_impossible)


class TestRequestToGetSaysWhatWentWrong(TimeBoxedTestCase):

    def _request_to_get(self, epc, edt):
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo._request_to_get = lambda props: [EchonetPropertyWithData(epc, edt)]
        return mo.request_to_get({epc})

    def test_a_short_edt_is_a_momonga_error_not_an_index_error(self):
        with self.assertRaises(MomongaResponseNotExpected):
            self._request_to_get(EPC.cumulative_energy_measured_at_fixed_time, b'\x07')

    def test_a_missing_edt_is_a_momonga_error_too(self):
        with self.assertRaises(MomongaResponseNotExpected):
            self._request_to_get(EPC.instantaneous_power, None)

    def test_a_well_formed_edt_still_parses(self):
        result = self._request_to_get(EPC.cumulative_energy_measured_at_fixed_time,
                                      FIXED_TIME_OK)
        self.assertIn('cumulative energy',
                      result[EPC.cumulative_energy_measured_at_fixed_time])

    def test_a_parser_raising_a_momonga_error_keeps_its_own_type(self):
        with self.assertRaises(MomongaRuntimeError) as caught:
            self._request_to_get(EPC.unit_for_cumulative_energy, b'\xff')  # undefined unit
        self.assertNotIsInstance(caught.exception, MomongaResponseNotExpected)


if __name__ == '__main__':
    unittest.main()
