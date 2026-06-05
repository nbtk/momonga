"""
Integration test: all AsyncMomonga getters called simultaneously via asyncio.gather().

Requires a Wi-SUN USB dongle connected to a live smart meter.

Configure via environment variables:
  MOMONGA_ROUTEB_ID        Route-B ID
  MOMONGA_ROUTEB_PASSWORD  Password
  MOMONGA_DEV_PATH         Serial device path (e.g. /dev/ttyUSB0, COM3)
  MOMONGA_TIMEOUT          Seconds to wait per call (default: 2400)

Run:
  MOMONGA_ROUTEB_ID=... MOMONGA_ROUTEB_PASSWORD=... MOMONGA_DEV_PATH=... \
    python -m unittest tests/test_async.py -v
"""
import asyncio
import datetime
import os
import unittest

from momonga import AsyncMomonga
from momonga.momonga_echonet_enum import EchonetPropertyCode

_RBID    = os.environ.get('MOMONGA_ROUTEB_ID', '')
_PWD     = os.environ.get('MOMONGA_ROUTEB_PASSWORD', '')
_DEV     = os.environ.get('MOMONGA_DEV_PATH', '')
_TIMEOUT = int(os.environ.get('MOMONGA_TIMEOUT', '2400'))

_SKIP        = not all([_RBID, _PWD, _DEV])
_SKIP_REASON = 'Set MOMONGA_ROUTEB_ID, MOMONGA_ROUTEB_PASSWORD, MOMONGA_DEV_PATH to run hardware integration tests.'


@unittest.skipIf(_SKIP, _SKIP_REASON)
class TestAllGettersInParallel(unittest.IsolatedAsyncioTestCase):
    """Call every getter simultaneously via asyncio.gather() and verify basic return types."""

    @classmethod
    def setUpClass(cls):
        async def _open():
            mo = AsyncMomonga(_RBID, _PWD, _DEV)
            await mo.open()
            return mo
        cls.mo = asyncio.run(_open())

    @classmethod
    def tearDownClass(cls):
        async def _close():
            await cls.mo.close()
        asyncio.run(_close())

    async def test_all_getters_in_parallel(self):
        EPC = EchonetPropertyCode

        # (EPC, label, getter function)
        all_getters = [
            (EPC.operation_status,                          'operation_status',                    self.mo.get_operation_status),
            (EPC.installation_location,                     'installation_location',               self.mo.get_installation_location),
            (EPC.standard_version_information,              'standard_version',                    self.mo.get_standard_version),
            (EPC.fault_status,                              'fault_status',                        self.mo.get_fault_status),
            (EPC.manufacturer_code,                         'manufacturer_code',                   self.mo.get_manufacturer_code),
            (EPC.serial_number,                             'serial_number',                       self.mo.get_serial_number),
            (EPC.current_time_setting,                      'current_time_setting',                self.mo.get_current_time_setting),
            (EPC.current_date_setting,                      'current_date_setting',                self.mo.get_current_date_setting),
            (EPC.properties_for_status_notification,        'properties_for_status_notification',  self.mo.get_properties_for_status_notification),
            (EPC.properties_to_set_values,                  'properties_to_set_values',            self.mo.get_properties_to_set_values),
            (EPC.properties_to_get_values,                  'properties_to_get_values',            self.mo.get_properties_to_get_values),
            (EPC.route_b_id,                                'route_b_id',                          self.mo.get_route_b_id),
            (EPC.coefficient_for_cumulative_energy,         'coefficient_for_cumulative_energy',   self.mo.get_coefficient_for_cumulative_energy),
            (EPC.number_of_effective_digits_for_cumulative_energy, 'number_of_effective_digits',   self.mo.get_number_of_effective_digits_for_cumulative_energy),
            (EPC.measured_cumulative_energy,                'measured_cumulative_energy',          self.mo.get_measured_cumulative_energy),
            (EPC.measured_cumulative_energy_reversed,       'measured_cumulative_energy_reversed', lambda: self.mo.get_measured_cumulative_energy(reverse=True)),
            (EPC.unit_for_cumulative_energy,                'unit_for_cumulative_energy',          self.mo.get_unit_for_cumulative_energy),
            (EPC.day_for_historical_data_1,                 'day_for_historical_data_1',           self.mo.get_day_for_historical_data_1),
            (EPC.instantaneous_power,                       'instantaneous_power',                 self.mo.get_instantaneous_power),
            (EPC.instantaneous_current,                     'instantaneous_current',               self.mo.get_instantaneous_current),
            (EPC.time_for_historical_data_2,                'time_for_historical_data_2',          self.mo.get_time_for_historical_data_2),
            (EPC.time_for_historical_data_3,                'time_for_historical_data_3',          self.mo.get_time_for_historical_data_3),
        ]

        expected_types = {
            'operation_status':                    (bool, type(None)),
            'installation_location':               str,
            'standard_version':                    str,
            'fault_status':                        (bool, type(None)),
            'manufacturer_code':                   bytes,
            'serial_number':                       str,
            'current_time_setting':                datetime.time,
            'current_date_setting':                datetime.date,
            'properties_for_status_notification':  set,
            'properties_to_set_values':            set,
            'properties_to_get_values':            set,
            'route_b_id':                          dict,
            'coefficient_for_cumulative_energy':   (int, float),
            'number_of_effective_digits':          (int, float),
            'measured_cumulative_energy':          (int, float),
            'measured_cumulative_energy_reversed': (int, float),
            'unit_for_cumulative_energy':          (int, float),
            'day_for_historical_data_1':           (int, float),
            'instantaneous_power':                 (int, float),
            'instantaneous_current':               dict,
            'time_for_historical_data_2':          dict,
            'time_for_historical_data_3':          dict,
        }

        supported = await self.mo.get_properties_to_get_values()

        getters = [(name, fn) for epc, name, fn in all_getters if epc in supported]

        print()
        skipped = [name for epc, name, _ in all_getters if epc not in supported]
        for name in skipped:
            print('  %-45s (not supported)' % (name + ':'))

        names, fns = zip(*getters)
        results = await asyncio.gather(*(fn() for fn in fns))

        for name, val in zip(names, results):
            print('  %-45s %s' % (name + ':', val))

        for name, val in zip(names, results):
            self.assertIsInstance(val, expected_types[name],
                                  'Unexpected type for %s: %r' % (name, val))


if __name__ == '__main__':
    unittest.main()
