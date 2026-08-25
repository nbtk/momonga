"""The code people copy, checked against the library it is copied from.

error_handling_example.py sat in tests/ with nothing running it and nothing
importing it, and README.md is text. Every name in either was a copy of the API
frozen at the moment it was written, so renaming an exception left them wrong
and silent, which is the one thing an example must never be.

What is checked is what a reader takes away: the names still exist, the
handlers still name the two groups rather than their members, the schedule
really does start over each session, reading really does survive a response
that cannot be read, and the minutes quoted in a docstring are the minutes the
arguments buy.

Run:
  python -m unittest tests/test_examples_unit.py -v
"""
import ast
import inspect
import io
import pathlib
import re
import unittest

from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import momonga

from momonga.momonga_sk_wrapper import MomongaSkWrapper

from tests import error_handling_example as example
from tests._timebox import TimeBoxedTestCase

SOURCE = pathlib.Path(example.__file__).read_text()
TREE = ast.parse(SOURCE)

GROUPS = (momonga.MomongaConnectionFailure, momonga.MomongaNeedToReopen)

MANUAL = pathlib.Path('README.md').read_text()


def _manual_blocks():
    """Every fenced python3 block in the manual, code and output alike."""
    return re.findall(r'^```python3\n(.*?)^```', MANUAL, re.S | re.M)


def _names_reached_for(tree):
    return {n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == 'momonga'}


def _constructor_keywords(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and ast.unparse(node.func) in ('momonga.Momonga',
                                               'momonga.AsyncMomonga')):
            for keyword in node.keywords:
                yield keyword


def _exceptions_caught(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            parts = (node.type.elts if isinstance(node.type, ast.Tuple)
                     else [node.type])
            names.update(ast.unparse(p).removeprefix('momonga.') for p in parts)
    return names


def _accepted_arguments():
    return (set(inspect.signature(momonga.Momonga).parameters)
            | set(inspect.signature(momonga.AsyncMomonga).parameters))


class TestItStillMatchesTheLibrary(TimeBoxedTestCase):

    def test_every_name_it_reaches_for_exists(self):
        used = _names_reached_for(TREE)

        self.assertTrue(used)
        for name in sorted(used):
            with self.subTest(name=name):
                self.assertTrue(hasattr(momonga, name))

    def test_the_arguments_it_passes_are_real_parameters(self):
        accepted = _accepted_arguments()
        keywords = list(_constructor_keywords(TREE))

        self.assertTrue(keywords)
        for keyword in keywords:
            with self.subTest(argument=keyword.arg):
                self.assertIn(keyword.arg, accepted)


class TestTheHandlersNameGroupsNotMembers(TimeBoxedTestCase):
    """The point of the two group names is that an example written against them
    does not grow a line when a new cause is added. Catching a member instead
    still works today and quietly stops covering its siblings tomorrow."""

    @staticmethod
    def _caught():
        return _exceptions_caught(TREE)

    def test_it_catches_something(self):
        self.assertTrue(self._caught())

    def test_no_handler_reaches_inside_a_group(self):
        for name in sorted(self._caught()):
            cls = getattr(momonga, name)
            with self.subTest(caught=name):
                if cls in GROUPS:
                    continue
                self.assertFalse(issubclass(cls, GROUPS))

    def test_both_groups_are_accounted_for(self):
        caught = self._caught()

        for group in GROUPS:
            with self.subTest(group=group.__name__):
                self.assertIn(group.__name__, caught)


class TestTheScheduleStartsOverEachSession(TimeBoxedTestCase):
    """A single chain handed to a second session carries on from where the
    first left it, so the climb happens once and every later outage waits the
    longest delay from its first attempt. Passing the function is the fix, and
    the example has to keep passing the function."""

    def test_a_second_session_starts_where_the_first_one_did(self):
        first = example.backoff()
        for _ in range(3):          # the first session climbed to the top
            next(first)

        self.assertEqual(next(example.backoff()), 60.0)

    def test_it_climbs(self):
        schedule = example.backoff()

        self.assertEqual([next(schedule) for _ in range(3)], [60.0, 120.0, 300.0])

    def test_and_then_never_runs_out(self):
        schedule = example.backoff()
        for _ in range(3):
            next(schedule)

        self.assertEqual([next(schedule) for _ in range(100)], [600.0] * 100)

    def test_the_function_is_passed_rather_than_its_result(self):
        for call in (n for n in ast.walk(TREE) if isinstance(n, ast.Call)
                     and ast.unparse(n.func) == 'momonga.Momonga'):
            for keyword in call.keywords:
                if keyword.arg != 'reopen_delays':
                    continue
                with self.subTest(passed=ast.unparse(keyword.value)):
                    self.assertEqual(ast.unparse(keyword.value), 'backoff')

    def test_the_library_takes_it_that_way(self):
        mo = momonga.Momonga('rbid', 'pwd', '/dev/ttyUSB0',
                             reopen_delays=example.backoff)

        self.assertTrue(callable(mo.reopen_delays))


class _Meter:
    """Answers get_instantaneous_power() from a script."""

    def __init__(self, *answers):
        self.answers = iter(answers)

    def get_instantaneous_power(self):
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _Enough(Exception):
    """Stops read_forever, which is meant never to stop on its own."""


class TestReadingSurvivesAResponseItCannotRead(TimeBoxedTestCase):

    def _read(self, *answers):
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= len(answers):
                raise _Enough

        out = io.StringIO()
        with patch.object(example.time, 'sleep', sleep), redirect_stdout(out):
            with self.assertRaises(_Enough):
                example.read_forever(_Meter(*answers))
        return out.getvalue().splitlines(), sleeps

    def test_a_reading_is_printed(self):
        printed, _ = self._read(612.0)

        self.assertEqual(printed, ['612.0W'])

    def test_no_data_is_not_formatted_as_a_number(self):
        printed, _ = self._read(None)

        self.assertEqual(printed, ['no data'])

    def test_an_unreadable_response_does_not_end_the_loop(self):
        printed, sleeps = self._read(
            momonga.MomongaResponseNotExpected('a frame that made no sense'),
            612.0)

        self.assertEqual(printed, ['612.0W'])
        self.assertEqual(len(sleeps), 2)

    def test_it_waits_a_minute_between_readings(self):
        _, sleeps = self._read(612.0, 613.0)

        self.assertEqual(sleeps, [60, 60])

    def test_a_lost_session_is_left_to_the_caller(self):
        with patch.object(example.time, 'sleep', lambda s: None):
            with self.assertRaises(momonga.MomongaNeedToReopen):
                example.read_forever(
                    _Meter(momonga.MomongaNeedToReopen('the session is gone')))


class TestTheMinutesItQuotes(TimeBoxedTestCase):
    """no_handler_at_all leans on its arguments instead of a handler, and says
    how long they buy. If that figure drifts, the example is telling people to
    size a supervisor against a number that is no longer true."""

    @staticmethod
    def _arguments():
        for call in (n for n in ast.walk(TREE) if isinstance(n, ast.Call)
                     and ast.unparse(n.func) == 'momonga.Momonga'):
            found = {k.arg: ast.literal_eval(k.value) for k in call.keywords
                     if k.arg in ('scan_retries', 'join_retries')}
            if found:
                return found
        return {}

    def _scan_minutes(self, retry):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        widths = []

        def exec_command(_self, command, expect=None, *args, **kwargs):
            widths.append(int(command[3]))
            return ['EVENT 22 FE80::1 0']          # the PAN is never there

        with patch.object(MomongaSkWrapper, 'exec_command', exec_command):
            with self.assertRaises(momonga.MomongaSkScanFailure):
                skw.skscan(retry=retry)
        return sum(0.0096 * (2 ** w + 1) * 28 for w in widths) / 60

    def test_it_names_both_counts(self):
        self.assertEqual(set(self._arguments()), {'scan_retries', 'join_retries'})

    def test_about_fifteen_minutes_is_about_fifteen_minutes(self):
        args = self._arguments()
        join_minutes = args['join_retries'] * 40 / 60   # skjoin's own estimate

        total = self._scan_minutes(args['scan_retries']) + join_minutes

        self.assertAlmostEqual(total, 15, delta=1.5)

    def test_the_docstring_says_the_same(self):
        self.assertIn('15 minutes', example.no_handler_at_all.__doc__)


class TestTheWaysToRunItAreTheWaysItLists(TimeBoxedTestCase):

    def test_the_names_in_the_docstring_are_the_names_it_accepts(self):
        for name in ('manual', 'automatic', 'none'):
            with self.subTest(name=name):
                self.assertIn(name, example.EXAMPLES)
                self.assertIn(name, example.__doc__)

    def test_each_one_is_callable(self):
        for name, fn in example.EXAMPLES.items():
            with self.subTest(name=name):
                self.assertTrue(callable(fn))


class TestTheManualSaysTheSameThing(TimeBoxedTestCase):
    """README.md is what most people read and none of it was ever run. It gave
    different advice from the file it now points at: one handler for both
    groups and no wait at all, so a meter that had gone would be re-scanned
    every couple of minutes for as long as the process lived. Its comment also
    listed the members of each group by hand, which went stale the moment
    MomongaSkResponseNotExpected joined one of them.
    """

    def _code_blocks(self):
        for block in _manual_blocks():
            try:
                yield block, ast.parse(block)
            except SyntaxError:
                continue

    def test_the_manual_has_examples_in_it(self):
        self.assertGreater(len(list(self._code_blocks())), 20)

    def test_anything_that_will_not_parse_is_output_rather_than_code(self):
        for block in _manual_blocks():
            try:
                ast.parse(block)
            except SyntaxError:
                with self.subTest(block=block[:40]):
                    self.assertTrue(block.lstrip().startswith('{<'))

    def test_every_name_it_reaches_for_exists(self):
        for block, tree in self._code_blocks():
            for name in sorted(_names_reached_for(tree)):
                with self.subTest(name=name):
                    self.assertTrue(hasattr(momonga, name))

    def test_the_arguments_it_passes_are_real_parameters(self):
        accepted = _accepted_arguments()

        for block, tree in self._code_blocks():
            for keyword in _constructor_keywords(tree):
                with self.subTest(argument=keyword.arg):
                    self.assertIn(keyword.arg, accepted)

    def test_no_handler_reaches_inside_a_group(self):
        for block, tree in self._code_blocks():
            for name in sorted(_exceptions_caught(tree)):
                cls = getattr(momonga, name, None)
                if not isinstance(cls, type) or cls in GROUPS:
                    continue
                with self.subTest(caught=name):
                    self.assertFalse(issubclass(cls, GROUPS))

    def test_it_waits_before_connecting_again(self):
        """The file explains why; the manual used to do the opposite."""
        handlers = [h for _, tree in self._code_blocks()
                    for h in ast.walk(tree)
                    if isinstance(h, ast.ExceptHandler) and h.type is not None
                    and 'MomongaConnectionFailure' in ast.unparse(h.type)]

        self.assertTrue(handlers)
        for handler in handlers:
            waits = any(ast.unparse(n.func) == 'time.sleep'
                        for n in ast.walk(handler) if isinstance(n, ast.Call))
            with self.subTest(handler=ast.unparse(handler.type)):
                self.assertTrue(waits)

    def test_it_points_at_the_runnable_version(self):
        self.assertIn('tests/error_handling_example.py', MANUAL)


if __name__ == '__main__':
    unittest.main()
