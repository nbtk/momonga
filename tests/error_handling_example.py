"""Keeping a meter read when things fail, three ways.

    manual        you rebuild the session yourself
    automatic     reopen_delays rebuilds it; connecting is still yours
    none          reopen_delays never gives up, so nothing is left to catch

Which exception means what is in the README under Exception. What is here is
what to do about each.

Run:
  MOMONGA_ROUTEB_ID=... MOMONGA_ROUTEB_PASSWORD=... MOMONGA_DEV_PATH=... \
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

# A connect that failed has just spent minutes scanning or joining. Going
# straight round again spends radio time and changes nothing.
CONNECT_RETRY_DELAY = 600.0


def backoff():
    """A fresh schedule per session: a minute, two, five, then every ten."""
    # passed to Momonga as the function, not its result - one chain object
    # given to a second Momonga carries on from where the first left it
    return chain([60.0, 120.0, 300.0], repeat(600.0))


def report(e):
    print('%s: %s' % (type(e).__name__, e), file=sys.stderr)


def read_forever(mo):
    """Read every minute, surviving a response that cannot be read."""
    while True:
        try:
            res = mo.get_instantaneous_power()
        except momonga.MomongaResponseNotExpected as e:
            report(e)  # one frame that could not be read, not a lost session
        else:
            print('no data' if res is None else '%0.1fW' % res)
        time.sleep(60)


def manual_recovery():
    """No reopen_delays, so every failure arrives here."""
    while True:
        try:
            with momonga.Momonga(rbid, pwd, dev) as mo:
                read_forever(mo)
        except momonga.MomongaNeedToReopen as e:
            report(e)  # the module is answering; a new session is worth having
        except momonga.MomongaConnectionFailure as e:
            report(e)  # the module, the port or the radio is not
            time.sleep(CONNECT_RETRY_DELAY)


def automatic_recovery():
    """reopen_delays rebuilds the session; connecting is still yours.

    A schedule ending in repeat() never runs out, so MomongaNeedToReopen never
    arrives here. open() is outside its scope, so MomongaConnectionFailure does.
    """
    while True:
        try:
            with momonga.Momonga(rbid, pwd, dev, reopen_delays=backoff) as mo:
                read_forever(mo)
        except momonga.MomongaConnectionFailure as e:
            report(e)
            time.sleep(CONNECT_RETRY_DELAY)


def no_handler_at_all():
    """Let the arguments do all of it, and let a supervisor restart.

    Nothing is caught: an uncaught exception exits non-zero, and systemd or
    `docker run --restart=always` starts a new process with a new handle on the
    port. The retry counts are the whole of the wait - RestartSec defaults to
    100 ms - and these give open() about 15 minutes before it gives up.

    What that costs: one unreadable response ends the process too, and a meter
    gone for good is retried silently for as long as the process lives. Call
    read_forever(mo) for the first; bound reopen_delays for the second.
    """
    with momonga.Momonga(rbid, pwd, dev,
                         reopen_delays=backoff,
                         scan_retries=6, join_retries=15) as mo:
        while True:  # read_forever() without its one handler
            res = mo.get_instantaneous_power()
            print('no data' if res is None else '%0.1fW' % res)
            time.sleep(60)


EXAMPLES = {'manual': manual_recovery,
            'automatic': automatic_recovery,
            'none': no_handler_at_all}


if __name__ == '__main__':
    if not all((rbid, pwd, dev)):
        print('Please set MOMONGA_ROUTEB_ID, MOMONGA_ROUTEB_PASSWORD, and '
              'MOMONGA_DEV_PATH environment variables.', file=sys.stderr)
        sys.exit(1)

    name = sys.argv[1] if len(sys.argv) > 1 else 'automatic'
    if name not in EXAMPLES:
        print('usage: %s [%s]' % (sys.argv[0], '|'.join(EXAMPLES)), file=sys.stderr)
        sys.exit(1)
    EXAMPLES[name]()
