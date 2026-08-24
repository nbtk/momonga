import momonga
import time
import os
import sys


rbid = os.environ.get('MOMONGA_ROUTEB_ID')
pwd = os.environ.get('MOMONGA_ROUTEB_PASSWORD')
dev = os.environ.get('MOMONGA_DEV_PATH')


def read_forever(mo):
    """A response that cannot be read is not a session that has been lost."""
    while True:
        try:
            res = mo.get_instantaneous_power()
        except momonga.MomongaResponseNotExpected as e:
            print('%s: %s' % (type(e).__name__, e), file=sys.stderr)
        else:
            print('no data' if res is None else '%0.1fW' % res)
        time.sleep(60)


def manual_recovery():
    """Build a new session yourself every time Momonga asks for one."""
    while True:
        try:
            with momonga.Momonga(rbid, pwd, dev) as mo:
                read_forever(mo)
        except (momonga.MomongaSkScanFailure,
                momonga.MomongaSkJoinFailure,
                momonga.MomongaTimeoutError,
                momonga.MomongaNeedToReopen) as e:
            print('%s: %s' % (type(e).__name__, e), file=sys.stderr)
            continue


def automatic_recovery():
    """Let reopen_delays do it. MomongaNeedToReopen reaches this handler
    only once all three attempts have been spent."""
    while True:
        try:
            with momonga.Momonga(rbid, pwd, dev,
                                 reopen_delays=[600.0, 600.0, 600.0]) as mo:
                read_forever(mo)
        except (momonga.MomongaSkScanFailure,
                momonga.MomongaSkJoinFailure,
                momonga.MomongaTimeoutError,
                momonga.MomongaNeedToReopen) as e:
            print('%s: %s' % (type(e).__name__, e), file=sys.stderr)
            continue


EXAMPLES = {'manual': manual_recovery, 'automatic': automatic_recovery}


if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'manual'
    if name not in EXAMPLES:
        print('usage: %s [%s]' % (sys.argv[0], '|'.join(EXAMPLES)), file=sys.stderr)
        sys.exit(1)
    EXAMPLES[name]()
