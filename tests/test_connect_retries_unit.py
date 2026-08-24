"""
How many times connecting is attempted before open() gives up.

skscan and skjoin have taken a retry count all along; open() called them with
neither, so three was the only answer available. Ten months of collector logs
say three is often not enough: 30 scan failures and 95 join failures against
39 sessions that did eventually open, one of them only after the caller had
been round the loop 45 times. Every one of those rounds re-scanned a PAN it
had already found, because the outer loop is the only place a retry could
happen.

Run:
  python -m unittest tests/test_connect_retries_unit.py -v
"""
import unittest
from unittest.mock import MagicMock, patch

import momonga
from momonga.momonga import Momonga
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

SCAN_HIT = ['EPANDESC', '  Channel:21', '  Channel Page:09', '  Pan ID:8888',
            '  Addr:AABBCCDDEEFF0011', '  LQI:6E', '  Side:0', '  PairID:12345678']


def _session_manager(**kwargs):
    sm = MomongaSessionManager('rbid', 'pwd', '/dev/ttyUSB0', reset_dev=False, **kwargs)
    sm.skw = MagicMock()
    sm.skw.subscribers = {}
    sm.skw.skscan.return_value = MagicMock(mac_addr=b'\x00' * 8, channel=0x21,
                                           pan_id=b'\x88\x88')
    sm.skw.skll64.return_value = MagicMock(ip6_addr='FE80::1')
    return sm


class TestTheCountReachesTheCommands(TimeBoxedTestCase):

    def test_the_default_is_what_it_always_was(self):
        sm = _session_manager()
        sm.open()

        self.assertEqual(sm.skw.skscan.call_args.kwargs['retry'], 3)
        self.assertEqual(sm.skw.skjoin.call_args.kwargs['retry'], 3)

    def test_a_higher_scan_count_is_passed_on(self):
        sm = _session_manager(scan_retries=7)
        sm.open()

        self.assertEqual(sm.skw.skscan.call_args.kwargs['retry'], 7)

    def test_a_higher_join_count_is_passed_on(self):
        sm = _session_manager(join_retries=15)
        sm.open()

        self.assertEqual(sm.skw.skjoin.call_args.kwargs['retry'], 15)

    def test_the_two_are_independent(self):
        sm = _session_manager(scan_retries=2, join_retries=9)
        sm.open()

        self.assertEqual(sm.skw.skscan.call_args.kwargs['retry'], 2)
        self.assertEqual(sm.skw.skjoin.call_args.kwargs['retry'], 9)


class TestTheCountSurvivesAReopen(TimeBoxedTestCase):
    """reopen() builds a session manager of its own. A setting the second one
    does not get is a setting that quietly stops applying after the first
    outage - which is when it matters most."""

    def _reopened_manager(self, **kwargs):
        built = []

        class Recording(MomongaSessionManager):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                built.append(self)
                self.skw = MagicMock()
                self.skw.subscribers = {}

            def open(self):
                return self

            def close(self):
                pass

        with patch('momonga.momonga.MomongaSessionManager', Recording), \
             patch.object(Momonga, '_init_energy_unit', lambda _self: None), \
             patch('momonga.momonga.time.sleep', lambda _s: None):
            mo = momonga.Momonga('rbid', 'pwd', '/dev/ttyUSB0', **kwargs)
            mo.open()
            mo.reopen()
        return built

    def test_the_rebuilt_manager_keeps_the_counts(self):
        built = self._reopened_manager(scan_retries=7, join_retries=15)

        self.assertGreaterEqual(len(built), 2)
        for sm in built:
            self.assertEqual((sm._scan_retries, sm._join_retries), (7, 15))

    def test_the_defaults_survive_too(self):
        built = self._reopened_manager()

        for sm in built:
            self.assertEqual((sm._scan_retries, sm._join_retries), (3, 3))


class TestTheCommandsHonourTheCount(TimeBoxedTestCase):
    """What the numbers buy, at the wrapper: attempts before giving up."""

    def _attempts(self, method, retry, reply):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        sent = []

        def exec_command(_self, command, expect=None, *args, **kwargs):
            sent.append(command)
            return reply

        with patch.object(MomongaSkWrapper, 'exec_command', exec_command):
            with self.assertRaises(momonga.MomongaError):
                method(skw, retry)
        return len(sent)

    def test_scanning_tries_as_many_times_as_asked(self):
        for retry in (1, 3, 8):
            with self.subTest(retry=retry):
                self.assertEqual(
                    self._attempts(lambda s, r: s.skscan(retry=r), retry,
                                   ['EVENT 22 FE80::1 0']), retry)

    def test_joining_tries_as_many_times_as_asked(self):
        for retry in (1, 3, 15):
            with self.subTest(retry=retry):
                self.assertEqual(
                    self._attempts(lambda s, r: s.skjoin('FE80::1', retry=r), retry,
                                   ['EVENT 24 FE80::1 0']), retry)


class TestACountBelowOneIsRefused(TimeBoxedTestCase):
    """Zero means never attempting to connect and then reporting that the
    connection failed, which reads as a meter that is not there."""

    def test_zero_and_below_are_rejected_at_construction(self):
        for name in ('scan_retries', 'join_retries'):
            for value in (0, -1):
                with self.subTest(**{name: value}):
                    with self.assertRaises(momonga.MomongaValueError):
                        momonga.Momonga('rbid', 'pwd', '/dev/ttyUSB0', **{name: value})

    def test_the_message_names_the_argument(self):
        with self.assertRaises(momonga.MomongaValueError) as caught:
            momonga.Momonga('rbid', 'pwd', '/dev/ttyUSB0', join_retries=0)

        self.assertIn('join_retries', str(caught.exception))

    def test_one_is_allowed(self):
        mo = momonga.Momonga('rbid', 'pwd', '/dev/ttyUSB0',
                             scan_retries=1, join_retries=1)

        self.assertEqual((mo._scan_retries, mo._join_retries), (1, 1))


class TestScanningStopsWidening(TimeBoxedTestCase):
    """SKSCAN's window doubles with each step of DURATION. Widening it once per
    retry with no stop is what turned ten retries into five hours, and only
    three steps of it were ever exercised."""

    def _durations(self, retry):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        seen = []

        def exec_command(_self, command, expect=None, *args, **kwargs):
            seen.append(int(command[3]))       # SKSCAN <mode> <mask> <duration>
            return ['EVENT 22 FE80::1 0']      # never finds the PAN

        with patch.object(MomongaSkWrapper, 'exec_command', exec_command):
            with self.assertRaises(momonga.MomongaSkScanFailure):
                skw.skscan(retry=retry)
        return seen

    def _scan_seconds(self, retry):
        # the estimate skscan is written against
        return sum(0.0096 * (2 ** d + 1) * 28 for d in self._durations(retry))

    def test_the_default_three_are_unchanged(self):
        self.assertEqual(self._durations(3), [6, 7, 8])

    def test_the_widest_window_repeats_rather_than_doubling(self):
        self.assertEqual(self._durations(6), [6, 7, 8, 8, 8, 8])

    def test_the_cost_grows_with_the_count_not_faster(self):
        three, six = self._scan_seconds(3), self._scan_seconds(6)

        self.assertLess(six, three * 3)   # twice the count, under three times the wait

    def test_ten_retries_are_minutes_rather_than_hours(self):
        self.assertLess(self._scan_seconds(10) / 60, 15)

    def test_the_figures_the_readme_quotes(self):
        for retries, minutes in ((3, 2.0), (4, 3.2), (5, 4.3), (6, 5.5),
                                 (8, 7.8), (10, 10.1)):
            with self.subTest(scan_retries=retries):
                self.assertAlmostEqual(self._scan_seconds(retries) / 60, minutes,
                                       delta=0.2)


class TestJoiningCostsTheSameEachTime(TimeBoxedTestCase):

    JOIN_SECONDS = 40  # skjoin's own estimate, per attempt

    def test_it_grows_with_the_count(self):
        self.assertEqual(self.JOIN_SECONDS * 6, self.JOIN_SECONDS * 3 * 2)


class TestTheAsyncWrapperTakesThemToo(TimeBoxedTestCase):

    def test_they_reach_the_sync_object(self):
        from momonga.momonga_async import AsyncMomonga
        amo = AsyncMomonga('rbid', 'pwd', '/dev/ttyUSB0', scan_retries=5, join_retries=11)
        self.addCleanup(lambda: [ex.shutdown(wait=False) for ex in
                                 (amo._executor, amo._notif_executor, amo._life_executor)])

        self.assertEqual(amo._sync._scan_retries, 5)
        self.assertEqual(amo._sync._join_retries, 11)
        self.assertEqual(amo._sync.session_manager._join_retries, 11)


if __name__ == '__main__':
    unittest.main()
