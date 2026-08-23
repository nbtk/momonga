"""
Unit tests for truncated ECHONET frames reaching the parsers.

Run:
  python -m unittest tests/test_truncated_frame_unit.py -v
"""
import queue
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_echonet_data import EchonetProperty
from momonga.momonga_echonet_enum import EchonetPropertyCode
from momonga.momonga_exception import MomongaResponseNotExpected
from momonga.momonga_response import SkParsedRxUdp
from tests._timebox import TimeBoxedTestCase

EXTRACT = '_extract_response_payload'
HEADER = b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01'  # EHD TID SEOJ DEOJ


def _notification(*, opc: int, properties: bytes) -> SkParsedRxUdp:
    data = HEADER + b'\x73' + opc.to_bytes(1, 'big') + properties
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='FE80::2', src_port=3610, dst_port=3610,
                         src_mac=b'\x00' * 8, side=0, sec=1, data=data)


def _response(*, tid: int, opc: int, properties: bytes) -> bytes:
    return (b'\x10\x81' + tid.to_bytes(2, 'big') + b'\x02\x88\x01\x05\xff\x01'
            + b'\x72' + opc.to_bytes(1, 'big') + properties)


def _make_mo():
    mo = Momonga('', '', '/dev/ttyUSB0')
    mo.is_open = True
    mo.session_manager = MagicMock()
    mo.session_manager.notif_q = queue.Queue()
    return mo


class TestTruncatedNotification(TimeBoxedTestCase):

    def _get(self, frame):
        mo = _make_mo()
        mo.session_manager.notif_q.put(frame)
        return mo.get_notification(timeout=1)

    def test_epc_cut_off_is_discarded(self):
        # OPC says 1 property, but the frame ends right after OPC
        self.assertIsNone(self._get(_notification(opc=1, properties=b'')))

    def test_pdc_cut_off_is_discarded(self):
        # EPC present, PDC missing
        self.assertIsNone(self._get(_notification(opc=1, properties=b'\xe7')))

    def test_edt_shorter_than_pdc_is_discarded(self):
        # PDC claims 4 bytes of EDT, only 2 follow
        self.assertIsNone(self._get(_notification(opc=1, properties=b'\xe7\x04\x00\x00')))

    def test_second_property_cut_off_is_discarded(self):
        first = b'\xe7\x04\x00\x00\x00\x64'
        self.assertIsNone(self._get(_notification(opc=2, properties=first + b'\xe8')))

    def test_a_complete_notification_is_still_parsed(self):
        notif = self._get(_notification(opc=1, properties=b'\xe7\x04\x00\x00\x00\x64'))
        self.assertIsNotNone(notif)
        self.assertEqual(notif['properties'][EchonetPropertyCode.instantaneous_power], 100)

    def test_no_infc_res_is_sent_for_a_truncated_frame(self):
        mo = _make_mo()
        data = HEADER + b'\x74\x01\xe7'  # INFC, OPC 1, PDC missing
        mo.session_manager.notif_q.put(
            SkParsedRxUdp(src_addr='FE80::1', dst_addr='FE80::2', src_port=3610, dst_port=3610,
                          src_mac=b'\x00' * 8, side=0, sec=1, data=data))

        self.assertIsNone(mo.get_notification(timeout=1))
        mo.session_manager.xmitter.assert_not_called()


class TestTruncatedResponse(TimeBoxedTestCase):

    def _extract(self, data, tid=1, epc=EchonetPropertyCode.instantaneous_power):
        return getattr(Momonga, EXTRACT)(data, tid, [EchonetProperty(epc)])

    def test_short_header_is_rejected(self):
        with self.assertRaises(MomongaResponseNotExpected):
            self._extract(b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01')

    def test_pdc_cut_off_is_rejected(self):
        with self.assertRaises(MomongaResponseNotExpected):
            self._extract(_response(tid=1, opc=1, properties=b'\xe7'))

    def test_edt_shorter_than_pdc_is_rejected(self):
        with self.assertRaises(MomongaResponseNotExpected):
            self._extract(_response(tid=1, opc=1, properties=b'\xe7\x04\x00\x00'))

    def test_a_complete_response_is_still_parsed(self):
        res = self._extract(_response(tid=1, opc=1, properties=b'\xe7\x04\x00\x00\x00\x64'))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].edt, b'\x00\x00\x00\x64')


if __name__ == '__main__':
    unittest.main()
