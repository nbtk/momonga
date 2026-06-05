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
        getters = [
            ('operation_status',                    self.mo.get_operation_status()),
            ('installation_location',               self.mo.get_installation_location()),
            ('standard_version',                    self.mo.get_standard_version()),
            ('fault_status',                        self.mo.get_fault_status()),
            ('manufacturer_code',                   self.mo.get_manufacturer_code()),
            ('serial_number',                       self.mo.get_serial_number()),
            ('current_time_setting',                self.mo.get_current_time_setting()),
            ('current_date_setting',                self.mo.get_current_date_setting()),
            ('properties_for_status_notification',  self.mo.get_properties_for_status_notification()),
            ('properties_to_set_values',            self.mo.get_properties_to_set_values()),
            ('properties_to_get_values',            self.mo.get_properties_to_get_values()),
            ('route_b_id',                          self.mo.get_route_b_id()),
            ('coefficient_for_cumulative_energy',   self.mo.get_coefficient_for_cumulative_energy()),
            ('number_of_effective_digits',          self.mo.get_number_of_effective_digits_for_cumulative_energy()),
            ('measured_cumulative_energy',          self.mo.get_measured_cumulative_energy()),
            ('measured_cumulative_energy_reversed', self.mo.get_measured_cumulative_energy(reverse=True)),
            ('unit_for_cumulative_energy',          self.mo.get_unit_for_cumulative_energy()),
            ('day_for_historical_data_1',           self.mo.get_day_for_historical_data_1()),
            ('instantaneous_power',                 self.mo.get_instantaneous_power()),
            ('instantaneous_current',               self.mo.get_instantaneous_current()),
            ('time_for_historical_data_2',          self.mo.get_time_for_historical_data_2()),
            ('time_for_historical_data_3',          self.mo.get_time_for_historical_data_3()),
        ]

        names, coros = zip(*getters)
        results = await asyncio.gather(*coros)

        print()
        for name, val in zip(names, results):
            print('  %-45s %s' % (name + ':', val))

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

        for name, val in zip(names, results):
            self.assertIsInstance(val, expected_types[name],
                                  'Unexpected type for %s: %r' % (name, val))


if __name__ == '__main__':
    unittest.main()
