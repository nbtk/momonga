"""
open() and close(), over and over.

Each open starts a publisher and a receiver and builds fresh queues; each close
is supposed to take them all back. Nothing here shows up in a single call - it
shows up on the hundredth, in a process that has been reconnecting for days,
which is the way this library is used.

Run:
  python -m unittest tests/test_no_accumulation_unit.py -v
"""
import gc
import queue
import threading
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga
from momonga.momonga_session_manager import MomongaSessionManager
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

CYCLES = 200


def _fake_port_open(self):
    """What MomongaSkWrapper.open does, minus the serial port: the publisher is
    real, because it is the thread most likely to be left behind."""
    self._ser = MagicMock()
    self._ser.closed = False
    self._ser.readline.return_value = b''
    for q in list(self.subscribers.values()):
        while not q.empty():
            q.get()
    self._publisher_th_breaker = False
    self.publisher_exception = None
    self._cancelled = False
    self._publisher_th = threading.Thread(target=self.received_packet_publisher,
                                          daemon=True)
    self._publisher_th.start()
    return self


class TestReconnectingDoesNotPileUp(TimeBoxedTestCase):

    MAX_SECONDS = 60  # CYCLES open/close rounds, ~6 s here and slower on CI

    def setUp(self):
        patches = [
            patch.object(MomongaSkWrapper, 'open', _fake_port_open),
            patch.object(MomongaSkWrapper, 'skreset'),
            patch.object(MomongaSkWrapper, 'sksreg'),
            patch.object(MomongaSkWrapper, 'sksetrbid'),
            patch.object(MomongaSkWrapper, 'sksetpwd'),
            patch.object(MomongaSkWrapper, 'skjoin'),
            patch.object(MomongaSkWrapper, 'skterm'),
            patch.object(Momonga, '_init_energy_unit'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        scan = patch.object(MomongaSkWrapper, 'skscan').start()
        scan.return_value = MagicMock(mac_addr=b'\x00' * 8, channel=0x21,
                                      pan_id=b'\xdd\x5b')
        ll64 = patch.object(MomongaSkWrapper, 'skll64').start()
        ll64.return_value = MagicMock(ip6_addr='FE80::1')
        self.addCleanup(patch.stopall)

        self.mo = Momonga('', '', '/dev/ttyUSB0')
        self.mo.internal_xmit_interval = 0
        self.mo.open()
        self.mo.close()      # warm up whatever is built once

    @staticmethod
    def _census():
        gc.collect()
        objs = gc.get_objects()
        return {
            'threads': threading.active_count(),
            'session managers': sum(1 for o in objs
                                    if isinstance(o, MomongaSessionManager)),
            'wrappers': sum(1 for o in objs if isinstance(o, MomongaSkWrapper)),
            'queues': sum(1 for o in objs if isinstance(o, queue.Queue)),
        }

    def test_nothing_is_left_over_after_many_reconnections(self):
        before = self._census()
        for _ in range(CYCLES):
            self.mo.open()
            self.mo.close()
        after = self._census()
        for what in before:
            with self.subTest(what=what):
                self.assertLessEqual(
                    after[what], before[what],
                    '%s went from %d to %d over %d open/close cycles'
                    % (what, before[what], after[what], CYCLES))

    def test_the_publisher_is_taken_back_every_time(self):
        before = threading.active_count()
        for _ in range(CYCLES):
            self.mo.open()
            self.assertIsNotNone(self.mo.session_manager.skw._publisher_th)
            self.mo.close()
            self.assertIsNone(self.mo.session_manager.skw._publisher_th)
        self.assertLessEqual(threading.active_count(), before)


if __name__ == '__main__':
    unittest.main()
