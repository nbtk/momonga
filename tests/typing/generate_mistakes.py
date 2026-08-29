"""Write a file that uses every public method wrongly, one line each.

The hand-written consumer_mistakes.py covers four calls. This covers all of
them, and keeps covering them when a method is added: a getter whose return
type is too loose to constrain anything, or an argument annotated Any, shows
up here as a method no misuse could be written for.

Each line carries the error code it is meant to provoke, so that a line
reported for some unrelated reason does not pass as a catch.

  python tests/typing/generate_mistakes.py out.py
  mypy --strict out.py        # every line must be reported, with that code
"""
import datetime, inspect, sys, types, typing

import momonga
from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga

#: contradiction() の答え -> その型のリテラル
ARG_VALUE = {'str': "'not the right type'", 'int': '123', 'bytes': "b'\\x00'"}
#: 必須引数に置く、注釈どおりの値
SAMPLE = {int: '0', bool: 'False', float: '0.0', str: "''", bytes: "b''",
          set: 'set()', frozenset: 'frozenset()', list: '[]', dict: '{}',
          tuple: '()', datetime.datetime: 'datetime.datetime.now()'}


def base_of(ann):
    o = typing.get_origin(ann)
    return o if o is not None else ann


def contradiction(ann):
    """A type that is incompatible with the annotation in either direction."""
    if ann is inspect.Signature.empty or ann is None:
        return None
    allowed = set()
    stack = [ann]
    while stack:
        a = stack.pop()
        if typing.get_origin(a) in (typing.Union, types.UnionType):
            stack.extend(typing.get_args(a))
            continue
        allowed.add(base_of(a))
    if typing.Any in allowed or object in allowed:
        return None                    # everything is both of those
    for candidate, name in ((str, 'str'), (int, 'int'), (bytes, 'bytes')):
        if any(isinstance(a, type) and issubclass(a, candidate)
               for a in allowed if isinstance(a, type)):
            continue                   # a subclass of it is allowed
        if candidate is int and allowed & {float, complex}:
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
            yield name, inspect.signature(obj), inspect.isasyncgenfunction(obj)
        except (ValueError, TypeError):
            continue


def sample(p):
    return SAMPLE.get(base_of(p.annotation), 'None')


def emit_init(cls, skipped):
    """Constructing it wrongly - the first line any caller writes."""
    lines, count = [], 0
    sig = inspect.signature(cls.__init__)
    for pname, p in sig.parameters.items():
        if pname == 'self':
            continue
        if p.annotation is inspect.Signature.empty:
            skipped.append('%s.__init__(%s) has no annotation' % (cls.__name__, pname))
            continue
        wrong = contradiction(p.annotation)
        if wrong is None:
            skipped.append('%s.__init__(%s: %s) admits everything'
                           % (cls.__name__, pname, p.annotation))
            continue
        args = ['%s=%s' % (n, sample(q)) for n, q in sig.parameters.items()
                if n not in ('self', pname) and q.default is inspect.Parameter.empty]
        args.append('%s=%s' % (pname, ARG_VALUE[wrong]))
        lines.append('momonga.%s(%s)  # want: arg-type' % (cls.__name__, ', '.join(args)))
        count += 1
    return lines, count


def emit_properties(cls, varname, skipped):
    """Reading a property into the wrong type, and assigning the wrong type to it."""
    lines, count = [], 0
    for name in sorted(vars(cls)):
        prop = vars(cls)[name]
        if name.startswith('_') or not isinstance(prop, property):
            continue
        if prop.fget is not None:
            ret = inspect.signature(prop.fget).return_annotation
            wrong = contradiction(ret)
            if wrong is None:
                skipped.append('%s.%s -> %s' % (cls.__name__, name, ret))
            else:
                lines.append('_p_%s_%s: %s = %s.%s  # want: assignment'
                             % (varname, name, wrong, varname, name))
                count += 1
        if prop.fset is not None:
            ann = list(inspect.signature(prop.fset).parameters.values())[1].annotation
            wrong = contradiction(ann)
            if wrong is None:
                skipped.append('%s.%s setter %s'
                               % (cls.__name__, name,
                                  'has no annotation'
                                  if ann is inspect.Signature.empty
                                  else 'takes %s, which admits everything' % (ann,)))
            else:
                lines.append('%s.%s = %s  # want: assignment'
                             % (varname, name, ARG_VALUE[wrong]))
                count += 1
    return lines, count


def emit(cls, varname, await_, skipped):
    lines, count = [], 0
    for name, sig, is_asyncgen in methods(cls):
        prefix = '' if is_asyncgen else await_
        # 1. 引数の型違い（注釈のある引数それぞれ）
        for pname, p in sig.parameters.items():
            if pname == 'self':
                continue
            if p.annotation is inspect.Signature.empty:
                skipped.append('%s.%s(%s) has no annotation'
                               % (cls.__name__, name, pname))
                continue
            wrong = contradiction(p.annotation)
            if wrong is None:
                skipped.append('%s.%s(%s: %s) admits everything'
                               % (cls.__name__, name, pname, p.annotation))
                continue
            lines.append('%s%s.%s(%s=%s)  # want: arg-type'
                         % (prefix, varname, name, pname, ARG_VALUE[wrong]))
            count += 1
        # 2. 返り値を違う型で受ける
        ret = sig.return_annotation
        if ret is inspect.Signature.empty or ret is None:
            continue
        wrong = contradiction(ret)
        if wrong is None:
            skipped.append('%s.%s -> %s' % (cls.__name__, name, ret))
            continue
        call = ', '.join('%s=%s' % (n, sample(p))
                         for n, p in sig.parameters.items()
                         if n != 'self' and p.default is inspect.Parameter.empty)
        lines.append('_v_%s_%s: %s = %s%s.%s(%s)  # want: assignment'
                     % (varname, name, wrong, prefix, varname, name, call))
        count += 1
    return lines, count


out = ['"""Generated: every public method used wrongly, one line each."""',
       'import datetime', '', 'import momonga', '',
       "mo = momonga.Momonga('id', 'pw', '/dev/ttyUSB0')",
       "amo = momonga.AsyncMomonga('id', 'pw', '/dev/ttyUSB0')", '']
skipped: list[str] = []
sync_lines, n1 = emit_init(Momonga, skipped)
async_lines, n2 = emit_init(AsyncMomonga, skipped)
for cls_, var_, into_, n_ in ((Momonga, 'mo', 'sync', 1), (AsyncMomonga, 'amo', 'async', 2)):
    l_, c_ = emit_properties(cls_, var_, skipped)
    if into_ == 'sync':
        sync_lines += l_; n1 += c_
    else:
        async_lines += l_; n2 += c_
l_, c_ = emit(Momonga, 'mo', '', skipped)
sync_lines += l_; n1 += c_
l_, c_ = emit(AsyncMomonga, 'amo', 'await ', skipped)
async_lines += l_; n2 += c_
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
          ' nothing. Tighten it, or teach contradiction() how to contradict it.')
    sys.exit(1)
