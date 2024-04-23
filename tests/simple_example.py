import momonga
import os


rbid = os.environ.get('MOMONGA_ROUTEB_ID')
pwd = os.environ.get('MOMONGA_ROUTEB_PASSWORD')
dev = os.environ.get('MOMONGA_DEV_PATH')

with momonga.Momonga(rbid, pwd, dev) as mo:
    res = mo.get_instantaneous_power()
    print('%0.1fW' % res)
