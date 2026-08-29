"""Assert the checker reported every line the generator wrote.

A line that goes unreported means the annotation behind it does not constrain
anything - Any somewhere, or a return type wide enough to accept the wrong
assignment. Counting errors would not catch that: two reports on one line and
none on another still adds up.
"""
import pathlib
import re
import sys

generated, report = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

wrote = [n for n, line in enumerate(generated.read_text().splitlines(), start=1)
         if ('mo.' in line or 'amo.' in line)
         and 'momonga.Momonga(' not in line
         and 'momonga.AsyncMomonga(' not in line]
reported = {int(m) for m in re.findall(r'^[^:]+:(\d+): error:',
                                       report.read_text(), re.M)}

missed = [n for n in wrote if n not in reported]
print('wrong uses written: %d' % len(wrote))
print('lines reported    : %d' % len(reported & set(wrote)))
if missed:
    lines = generated.read_text().splitlines()
    print('NOT REPORTED:')
    for n in missed:
        print('  %d: %s' % (n, lines[n - 1].strip()))
    sys.exit(1)
if not wrote:
    print('the generator produced nothing')
    sys.exit(1)
print('every wrong use was reported')
