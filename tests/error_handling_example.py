import momonga
import os
import sys


rbid = os.environ.get('MOMONGA_ROUTEB_ID')
pwd = os.environ.get('MOMONGA_ROUTEB_PASSWORD')
dev = os.environ.get('MOMONGA_DEV_PATH')

while True:
    try:
        with momonga.Momonga(rbid, pwd, dev) as mo:
            res = mo.get_instantaneous_power()
            print('%0.1fW' % res)
            break
    except (momonga.MomongaSkScanFailure,
            momonga.MomongaSkJoinFailure,
            momonga.MomongaNeedToReopen) as e:
        print('%s: %s' % (type(e).__name__, e), file=sys.stderr)
        continue
