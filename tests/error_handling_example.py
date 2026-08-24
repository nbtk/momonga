import momonga
import time
import os
import sys

from itertools import chain, repeat


rbid = os.environ.get('MOMONGA_ROUTEB_ID')
pwd = os.environ.get('MOMONGA_ROUTEB_PASSWORD')
dev = os.environ.get('MOMONGA_DEV_PATH')


# Losing a session and never having one are different failures, and only the
# first is what reopen_delays paces. Scanning again the instant a scan failed
# spends radio time and changes nothing, so the second waits.
#
# The other half of that is scan_retries and join_retries, which decide how
# hard open() tries before it reports a failure at all. Raising join_retries
# is what stops a link that needs many attempts from re-scanning a PAN it has
# already found on every round of this loop.
CONNECT_RETRY_DELAY = 600.0


def backoff():
    """A minute, then two, then five, then every ten for as long as it takes.

    Handed to Momonga as the function, not its result: reopen_delays calls it
    for a fresh schedule, so every outage ramps from the bottom no matter how
    many sessions have been built. Passing chain(...) itself would work for
    the first Momonga and quietly stop ramping for the ones after it, since a
    chain object carries on from wherever the last one left it.
    """
    return chain([60.0, 120.0, 300.0], repeat(600.0))


def report(e):
    print('%s: %s' % (type(e).__name__, e), file=sys.stderr)


def read_forever(mo):
    """A response that cannot be read is not a session that has been lost."""
    while True:
        try:
            res = mo.get_instantaneous_power()
        except momonga.MomongaResponseNotExpected as e:
            report(e)
        else:
            print('no data' if res is None else '%0.1fW' % res)
        time.sleep(60)


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
    """Put the backoff in reopen_delays, and there is nothing left to catch.

    A schedule ending in repeat() never runs out, so MomongaNeedToReopen never
    reaches the caller - the library keeps rebuilding the session on its own,
    waiting longer each time. Catching it here to wait and try again would
    only be the same schedule written twice.

    What is left is the failures reopen_delays does not cover. A session that
    could never be established is a MomongaSkScanFailure or a
    MomongaSkJoinFailure out of open(), and open() is outside its scope.
    """
    while True:
        try:
            with momonga.Momonga(rbid, pwd, dev, reopen_delays=backoff) as mo:
                read_forever(mo)
        except (momonga.MomongaSkScanFailure,
                momonga.MomongaSkJoinFailure,
                momonga.MomongaTimeoutError) as e:
            report(e)
            time.sleep(CONNECT_RETRY_DELAY)


def give_up_to_the_supervisor():
    """Bound the schedule, and let something outside decide what next.

    This is when catching MomongaNeedToReopen earns its place: what follows is
    not another wait. A wait belongs in reopen_delays, where the library is
    already doing it. Exiting does not - under systemd, or `docker run
    --restart=always`, the process comes back with a new interpreter and a new
    handle on the serial port, which is more than reopening gets you.
    """
    try:
        with momonga.Momonga(rbid, pwd, dev,
                             reopen_delays=[60.0, 120.0, 300.0, 600.0]) as mo:
            read_forever(mo)
    except (momonga.MomongaSkScanFailure,
            momonga.MomongaSkJoinFailure,
            momonga.MomongaTimeoutError,
            momonga.MomongaNeedToReopen) as e:
        report(e)
        sys.exit(1)


EXAMPLES = {'manual': manual_recovery,
            'automatic': automatic_recovery,
            'supervised': give_up_to_the_supervisor}


if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'manual'
    if name not in EXAMPLES:
        print('usage: %s [%s]' % (sys.argv[0], '|'.join(EXAMPLES)), file=sys.stderr)
        sys.exit(1)
    EXAMPLES[name]()
