"""Rules about the source that a reader cannot check by reading one file.

Each of these was a real defect found by hand during the 0.7.0 review, and
each would come back silently: a new export missing from __all__, a log line
built before anyone asks for it, a re-raise that loses why. Nothing here needs
a tool installed - the checks read the source the same way the review did.

Each one reads the syntax tree and decides on what the code is, not on how it
is worded. Anything that would need a list of phrases to look for is not here:
it would pass or fail on the wording rather than the thing, and a rule that
can be walked around by rephrasing is not a check. Line length is not here
either - this project sets its own rather than taking PEP 8's 79.

Run:
  python -m unittest tests/test_conventions_unit.py -v
"""
import ast
import pathlib
import unittest

import momonga

from tests._timebox import TimeBoxedTestCase

SOURCES = sorted(pathlib.Path('momonga').glob('*.py'))
LOG_METHODS = ('debug', 'info', 'warning', 'error', 'critical', 'exception')


def _trees():
    for path in SOURCES:
        yield path, ast.parse(path.read_text())


class TestThePackageNamesWhatItExports(TimeBoxedTestCase):
    """py.typed tells a type checker to read the annotations, and mypy's
    re-export rule then says a name imported into __init__.py is private
    unless the package says otherwise. Without __all__, a caller running
    --strict was told momonga does not export Momonga."""

    def test_every_public_name_is_declared(self):
        public = {n for n in dir(momonga)
                  if not n.startswith('_') and not n.startswith('momonga')}

        self.assertEqual(public - set(momonga.__all__), set())

    def test_every_declared_name_exists(self):
        for name in momonga.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(momonga, name))

    def test_every_exception_is_exported(self):
        from momonga import momonga_exception as module
        for name, cls in vars(module).items():
            if (isinstance(cls, type) and issubclass(cls, BaseException)
                    and cls.__module__ == module.__name__):
                with self.subTest(exception=name):
                    self.assertIn(name, momonga.__all__)

    def test_the_marker_is_there_and_ships(self):
        self.assertTrue(pathlib.Path('momonga/py.typed').is_file())
        self.assertIn("package_data={'momonga': ['py.typed']}",
                      pathlib.Path('setup.py').read_text())


class TestALogLineIsBuiltOnlyWhenItIsWanted(TimeBoxedTestCase):
    """logger.debug('%s' % value) formats before logging is asked whether it
    wants the line. The serial dumps run on every line in and out of the
    module, so at INFO that is a string built and thrown away each time."""

    @staticmethod
    def _eager():
        for path, tree in _trees():
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, 'attr', '') in LOG_METHODS
                        and node.args
                        and isinstance(node.args[0], ast.BinOp)
                        and isinstance(node.args[0].op, ast.Mod)):
                    yield '%s:%d' % (path.name, node.lineno)

    def test_no_call_formats_its_own_message(self):
        self.assertEqual(list(self._eager()), [])

    def test_the_check_looks_at_something(self):
        calls = [n for _p, t in _trees() for n in ast.walk(t)
                 if isinstance(n, ast.Call) and getattr(n.func, 'attr', '') in LOG_METHODS]

        self.assertGreater(len(calls), 50)


class TestARaiseInsideAHandlerSaysHowTheTwoRelate(TimeBoxedTestCase):
    """Without `from`, Python prints "During handling of the above exception,
    another exception occurred", which is what it says when the handler itself
    is broken - not when a failure is being renamed on its way out. `from err`
    keeps the cause; `from None` drops one that says nothing."""

    @staticmethod
    def _bare_raises():
        for path, tree in _trees():
            for handler in (n for n in ast.walk(tree)
                            if isinstance(n, ast.ExceptHandler)):
                for node in ast.walk(handler):
                    if (isinstance(node, ast.Raise) and node.exc is not None
                            and node.cause is None):
                        yield '%s:%d' % (path.name, node.lineno)

    def test_every_one_of_them_does(self):
        self.assertEqual(list(self._bare_raises()), [])

    def test_a_bare_reraise_is_not_counted(self):
        """`raise` on its own re-raises what is already being handled."""
        reraises = [n for _p, t in _trees()
                    for h in ast.walk(t) if isinstance(h, ast.ExceptHandler)
                    for n in ast.walk(h) if isinstance(n, ast.Raise) and n.exc is None]

        self.assertGreater(len(reraises), 0)


if __name__ == '__main__':
    unittest.main()
