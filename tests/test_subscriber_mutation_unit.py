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
from unittest.mock import MagicMock

from momonga.momonga_sk_wrapper import MomongaSkWrapper


def _make_skw():
    skw = object.__new__(MomongaSkWrapper)
    skw.subscribers = {'cmd_exec_q': queue.Queue()}
    skw.publisher_th_breaker = False
    skw.publisher_exception = None
    skw.ser = MagicMock()
    skw.ser.readline.return_value = b'EVENT 21 FE80::1 0 00\r\n'
    return skw


class TestPublisherSurvivesSubscriberChanges(unittest.TestCase):

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

            skw.publisher_th_breaker = True
            publisher.join(5)
        finally:
            sys.setswitchinterval(original)

        self.assertIsNone(skw.publisher_exception)
        self.assertFalse(publisher.is_alive())


if __name__ == '__main__':
    unittest.main()
