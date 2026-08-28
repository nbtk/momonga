"""How a publisher notices the port is no longer its own.

open() puts a new Serial object on the wrapper and close() puts None there, so
a publisher left over from an earlier session sees an object that is not the
one it started with and stops - quietly, without recording an exception or
telling the subscribers the publisher died, because nothing went wrong.

That check is an identity comparison against whatever `_ser` holds, `None`
included. Routing it through an accessor that refuses to hand back a closed
port turns an ordinary shutdown into an exception, and then into a second one
inside the handler, so the publisher neither records why it stopped nor sends
PUBLISHER_STOPPED. The 790 tests all passed while it did.

Run:
  python -m unittest tests/test_publisher_port_swap_unit.py -v
"""
import threading
import time
import unittest

from unittest.mock import MagicMock

from momonga.momonga_sk_wrapper import MomongaSkWrapper, PUBLISHER_STOPPED

from tests._timebox import TimeBoxedTestCase


class _Publisher(TimeBoxedTestCase):

    def _running(self):
        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = MagicMock()
        skw._readline = lambda timeout=None: (time.sleep(0.01), '')[1]
        thread = threading.Thread(target=skw.received_packet_publisher, daemon=True)
        thread.start()
        self.addCleanup(self._stop, skw, thread)
        self._settle(lambda: thread.is_alive())
        return skw, thread

    def _stop(self, skw, thread):
        skw._publisher_th_breaker = True
        thread.join(5)

    def _settle(self, predicate, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False


class TestAClosedPortStopsThePublisherQuietly(_Publisher):
    """threading.excepthook is the only way to see this from outside: the
    publisher catches everything and records it, so an exception raised by the
    check itself - or by the handler that runs afterwards - leaves the same
    stopped thread, empty queue and empty publisher_exception as an orderly
    shutdown does."""

    def test_the_thread_does_not_die_of_an_exception(self):
        raised = []
        original = threading.excepthook
        threading.excepthook = lambda args: raised.append(args.exc_type)
        self.addCleanup(setattr, threading, 'excepthook', original)

        skw, thread = self._running()
        skw._ser = None
        self._settle(lambda: not thread.is_alive())

        self.assertEqual(raised, [])

    def test_it_stops(self):
        skw, thread = self._running()

        skw._ser = None

        self.assertTrue(self._settle(lambda: not thread.is_alive()))

    def test_without_recording_a_failure(self):
        skw, thread = self._running()

        skw._ser = None
        self._settle(lambda: not thread.is_alive())

        self.assertIsNone(skw.publisher_exception)

    def test_and_without_telling_the_subscribers_it_died(self):
        skw, thread = self._running()

        skw._ser = None
        self._settle(lambda: not thread.is_alive())

        self.assertTrue(skw.subscribers['cmd_exec_q'].empty())


class TestAReplacedPortStopsTheOldPublisherToo(_Publisher):
    """What open() does on a reopen: a new Serial, not None."""

    def test_it_stops(self):
        skw, thread = self._running()

        skw._ser = MagicMock()

        self.assertTrue(self._settle(lambda: not thread.is_alive()))

    def test_quietly(self):
        skw, thread = self._running()

        skw._ser = MagicMock()
        self._settle(lambda: not thread.is_alive())

        self.assertIsNone(skw.publisher_exception)
        self.assertTrue(skw.subscribers['cmd_exec_q'].empty())


class TestARealFailureIsStillReported(_Publisher):
    """The quiet path is only for a port that is no longer ours."""

    def test_the_exception_is_kept_and_the_subscribers_told(self):
        skw, thread = self._running()

        skw._readline = MagicMock(side_effect=RuntimeError('the port went away'))
        self._settle(lambda: not thread.is_alive())

        self.assertIsInstance(skw.publisher_exception, RuntimeError)
        self.assertIs(skw.subscribers['cmd_exec_q'].get_nowait(), PUBLISHER_STOPPED)


class TestComparingAgainstAMeterNotFoundYet(TimeBoxedTestCase):
    """smart_meter_addr is None until a scan fills it in, and two places
    compare a frame's source against it. Those have to stay ordinary
    comparisons: an accessor that refuses to hand back an address nobody has
    yet would raise where the answer is simply "not from the meter" - the same
    shape of mistake the port accessor made in the publisher above.
    """

    def _session(self):
        from momonga.momonga_session_manager import MomongaSessionManager
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0')
        sm.skw = MagicMock()
        sm.skw.subscribers = {}
        return sm

    def test_a_frame_arriving_before_a_scan_is_not_a_failure(self):
        from momonga.momonga_response import SkParsedRxUdp
        sm = self._session()
        seen = []
        sm.on_meter_frame = seen.append
        frame = SkParsedRxUdp(src_addr='FE80::9', dst_addr='', src_port=0x0E1A,
                              dst_port=0x0E1A, src_mac=b'\x00' * 8, lqi=0x6C,
                              rssi=-80.0, sec=1, side=0, data=b'')

        self.assertIsNone(sm.smart_meter_addr)
        self.assertNotEqual(frame.src_addr, sm.smart_meter_addr)
        self.assertEqual(seen, [])

    def test_a_request_before_a_scan_says_so_rather_than_crashing(self):
        import momonga
        sm = self._session()

        with self.assertRaises(momonga.MomongaError):
            sm._meter_addr

    def test_once_a_scan_has_run_the_address_is_handed_over(self):
        sm = self._session()
        sm.smart_meter_addr = 'FE80::1'

        self.assertEqual(sm._meter_addr, 'FE80::1')


if __name__ == '__main__':
    unittest.main()
