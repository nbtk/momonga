"""
Unit tests for dispatching while the subscriber set is being changed.

Run:
  python -m unittest tests/test_subscriber_mutation_unit.py -v
"""
import queue
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import serial

from momonga.momonga_sk_wrapper import MomongaSkWrapper, PUBLISHER_STOPPED
from tests._timebox import TimeBoxedTestCase


def _make_skw():
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    skw._ser.readline.return_value = b'EVENT 21 FE80::1 0 00\r\n'
    return skw


class TestPublisherSurvivesSubscriberChanges(TimeBoxedTestCase):

    def test_registering_and_removing_does_not_kill_it(self):
        # session_manager.open() registers pkt_sbsc_q and close() removes it, both
        # while the publisher is running
        skw = _make_skw()
        original = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            publisher = threading.Thread(target=skw.received_packet_publisher, daemon=True)
            publisher.start()

            pkt_sbsc_q = queue.Queue()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and skw.publisher_exception is None:
                skw.subscribers['pkt_sbsc_q'] = pkt_sbsc_q
                time.sleep(0)
                skw.subscribers.pop('pkt_sbsc_q')
                time.sleep(0)

            skw._publisher_th_breaker = True
            publisher.join(5)
        finally:
            sys.setswitchinterval(original)

        self.assertIsNone(skw.publisher_exception)
        self.assertFalse(publisher.is_alive())


class _RegistersAnotherWhenTouched(queue.Queue):
    """Adds a subscriber the first time the loop looks at this one.

    The race above only covers the dispatch loop, and only when the timing
    happens to land. The other two loops over subscribers - the drain in
    open() and the stop announced from the publisher's except block - can
    drop their list() copy with nothing noticing.
    """

    def __init__(self, subscribers, trigger):
        super().__init__()
        self._subscribers = subscribers
        self._trigger = trigger
        self._fired = False

    def _register_one_more(self):
        if not self._fired:
            self._fired = True
            self._subscribers['late'] = queue.Queue()

    def empty(self):
        if self._trigger == 'empty':
            self._register_one_more()
        return super().empty()

    def put(self, item, *args, **kwargs):
        if self._trigger == 'put':
            self._register_one_more()
        return super().put(item, *args, **kwargs)


class TestEveryLoopOverSubscribersTakesACopy(TimeBoxedTestCase):

    @staticmethod
    def _two_subscribers(skw, trigger):
        """One that grows the set when read, and one queued behind it."""
        skw.subscribers['grows'] = _RegistersAnotherWhenTouched(skw.subscribers, trigger)
        behind = queue.Queue()
        skw.subscribers['behind'] = behind
        return behind

    def test_the_drain_in_open_reaches_the_ones_behind_it(self):
        skw = _make_skw()
        behind = self._two_subscribers(skw, 'empty')
        behind.put('left over from the last session')

        with patch.object(MomongaSkWrapper, '_clear_buf'), \
             patch.object(MomongaSkWrapper, '_exec_ropt', return_value=1), \
             patch.object(MomongaSkWrapper, 'detect_device'), \
             patch.object(MomongaSkWrapper, 'received_packet_publisher'), \
             patch('momonga.momonga_sk_wrapper.serial.Serial'):
            skw.open()
            skw.close()

        self.assertTrue(behind.empty())

    def test_the_stop_announcement_reaches_the_ones_behind_it(self):
        skw = _make_skw()
        behind = self._two_subscribers(skw, 'put')
        skw._ser.readline.side_effect = serial.SerialException('the port went away')

        publisher = threading.Thread(target=skw.received_packet_publisher, daemon=True)
        publisher.start()
        publisher.join(5)

        self.assertFalse(publisher.is_alive())
        self.assertIs(behind.get_nowait(), PUBLISHER_STOPPED)


if __name__ == '__main__':
    unittest.main()
