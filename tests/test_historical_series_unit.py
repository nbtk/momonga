"""
The timestamps the three historical series carry.

Each parser walks a clock alongside the readings - forward for series 1,
backwards for 2 and 3 - and the direction and the step size are the whole
meaning of the data. Nothing checked either: mutation testing turned series 1
around and cut series 2's step to a minute with the suite still green, because
the existing tests read the energy values and left the timestamps alone.

Run:
  python -m unittest tests/test_historical_series_unit.py -v
"""
import datetime
import unittest

from momonga.momonga_echonet_data import EchonetDataParser
from tests._timebox import TimeBoxedTestCase

UNIT, COEFFICIENT = 0.1, 1
MISSING = 0xFFFFFFFE


def _series_1(day: int, values: list[int]) -> bytes:
    assert len(values) == 48
    return day.to_bytes(2, 'big') + b''.join(v.to_bytes(4, 'big') for v in values)


def _series_2_or_3(when: datetime.datetime, pairs: list[tuple[int, int]]) -> bytes:
    return (when.year.to_bytes(2, 'big')
            + bytes([when.month, when.day, when.hour, when.minute, len(pairs)])
            + b''.join(n.to_bytes(4, 'big') + r.to_bytes(4, 'big') for n, r in pairs))


class TestSeriesOneWalksForwardThroughItsDay(TimeBoxedTestCase):

    def setUp(self):
        self.points = EchonetDataParser.parse_historical_cumulative_energy_1(
            _series_1(2, list(range(48))), UNIT, COEFFICIENT)

    def test_it_starts_at_midnight_of_the_day_asked_for(self):
        midnight = datetime.datetime.combine(
            datetime.date.today() - datetime.timedelta(days=2),
            datetime.datetime.min.time())
        self.assertEqual(self.points[0]['timestamp'], midnight)

    def test_each_point_is_half_an_hour_after_the_one_before(self):
        steps = {b['timestamp'] - a['timestamp']
                 for a, b in zip(self.points, self.points[1:])}
        self.assertEqual(steps, {datetime.timedelta(minutes=30)})

    def test_the_forty_eight_points_cover_the_day_and_stop(self):
        span = self.points[-1]['timestamp'] - self.points[0]['timestamp']
        self.assertEqual(span, datetime.timedelta(hours=23, minutes=30))

    def test_a_missing_reading_is_none_and_keeps_its_slot(self):
        values = list(range(48))
        values[3] = MISSING
        points = EchonetDataParser.parse_historical_cumulative_energy_1(
            _series_1(0, values), UNIT, COEFFICIENT)

        self.assertIsNone(points[3]['cumulative energy'])
        self.assertEqual(points[3]['timestamp'] - points[0]['timestamp'],
                         datetime.timedelta(minutes=90))


class TestSeriesTwoWalksBackInHalfHours(TimeBoxedTestCase):

    WHEN = datetime.datetime(2026, 8, 23, 12, 0)

    def setUp(self):
        self.points = EchonetDataParser.parse_historical_cumulative_energy_2(
            _series_2_or_3(self.WHEN, [(i, i * 2) for i in range(6)]), UNIT, COEFFICIENT)

    def test_it_starts_at_the_time_that_was_asked_for(self):
        self.assertEqual(self.points[0]['timestamp'], self.WHEN)

    def test_each_point_is_half_an_hour_before_the_one_after(self):
        steps = {b['timestamp'] - a['timestamp']
                 for a, b in zip(self.points, self.points[1:])}
        self.assertEqual(steps, {datetime.timedelta(minutes=-30)})

    def test_it_reads_both_directions_at_each_point(self):
        self.assertEqual(self.points[3]['cumulative energy'],
                         {'normal direction': 3 * UNIT, 'reverse direction': 6 * UNIT})


class TestSeriesThreeWalksBackInMinutes(TimeBoxedTestCase):

    WHEN = datetime.datetime(2026, 8, 23, 12, 0)

    def setUp(self):
        self.points = EchonetDataParser.parse_historical_cumulative_energy_3(
            _series_2_or_3(self.WHEN, [(i, i * 2) for i in range(10)]), UNIT, COEFFICIENT)

    def test_it_starts_at_the_time_that_was_asked_for(self):
        self.assertEqual(self.points[0]['timestamp'], self.WHEN)

    def test_each_point_is_a_minute_before_the_one_after(self):
        steps = {b['timestamp'] - a['timestamp']
                 for a, b in zip(self.points, self.points[1:])}
        self.assertEqual(steps, {datetime.timedelta(minutes=-1)})

    def test_ten_points_span_nine_minutes_not_hours(self):
        span = self.points[0]['timestamp'] - self.points[-1]['timestamp']
        self.assertEqual(span, datetime.timedelta(minutes=9))


if __name__ == '__main__':
    unittest.main()
