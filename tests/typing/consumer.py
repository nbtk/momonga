"""What a caller must be able to type-check against the published package.

Built and installed the way a user would get it, then checked with --strict:
the annotations have to be visible (py.typed and __all__), the return types
have to be the declared ones, and a mistake has to be caught rather than
waved through as Any.
"""
import datetime

import momonga

mo = momonga.Momonga('id', 'pw', '/dev/ttyUSB0')

power: int | float | None = mo.get_instantaneous_power()
if power is not None:
    watts: float = round(power, 2)

when: datetime.time = mo.get_current_time_setting()
serial: str = mo.get_serial_number()

amo = momonga.AsyncMomonga('id', 'pw', '/dev/ttyUSB0')

try:
    mo.get_instantaneous_power()
except momonga.MomongaNeedToReopen:
    pass
except momonga.MomongaConnectionFailure:
    pass
