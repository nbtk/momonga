"""
What the request loop does with the events that arrive before the reply.

EVENT 21 is the module reporting whether the frame went out at all, and the
whole retransmit decision hangs off its parameter: 00 keeps waiting, 01 sends
the frame again, 02 means neighbour discovery is still running. Coverage found
the entire block unexecuted - every existing test answers a request with a
reply and nothing else - so the difference between "sent" and "not sent" was
never exercised.

Run:
  python -m unittest tests/test_request_loop_unit.py -v
"""
import queue
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_echonet_enum import (CONTROLLER_EOJ, ECHONET_LITE_EHD,
                                          ECHONET_LITE_PORT, SMART_METER_EOJ)
from momonga.momonga_exception import MomongaNeedToReopen
from momonga.momonga_response import SkParsedEvent, SkParsedRxUdp
from tests._timebox import TimeBoxedTestCase

POWER = -250

TX_DONE, NEIGHBOR_DISCOVERY = 0x21, 0x02
SENT, NOT_SENT, SOLICITING = 0x00, 0x01, 0x02


def _event(num, param=None):
    return SkParsedEvent(num=num, src_addr='FE80::1', side=0, param=param)


def _reply(tid: bytes) -> SkParsedRxUdp:
    data = (ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
            + b'\x72\x01\xe7\x04' + POWER.to_bytes(4, 'big', signed=True))
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=ECHONET_LITE_PORT,
                         dst_port=ECHONET_LITE_PORT, src_mac=b'', side=0, sec=2, data=data)


class _AModuleThatReportsItsSends(TimeBoxedTestCase):
    """`script` is what lands on recv_q for each attempt, in order."""

    def setUp(self):
        sm = MagicMock()
        sm.recv_q = queue.Queue()
        sm.smart_meter_addr = 'FE80::1'
        self.attempts = []
        self.script = []

        def xmitter(payload, timeout=None):
            tid = payload[2:4]
            self.attempts.append(tid)
            i = min(len(self.attempts) - 1, len(self.script) - 1)
            for item in self.script[i]:
                sm.recv_q.put(_reply(tid) if item == 'reply' else item)

        sm.xmitter = xmitter
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 3, 0.2, 0
        self.mo, self.sm = mo, sm


class TestTheTransmitResultDecidesWhetherToSendAgain(_AModuleThatReportsItsSends):

    def test_a_successful_send_is_followed_by_waiting_for_the_reply(self):
        self.script = [[_event(TX_DONE, SENT), 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)
        self.assertEqual(len(self.attempts), 1)

    def test_a_failed_send_is_repeated_without_waiting_out_the_reply(self):
        self.script = [[_event(TX_DONE, NOT_SENT)], [_event(TX_DONE, SENT), 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)
        self.assertEqual(len(self.attempts), 2)

    def test_a_repeat_carries_the_same_transaction_id(self):
        self.script = [[_event(TX_DONE, NOT_SENT)], [_event(TX_DONE, SENT), 'reply']]

        self.mo.get_instantaneous_power()

        self.assertEqual(len(set(self.attempts)), 1)

    def test_neighbour_solicitation_is_not_a_failure(self):
        self.script = [[_event(TX_DONE, SOLICITING), _event(TX_DONE, SENT), 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)
        self.assertEqual(len(self.attempts), 1)

    def test_an_unknown_transmit_result_is_ignored_rather_than_retried(self):
        self.script = [[_event(TX_DONE, 0x7F), 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)
        self.assertEqual(len(self.attempts), 1)

    def test_a_neighbour_advertisement_is_ignored(self):
        self.script = [[_event(NEIGHBOR_DISCOVERY), 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)

    def test_any_other_event_is_ignored(self):
        self.script = [[_event(0x33), _event(0x25), 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)

    def test_every_send_failing_uses_up_the_retries_and_gives_up(self):
        self.script = [[_event(TX_DONE, NOT_SENT)]]

        with self.assertRaises(MomongaNeedToReopen):
            self.mo.get_instantaneous_power()

        self.assertEqual(len(self.attempts), 3)  # xmit_retries


class TestSomethingElseOnTheQueueIsNotAReply(_AModuleThatReportsItsSends):

    def test_a_frame_that_is_neither_event_nor_udp_is_passed_over(self):
        self.script = [[object(), 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)

    def test_a_udp_frame_on_another_port_is_passed_over(self):
        stray = SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=0x0E1B,
                              dst_port=0x0E1B, src_mac=b'', side=0, sec=2, data=b'')
        self.script = [[stray, 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)

    def test_a_udp_frame_from_the_other_side_is_passed_over(self):
        stray = SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=ECHONET_LITE_PORT,
                              dst_port=ECHONET_LITE_PORT, src_mac=b'', side=1, sec=2,
                              data=b'')
        self.script = [[stray, 'reply']]

        self.assertEqual(self.mo.get_instantaneous_power(), POWER)


if __name__ == '__main__':
    unittest.main()
