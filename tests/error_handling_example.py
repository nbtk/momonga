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


# Losing a session and never having one are different failures, and only the
# first is what reopen_delays paces. Scanning again the instant a scan failed
# spends radio time and changes nothing, so the second waits.
CONNECT_RETRY_DELAY = 600.0


def report(e):
    print('%s: %s' % (type(e).__name__, e), file=sys.stderr)


def manual_recovery():
    """Build a new session yourself every time Momonga asks for one."""
    while True:
        try:
            with momonga.Momonga(rbid, pwd, dev) as mo:
                read_forever(mo)
        except momonga.MomongaNeedToReopen as e:
            # the session went; a new one is worth building at once.
            # MomongaXmitTimeout, MomongaSkCommandBusy and
            # MomongaSkCommandCancelled are subclasses and land here too
            report(e)
        except (momonga.MomongaSkScanFailure,
                momonga.MomongaSkJoinFailure,
                momonga.MomongaTimeoutError) as e:
            # the meter is not answering at all
            report(e)
            time.sleep(CONNECT_RETRY_DELAY)


def automatic_recovery():
    """Let reopen_delays rebuild the session.

    It covers requests only. A session that could never be established in the
    first place is not a MomongaNeedToReopen and never reaches that machinery,
    which is why the two handlers below are not the same: by the time the
    first one runs all three delays have been spent, and when the second one
    runs nothing has waited at all.
    """
    while True:
        try:
            with momonga.Momonga(rbid, pwd, dev,
                                 reopen_delays=[600.0, 600.0, 600.0]) as mo:
                read_forever(mo)
        except momonga.MomongaNeedToReopen as e:
            report(e)
        except (momonga.MomongaSkScanFailure,
                momonga.MomongaSkJoinFailure,
                momonga.MomongaTimeoutError) as e:
            report(e)
            time.sleep(CONNECT_RETRY_DELAY)


EXAMPLES = {'manual': manual_recovery, 'automatic': automatic_recovery}


if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'manual'
    if name not in EXAMPLES:
        print('usage: %s [%s]' % (sys.argv[0], '|'.join(EXAMPLES)), file=sys.stderr)
        sys.exit(1)
    EXAMPLES[name]()
