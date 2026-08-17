"""
Unit tests for the transmission spacing __init_energy_unit() applies.

Run:
  python -m unittest tests/test_init_energy_unit.py -v
"""
import unittest
from unittest.mock import patch

from momonga.momonga import Momonga
from momonga.momonga_exception import MomongaResponseNotPossible

INIT = '_Momonga__init_energy_unit'


def _make_mo():
    mo = object.__new__(Momonga)
    mo.is_open = True
    mo.internal_xmit_interval = 5
    mo.energy_unit = 1
    mo.energy_coefficient = 1
    return mo


def _trace(mo, coefficient_supported):
    events = []

    def unit():
        events.append('E1')
        return 0.1

    def coefficient():
        events.append('D3')
        if not coefficient_supported:
            raise MomongaResponseNotPossible('0xD3 is optional')
        return 1

    with patch.object(mo, 'get_unit_for_cumulative_energy', unit), \
         patch.object(mo, 'get_coefficient_for_cumulative_energy', coefficient), \
         patch('momonga.momonga.time.sleep', lambda s: events.append('sleep %s' % s)):
        getattr(mo, INIT)()
    return events


class TestTransmissionSpacing(unittest.TestCase):

    def test_the_two_requests_are_spaced_apart(self):
        events = _trace(_make_mo(), coefficient_supported=True)
        self.assertEqual(events, ['E1', 'sleep 5', 'D3', 'sleep 5'])

    def test_spacing_is_the_same_without_the_optional_property(self):
        events = _trace(_make_mo(), coefficient_supported=False)
        self.assertEqual(events, ['E1', 'sleep 5', 'D3', 'sleep 5'])

    def test_an_unsupported_coefficient_falls_back_to_one(self):
        mo = _make_mo()
        _trace(mo, coefficient_supported=False)
        self.assertEqual(mo.energy_coefficient, 1)

    def test_the_unit_and_coefficient_are_stored(self):
        mo = _make_mo()
        _trace(mo, coefficient_supported=True)
        self.assertEqual(mo.energy_unit, 0.1)
        self.assertEqual(mo.energy_coefficient, 1)


if __name__ == '__main__':
    unittest.main()
