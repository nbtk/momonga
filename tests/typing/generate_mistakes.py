"""Write a file that uses every public method wrongly, one line each.

The hand-written consumer_mistakes.py covers four calls. This covers all of
them, and keeps covering them when a method is added: a getter whose return
type is too loose to constrain anything, or an argument annotated Any, shows
up here as a line the checker does not complain about.

  python tests/typing/generate_mistakes.py out.py
  mypy --strict out.py        # every line must be reported
"""
import datetime, inspect, sys, typing

import momonga
from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga

#: 型ごとの「明らかに違う値」
WRONG_ARG = {int: "'not a number'", bool: "'not a bool'",
             float: "'not a number'", str: '123',
             datetime.datetime: '123', bytes: '123'}
#: 返り値をわざと違う型で受ける
WRONG_RET = {int: 'str', float: 'str', str: 'int', bool: 'str',
             bytes: 'int', datetime.time: 'int', datetime.date: 'int',
             dict: 'int', list: 'int', set: 'int'}


def base_of(ann):
    o = typing.get_origin(ann)
    return o if o is not None else ann


def contradiction(ann):
    """A type nothing the annotation allows can be assigned to."""
    if ann is inspect.Signature.empty or ann is None:
        return None
    allowed = set()
    stack = [ann]
    while stack:
        a = stack.pop()
        o = typing.get_origin(a)
        if o in (typing.Union, __import__('types').UnionType):
            stack.extend(typing.get_args(a))
            continue
        allowed.add(base_of(a))
    if typing.Any in allowed:
        return None                    # nothing contradicts Any
    for candidate, name in ((str, 'str'), (int, 'int'), (bytes, 'bytes')):
        if candidate in allowed:
            continue
        if bool in allowed and candidate is int:
            continue                   # bool is an int
        if allowed & {float} and candidate is int:
            continue                   # int is acceptable where float is
        return name
    return None


def methods(cls):
    for name in sorted(dir(cls)):
        if name.startswith('_'):
            continue
        obj = getattr(cls, name)
        if not inspect.isfunction(obj) and not inspect.iscoroutinefunction(obj):
            continue
        try:
            yield name, inspect.signature(obj)
        except (ValueError, TypeError):
            continue


def emit(cls, varname, prefix, skipped):
    lines, count = [], 0
    for name, sig in methods(cls):
        # 1. 引数の型違い（注釈のある引数それぞれ）
        for pname, p in sig.parameters.items():
            if pname == 'self':
                continue
            if p.annotation is inspect.Signature.empty:
                skipped.append('%s.%s(%s) has no annotation'
                               % (cls.__name__, name, pname))
                continue
            bad = WRONG_ARG.get(base_of(p.annotation))
            if bad is None:
                continue
            lines.append('%s%s.%s(%s=%s)' % (prefix, varname, name, pname, bad))
            count += 1
        # 2. 返り値を違う型で受ける
        ret = sig.return_annotation
        if ret is inspect.Signature.empty or ret is None:
            continue
        wrong = contradiction(ret)
        if wrong is None:
            skipped.append('%s.%s -> %s' % (cls.__name__, name, ret))
            continue
        call = ', '.join('%s=%s' % (n, _sample(p))
                         for n, p in sig.parameters.items()
                         if n != 'self' and p.default is inspect.Parameter.empty)
        lines.append('_v_%s_%s: %s = %s%s.%s(%s)'
                     % (varname, name, wrong, prefix, varname, name, call))
        count += 1
    return lines, count


def _sample(p):
    b = base_of(p.annotation)
    return {int: '0', bool: 'False', float: '0.0', str: "''",
            bytes: "b''", datetime.datetime: 'datetime.datetime.now()'}.get(b, 'None')


out = ['"""Generated: every public method used wrongly, one line each."""',
       'import datetime', '', 'import momonga', '',
       "mo = momonga.Momonga('id', 'pw', '/dev/ttyUSB0')",
       "amo = momonga.AsyncMomonga('id', 'pw', '/dev/ttyUSB0')", '']
skipped: list[str] = []
sync_lines, n1 = emit(Momonga, 'mo', '', skipped)
async_lines, n2 = emit(AsyncMomonga, 'amo', 'await ', skipped)
out += sync_lines
out += ['', 'async def _uses_async() -> None:']
out += ['    ' + l for l in async_lines]
open(sys.argv[1], 'w').write('\n'.join(out) + '\n')
print('generated %d wrong uses (sync %d, async %d)' % (n1 + n2, n1, n2))
if skipped:
    print('NO MISUSE COULD BE WRITTEN FOR:')
    for s_ in skipped:
        print('  ' + s_)
    print('An annotation loose enough that nothing can contradict it constrains'
          ' nothing. Tighten it, or teach WRONG_RET how to contradict it.')
    sys.exit(1)
