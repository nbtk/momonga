"""Mistakes the published package must catch, one per line.

Each line is expected to fail; the CI step asserts the count, so a checker
that has stopped seeing momonga's types fails here rather than passing
silently.
"""
import momonga

mo = momonga.Momonga('id', 'pw', '/dev/ttyUSB0')

round(mo.get_instantaneous_power(), 2)                    # int | None, not a number
mo.get_historical_cumulative_energy_1(day='yesterday')    # day is an int
mo.get_nonexistent_thing()                                # no such method
when: int = mo.get_current_time_setting()                 # returns datetime.time
