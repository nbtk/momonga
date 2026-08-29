"""Assert the checker reported every line the generator wrote, for the right reason.

A line that goes unreported means the annotation behind it does not constrain
anything - Any somewhere, or a return type wide enough to accept the wrong
assignment. Counting errors would not catch that: two reports on one line and
none on another still adds up.

Neither would the line number alone: a call reported for a missing argument
says nothing about whether the argument's type was checked. Each generated
line names the error code it is meant to provoke, and that code has to appear.
"""
import pathlib
import re
import sys

generated, report = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

lines = generated.read_text().splitlines()
want = {n: m.group(1)
        for n, line in enumerate(lines, start=1)
        for m in [re.search(r'# want: ([a-z-]+)$', line)] if m}

got: dict[int, set[str]] = {}
for n_, code in re.findall(r'^[^:]+:(\d+): error:.*\[([a-z-]+)\]$',
                           report.read_text(), re.M):
    got.setdefault(int(n_), set()).add(code)

missed = [(n, c) for n, c in want.items() if c not in got.get(n, set())]
print('wrong uses written: %d' % len(want))
print('reported as meant : %d' % (len(want) - len(missed)))
if missed:
    print('NOT REPORTED AS MEANT:')
    for n, c in missed:
        print('  %d: want %s, got %s | %s'
              % (n, c, sorted(got.get(n, ())) or 'nothing', lines[n - 1].strip()))
    sys.exit(1)
if not want:
    print('the generator produced nothing')
    sys.exit(1)
print('every wrong use was reported for the reason it was written')
