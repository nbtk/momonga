"""
Unit tests for the docstrings the public API carries.

Run:
  python -m unittest tests/test_docstrings_unit.py -v
"""
import inspect
import unittest

import momonga
from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga
from momonga.momonga_session_manager import GATE_WAIT_LIMIT


def _own_doc(obj):
    """The docstring written for this object, never one inherited from a base."""
    if isinstance(obj, property):
        return _own_doc(obj.fget) if obj.fget is not None else None
    if isinstance(obj, type):
        doc = obj.__dict__.get('__doc__')
    else:
        doc = getattr(obj, '__doc__', None)
    return doc if doc and doc.strip() else None


def _public_members(cls):
    for name, obj in vars(cls).items():
        if name.startswith('_') or isinstance(obj, type):
            continue
        if isinstance(obj, property) or inspect.isfunction(obj) or inspect.iscoroutinefunction(obj):
            yield name, obj


class DocstringTestCase(unittest.TestCase):
    def test_classes_are_described(self):
        for cls in (Momonga, AsyncMomonga):
            with self.subTest(cls=cls.__name__):
                self.assertIsNotNone(_own_doc(cls))

    def test_every_public_member_is_described(self):
        for cls in (Momonga, AsyncMomonga):
            for name, obj in _public_members(cls):
                with self.subTest(member='%s.%s' % (cls.__name__, name)):
                    self.assertIsNotNone(_own_doc(obj))

    def test_every_exception_is_described(self):
        for name in momonga.__all__:
            obj = getattr(momonga, name)
            if isinstance(obj, type) and issubclass(obj, BaseException):
                with self.subTest(exception=name):
                    self.assertIsNotNone(_own_doc(obj))

    def test_async_says_what_its_sync_counterpart_says(self):
        """A delegating method describes itself by carrying the original's words."""
        mirrored = 0
        for name, obj in _public_members(AsyncMomonga):
            counterpart = getattr(Momonga, name, None)
            if counterpart is None or isinstance(obj, property):
                continue
            with self.subTest(member=name):
                self.assertEqual(_own_doc(obj), _own_doc(counterpart))
            mirrored += 1
        self.assertGreater(mirrored, 30)

    def test_summary_line_stands_on_its_own(self):
        """One sentence, then a blank line - what a reader sees on hover."""
        subjects = [('%s.%s' % (cls.__name__, n), o)
                    for cls in (Momonga, AsyncMomonga) for n, o in _public_members(cls)]
        subjects += [(c.__name__, c) for c in (Momonga, AsyncMomonga)]
        subjects += [(n, getattr(momonga, n)) for n in momonga.__all__
                     if isinstance(getattr(momonga, n), type)
                     and issubclass(getattr(momonga, n), BaseException)]
        for label, obj in subjects:
            doc = _own_doc(obj)
            if doc is None:
                continue
            lines = inspect.cleandoc(doc).splitlines()
            with self.subTest(subject=label):
                self.assertTrue(lines[0].endswith('.'),
                                'summary should end in a period: %r' % lines[0])
                if len(lines) > 1:
                    self.assertEqual('', lines[1],
                                     'a blank line should follow the summary')


class NumbersInDocstringsTestCase(unittest.TestCase):
    """A number a reader cannot work out for themselves has to stay true."""

    def test_the_gate_schedule_xmit_timeout_names_is_the_one_that_runs(self):
        for cls in (Momonga, AsyncMomonga):
            doc = _own_doc(vars(cls)['xmit_timeout'])
            with self.subTest(cls=cls.__name__):
                self.assertIn(str(GATE_WAIT_LIMIT), doc)

    def test_the_message_for_None_names_the_same_schedule(self):
        mo = Momonga('id', 'pw', '/dev/ttyUSB0')
        with self.assertRaises(momonga.MomongaValueError) as caught:
            mo.xmit_timeout = None  # type: ignore[assignment]
        self.assertIn(str(GATE_WAIT_LIMIT), str(caught.exception))


if __name__ == '__main__':
    unittest.main()
