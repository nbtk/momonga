"""What a getter says it returns, against what it returns.

py.typed hands these annotations to a caller's type checker as the truth, so
one that is wrong is worse than one that is missing: a checker will reject
correct code on the strength of it. Four were wrong when this was written.
get_historical_cumulative_energy_1 declared its readings as a dict of
directions and returns a single number; _3 declared one level of nesting
where there are two. Three getters had copied a declaration between them
and the three shapes are not the same.

mypy cannot catch this on its own. It checks that the parser and the getter
agree, and they did - both said the same wrong thing. Only running the code
and looking at what comes back tells them apart.

Run:
  python -m unittest tests/test_declared_types_unit.py -v
"""
import datetime
import inspect
import types
import typing
import unittest

import momonga

from momonga.momonga import Momonga
from momonga.momonga_echonet_data import EchonetPropertyWithData
from momonga.momonga_echonet_enum import EchonetPropertyCode as Code

from tests._timebox import TimeBoxedTestCase

_SERIES_48 = bytes([0]) + b''.join((i + 1).to_bytes(4, 'big') * 2 for i in range(48))
_SERIES_3 = b'\x07\xea\x08\x1d\x0c\x00\x03' + b''.join(
    (i + 1).to_bytes(4, 'big') * 2 for i in range(3))
_AT_FIXED_TIME = b'\x07\xea\x08\x1d\x0c\x00\x00' + b'\x00\x00\x01\x00' * 2

#: What the meter would answer with, per property.
EDT = {
    Code.operation_status: b'\x30',
    Code.installation_location: b'\x00',
    Code.standard_version_information: b'\x00\x00\x45\x00',
    Code.fault_status: b'\x42',
    Code.manufacturer_code: b'\x00\x00\x01',
    Code.serial_number: b'ABCDEFGHIJKL',
    Code.current_time_setting: b'\x0c\x1e',
    Code.current_date_setting: b'\x07\xea\x08\x1d',
    Code.route_b_id: b'\x00' * 32,
    Code.coefficient_for_cumulative_energy: b'\x00\x00\x00\x01',
    Code.number_of_effective_digits_for_cumulative_energy: b'\x06',
    Code.unit_for_cumulative_energy: b'\x02',
    Code.day_for_historical_data_1: b'\x00',
    Code.instantaneous_power: b'\x00\x00\x02\x64',
    Code.instantaneous_current: b'\x00\x64\x00\x64',
    Code.measured_cumulative_energy: b'\x00\x00\x01\x00',
    Code.measured_cumulative_energy_reversed: b'\x00\x00\x01\x00',
    Code.cumulative_energy_measured_at_fixed_time: _AT_FIXED_TIME,
    Code.cumulative_energy_measured_at_fixed_time_reversed: _AT_FIXED_TIME,
    Code.one_minute_measured_cumulative_energy: _AT_FIXED_TIME,
    Code.historical_cumulative_energy_1: _SERIES_48,
    Code.historical_cumulative_energy_1_reversed: _SERIES_48,
    Code.historical_cumulative_energy_2: _SERIES_3,
    Code.historical_cumulative_energy_3: _SERIES_3,
    Code.time_for_historical_data_2: b'\x07\xea\x08\x1d\x0c\x00\x02',
    Code.time_for_historical_data_3: b'\x07\xea\x08\x1d\x0c\x00\x02',
    Code.properties_to_set_values: b'\x02\x80\x81',
    Code.properties_to_get_values: b'\x02\x80\x81',
    Code.properties_for_status_notification: b'\x02\x80\x81',
}

ARGUMENTS = {'reverse': False, 'day': 0, 'num_of_data_points': 2,
             'timestamp': datetime.datetime(2026, 8, 29, 12, 0)}


def _fits(value, annotation):
    """Whether a value is one of the things the annotation allows."""
    origin = typing.get_origin(annotation)
    if origin is None:
        if annotation is inspect.Signature.empty:
            return True
        if annotation is type(None):
            return value is None
        try:
            return isinstance(value, annotation)
        except TypeError:          # a form isinstance cannot answer
            return True
    if origin in (typing.Union, types.UnionType):
        return any(_fits(value, arg) for arg in typing.get_args(annotation))
    if origin is list:
        (inner,) = typing.get_args(annotation)
        return isinstance(value, list) and all(_fits(v, inner) for v in value)
    if origin is set:
        (inner,) = typing.get_args(annotation)
        return isinstance(value, set) and all(_fits(v, inner) for v in value)
    if origin is dict:
        _key, val = typing.get_args(annotation)
        return isinstance(value, dict) and all(_fits(v, val) for v in value.values())
    return True


def _getters():
    for name in sorted(n for n in dir(Momonga) if n.startswith('get_')):
        if name == 'get_notification':      # needs a session, not a reading
            continue
        yield name, getattr(Momonga, name)


class TestEveryGetterReturnsWhatItSaysItDoes(TimeBoxedTestCase):

    @staticmethod
    def _answering(edt):
        mo = Momonga.__new__(Momonga)
        mo.is_open = True
        mo.energy_unit = 1
        mo.energy_coefficient = 1
        mo._request_to_get = lambda reqs: [EchonetPropertyWithData(reqs[0].epc, edt)]
        mo._request_to_set = lambda reqs: None
        return mo

    def _call(self, name, method):
        kwargs = {p: ARGUMENTS[p] for p in inspect.signature(method).parameters
                  if p in ARGUMENTS}
        epc = None
        seen = []

        def capture(reqs):
            seen.append(reqs[0].epc)
            return [EchonetPropertyWithData(reqs[0].epc, EDT[reqs[0].epc])]

        mo = self._answering(None)
        mo._request_to_get = capture
        return method(mo, **kwargs), seen

    def test_there_are_getters_to_check(self):
        self.assertGreater(len(list(_getters())), 20)

    def test_a_reading_fits_the_declared_return_type(self):
        for name, method in _getters():
            with self.subTest(getter=name):
                value, _ = self._call(name, method)
                annotation = inspect.signature(method).return_annotation
                self.assertTrue(_fits(value, annotation),
                                '%s returned %r, which is not %s'
                                % (name, value, annotation))

    def test_the_check_would_notice_a_wrong_one(self):
        """The declaration that was wrong: a number where a dict was claimed."""
        self.assertFalse(_fits(256, dict[str, int]))
        self.assertFalse(_fits([{'a': 1}], list[dict[str, dict[str, int]]]))
        self.assertTrue(_fits([{'a': {'b': 1}}], list[dict[str, dict[str, int]]]))

    def test_every_getter_was_actually_exercised(self):
        for name, method in _getters():
            with self.subTest(getter=name):
                _value, seen = self._call(name, method)
                self.assertEqual(len(seen), 1)


class TestTheAsyncSurfaceDeclaresTheSame(TimeBoxedTestCase):
    """AsyncMomonga hands each call to the sync object, so a declaration that
    disagrees with it is wrong by construction."""

    def test_each_pair_agrees(self):
        from momonga.momonga_async import AsyncMomonga
        for name, method in _getters():
            other = getattr(AsyncMomonga, name, None)
            if other is None:
                continue
            with self.subTest(getter=name):
                self.assertEqual(
                    str(inspect.signature(other).return_annotation),
                    str(inspect.signature(method).return_annotation))


if __name__ == '__main__':
    unittest.main()
