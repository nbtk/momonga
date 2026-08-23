"""
The checks that decide whether a frame off the air is ours.

Every one of them is what stops another device's traffic being read as the
meter's, and mutation testing found the lot of them removable with the suite
still green: the transaction id check, the source object check on replies and
on notifications, and the destination check. Nothing fed the library a frame
it was supposed to turn down.

The shape here is a decoy followed by the real answer. A caller that comes
back with the decoy's value took a frame it should have dropped, which a test
that only ever sends well-formed frames cannot see.

Run:
  python -m unittest tests/test_frame_validation_unit.py -v
"""
import queue
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_echonet_enum import (CONTROLLER_EOJ, ECHONET_LITE_EHD,
                                          ECHONET_LITE_PORT, SMART_METER_EOJ)
from momonga.momonga_exception import (MomongaNeedToReopen,
                                       MomongaResponseNotPossible)
from momonga.momonga_response import SkParsedRxUdp
from tests._timebox import TimeBoxedTestCase

METER_ADDR = 'FE80::1'
DECOY, REAL = 111, 222
OTHER_OBJECT = b'\x02\x87\x01'   # a different ECHONET class
OTHER_CONTROLLER = b'\x05\xff\x02'


def _frame(tid: bytes,
           value: int,
           seoj: bytes = SMART_METER_EOJ,
           deoj: bytes = CONTROLLER_EOJ,
           esv: bytes = b'\x72',
           ) -> bytes:
    return (ECHONET_LITE_EHD + tid + seoj + deoj + esv + b'\x01'
            + b'\xe7\x04' + value.to_bytes(4, 'big', signed=True))


def _rx(data: bytes, src_addr: str = METER_ADDR) -> SkParsedRxUdp:
    return SkParsedRxUdp(src_addr=src_addr, dst_addr='', src_port=ECHONET_LITE_PORT,
                         dst_port=ECHONET_LITE_PORT, src_mac=b'', side=0, sec=2,
                         data=data)


class _Base(TimeBoxedTestCase):

    def setUp(self):
        sm = MagicMock()
        sm.recv_q = queue.Queue()
        sm.notif_q = queue.Queue()
        sm.smart_meter_addr = METER_ADDR
        self.sent = []
        self.script = []

        def xmitter(payload, timeout=None):
            self.sent.append(payload)
            for build in self.script:
                sm.recv_q.put(_rx(build(payload[2:4])))

        sm.xmitter = xmitter
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 1, 0.2, 0
        self.mo, self.sm = mo, sm


class TestAReplyHasToBeForThisRequest(_Base):

    def test_a_reply_carrying_another_transaction_id_is_passed_over(self):
        other = lambda tid: _frame((int.from_bytes(tid, 'big') ^ 0xFFFF).to_bytes(2, 'big'), DECOY)
        self.script = [other, lambda tid: _frame(tid, REAL)]

        self.assertEqual(self.mo.get_instantaneous_power(), REAL)

    def test_a_reply_that_is_only_ever_the_wrong_one_fails_the_request(self):
        self.script = [lambda tid: _frame(b'\x00\x00', DECOY)]

        with self.assertRaises(MomongaNeedToReopen):
            self.mo.get_instantaneous_power()


class TestAReplyHasToBeFromTheMeter(_Base):

    def test_a_reply_from_another_object_is_passed_over(self):
        self.script = [lambda tid: _frame(tid, DECOY, seoj=OTHER_OBJECT),
                       lambda tid: _frame(tid, REAL)]

        self.assertEqual(self.mo.get_instantaneous_power(), REAL)

    def test_a_reply_addressed_to_another_controller_is_passed_over(self):
        self.script = [lambda tid: _frame(tid, DECOY, deoj=OTHER_CONTROLLER),
                       lambda tid: _frame(tid, REAL)]

        self.assertEqual(self.mo.get_instantaneous_power(), REAL)

    def test_a_reply_from_another_address_is_passed_over(self):
        def xmitter(payload, timeout=None):
            tid = payload[2:4]
            self.sm.recv_q.put(_rx(_frame(tid, DECOY), src_addr='FE80::9'))
            self.sm.recv_q.put(_rx(_frame(tid, REAL)))

        self.sm.xmitter = xmitter

        self.assertEqual(self.mo.get_instantaneous_power(), REAL)


class TestARefusalIsNotAReading(_Base):

    def test_the_meter_saying_no_is_raised_not_parsed(self):
        self.script = [lambda tid: _frame(tid, DECOY, esv=b'\x52')]

        with self.assertRaises(MomongaResponseNotPossible):
            self.mo.get_instantaneous_power()


class TestANotificationHasToBeFromTheMeter(_Base):

    def test_a_notification_from_another_object_reaches_neither_queue(self):
        self.mo._route_meter_frame(_rx(_frame(b'\x00\x01', DECOY,
                                              seoj=OTHER_OBJECT, esv=b'\x73')))

        self.assertTrue(self.sm.notif_q.empty())
        self.assertTrue(self.sm.recv_q.empty())

    def test_a_notification_from_the_meter_is_kept(self):
        self.mo._route_meter_frame(_rx(_frame(b'\x00\x01', REAL, esv=b'\x73')))

        self.assertFalse(self.sm.notif_q.empty())


class TestTheAcknowledgementCarriesNoData(_Base):

    def test_infc_res_answers_each_property_with_an_empty_one(self):
        infc = _frame(b'\x00\x05', REAL, esv=b'\x74')
        self.mo._send_infc_res(infc)

        payload, = self.sent
        self.assertEqual(payload[10:11], b'\x7a')     # INFC_Res
        self.assertEqual(payload[11:12], b'\x01')     # one property
        self.assertEqual(payload[12:], b'\xe7\x00')   # that EPC, PDC 0, no EDT


if __name__ == '__main__':
    unittest.main()
