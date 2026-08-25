"""What a typed getter does when the meter's answer cannot be read.

A parser checks the EDT is long enough and then hands it to datetime, int and
bytes.decode. Those raise ValueError or UnicodeDecodeError for a value that is
the right length and still impossible - a month of 13, a year of 0, a serial
number that is not UTF-8 - and nine of the twenty-four parsers can reach one.

request_to_get() has always turned those into MomongaResponseNotExpected, and
get_notification() logs and keeps the raw bytes. The twenty-six typed getters
called their parser directly, so the same meter and the same bytes produced a
MomongaError through one door and a bare ValueError through another. Anyone
following the manual and catching MomongaError - which is what the no-handler
example does before letting a supervisor restart - caught it in one case and
exited on an unhandled traceback in the other.

Run:
  python -m unittest tests/test_getter_parse_failures_unit.py -v
"""
import ast
import datetime
import inspect
import pathlib
import unittest

from unittest.mock import patch

import momonga

from momonga.momonga import Momonga
from momonga.momonga_echonet_data import EchonetDataParser, EchonetPropertyWithData
from momonga.momonga_echonet_enum import EchonetPropertyCode as Code

from tests._timebox import TimeBoxedTestCase

ARGUMENTS = {'reverse': False, 'day': 1, 'num_of_data_points': 1,
             'timestamp': datetime.datetime(2026, 8, 26, 12, 0)}


def _typed_getters():
    """Every get_*() that reads one property through a parser."""
    found = {}
    for name in dir(Momonga):
        if not name.startswith('get_') or name == 'get_notification':
            continue
        method = getattr(Momonga, name)
        if not callable(method):
            continue
        try:
            tree = ast.parse(inspect.getsource(method).lstrip())
        except (OSError, SyntaxError):
            continue
        parsers = {n.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                   and n.value.id == 'EchonetDataParser'}
        if len(parsers) == 1:
            found[name] = parsers.pop()
    return found


GETTERS = _typed_getters()


class _MeterSaying:
    """A Momonga whose meter answers every request with the given EDT."""

    def __new__(cls, edt):
        mo = Momonga.__new__(Momonga)
        mo.is_open = True
        mo.energy_unit = 1
        mo.energy_coefficient = 1
        mo._request_to_get = lambda reqs, _d=edt: [
            EchonetPropertyWithData(reqs[0].epc, _d)]
        mo._request_to_set = lambda reqs: None
        return mo


def _call(mo, name):
    method = getattr(Momonga, name)
    kwargs = {p: ARGUMENTS[p] for p in inspect.signature(method).parameters
              if p in ARGUMENTS}
    return method(mo, **kwargs)


class TestThereAreTypedGettersToCheck(TimeBoxedTestCase):

    def test_the_search_found_them(self):
        self.assertGreater(len(GETTERS), 20)

    def test_none_of_them_calls_its_parser_directly(self):
        """A getter added the old way would be outside every handler the
        manual describes, and nothing else here would notice."""
        source = pathlib.Path('momonga/momonga.py').read_text()

        self.assertNotIn('return EchonetDataParser.parse_', source)


class TestAParserFailureReachesTheCallerAsAMomongaError(TimeBoxedTestCase):

    def test_every_getter_wraps_what_its_parser_raises(self):
        for getter, parser in sorted(GETTERS.items()):
            with self.subTest(getter=getter):
                mo = _MeterSaying(b'\x00' * 32)
                with patch.object(EchonetDataParser, parser,
                                  side_effect=ValueError('month must be in 1..12')):
                    with self.assertRaises(momonga.MomongaResponseNotExpected):
                        _call(mo, getter)

    def test_it_names_the_property_and_keeps_what_broke(self):
        mo = _MeterSaying(b'\xff\xff')

        with self.assertRaises(momonga.MomongaResponseNotExpected) as caught:
            mo.get_current_time_setting()

        self.assertIn('%02X' % Code.current_time_setting, str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_a_serial_number_that_is_not_text_is_the_same_kind_of_failure(self):
        mo = _MeterSaying(b'\xff' * 12)

        with self.assertRaises(momonga.MomongaResponseNotExpected) as caught:
            mo.get_serial_number()

        self.assertIsInstance(caught.exception.__cause__, UnicodeDecodeError)

    def test_the_real_values_that_break_a_parser_are_covered(self):
        """Not mocked: the bytes a meter would have to send."""
        for getter, edt in ((Momonga.get_current_time_setting, b'\xff\xff'),
                            (Momonga.get_current_date_setting, b'\x00\x00\x00\x00'),
                            (Momonga.get_serial_number, b'\xff' * 12)):
            with self.subTest(getter=getter.__name__):
                with self.assertRaises(momonga.MomongaError):
                    getter(_MeterSaying(edt))


class TestNothingElseChanges(TimeBoxedTestCase):

    def test_a_short_edt_still_says_so_rather_than_being_wrapped_again(self):
        """_require_edt already raises a MomongaError; wrapping it a second
        time would bury the length it was complaining about."""
        mo = _MeterSaying(b'\x00')

        with self.assertRaises(momonga.MomongaResponseNotExpected) as caught:
            mo.get_current_time_setting()

        self.assertIn('bytes long but', str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_a_reading_that_parses_comes_back_unchanged(self):
        self.assertEqual(_MeterSaying(b'\x0c\x1e').get_current_time_setting(),
                         datetime.time(12, 30))
        self.assertEqual(_MeterSaying(b'\x00\x00\x02\x64').get_instantaneous_power(),
                         612)

    def test_a_no_data_reading_is_still_None_rather_than_a_failure(self):
        self.assertIsNone(_MeterSaying(b'\x7f\xff\xff\xfe').get_instantaneous_power())

    def test_request_to_get_reports_it_the_same_way_it_always_did(self):
        mo = _MeterSaying(b'\xff\xff')
        mo._request_to_get = lambda reqs: [
            EchonetPropertyWithData(Code.current_time_setting, b'\xff\xff')]

        with self.assertRaises(momonga.MomongaResponseNotExpected) as caught:
            mo.request_to_get({Code.current_time_setting})

        self.assertIn('Could not read EPC', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
