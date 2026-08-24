"""Three ways to keep reading a meter, differing in who does the retrying.

    manual        you rebuild the session on every failure
    automatic     the library rebuilds it, and you handle the first connect
    none          the library rebuilds it endlessly and there is nothing
                  left to catch

Run:
  python tests/error_handling_example.py [manual|automatic|none]
"""
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
            # the session is unusable and the module is not - it is answering,
            # it just could not carry this. A new session is worth building at
            # once. MomongaXmitTimeout, MomongaSkCommandBusy and
            # MomongaSkCommandCancelled all land here
            report(e)
        except momonga.MomongaConnectionFailure as e:
            # what the session sits on failed: the scan, the join, the module
            # answering at all, or the device file. Waiting is what may change
            # that, and none of it is about when - a dongle can be pulled years
            # in, and open() itself issues requests, so either group can arrive
            # from either place
            report(e)
            time.sleep(CONNECT_RETRY_DELAY)


def automatic_recovery():
    """Put the backoff in reopen_delays, and there is nothing left to catch.

    A schedule ending in repeat() never runs out, so MomongaNeedToReopen never
    reaches the caller - the library keeps rebuilding the session on its own,
    waiting longer each time. Catching it here to wait and try again would
    only be the same schedule written twice.

    What is left is the failures reopen_delays does not cover. A session that
    could never be established is a MomongaConnectionFailure out of open(),
    and open() is outside its scope.
    """
    while True:
        try:
            with momonga.Momonga(rbid, pwd, dev, reopen_delays=backoff) as mo:
                read_forever(mo)
        except momonga.MomongaConnectionFailure as e:
            report(e)
            time.sleep(CONNECT_RETRY_DELAY)


def no_handler_at_all():
    """The arguments can be set so that there is nothing left to catch.

    A reopen_delays that never runs out means no MomongaNeedToReopen ever
    reaches here; the library keeps rebuilding the session, waiting longer
    each time. What is left is a first connect that fails anyway, and under
    systemd or `docker run --restart=always` the right answer to that is to
    stop: the process exits non-zero on the uncaught exception and comes back
    with a new interpreter and a new handle on the serial port.

    Both counts are raised, because on a link where connecting is hard either
    half can be the one that needs the attempts. What they cost differs, and
    that is what to size them by: a join attempt is about 40 s every time, so
    join_retries is 40 s each; a scan runs 17.5 s, then 34.7 s, then 69.1 s
    and stays there, so the first three cost 2 minutes and each one after
    costs 69 s. The numbers here give open() about 15 minutes before it gives
    up and the supervisor starts a new process.

    That is also the whole of the wait. A supervisor restarts at once unless
    told otherwise - systemd's RestartSec defaults to 100 ms - so these two
    counts are what decides how often a link that is down gets tried again,
    not the restart policy.

    Two things are given up for that.

    One unreadable response ends the process too. MomongaResponseNotExpected is
    rare but not impossible, and a restart costs a scan and a join, which is a
    lot to pay for one bad frame. The loop below is read_forever() with its one
    try/except taken out, so calling read_forever(mo) instead is how to put
    that single handler back and leave everything else as it is.

    And a schedule that never runs out means a meter that has gone for good is
    retried quietly for as long as the process lives, with nothing outside it
    learning. Bound reopen_delays instead if somebody is watching for the
    process to stop.
    """
    with momonga.Momonga(rbid, pwd, dev,
                         reopen_delays=backoff,
                         scan_retries=6, join_retries=15) as mo:
        while True:
            res = mo.get_instantaneous_power()
            print('no data' if res is None else '%0.1fW' % res)
            time.sleep(60)


EXAMPLES = {'manual': manual_recovery,
            'automatic': automatic_recovery,
            'none': no_handler_at_all}


if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'manual'
    if name not in EXAMPLES:
        print('usage: %s [%s]' % (sys.argv[0], '|'.join(EXAMPLES)), file=sys.stderr)
        sys.exit(1)
    EXAMPLES[name]()
