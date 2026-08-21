"""
Unit tests for the link quality the last meter frame arrived with.

Run:
  python -m unittest tests/test_link_quality_unit.py -v
"""
import unittest
from unittest.mock import MagicMock

from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga
from momonga.momonga_response import SkParsedRxUdp

INF_FRAME = b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01\x73\x01\xe7\x04\x00\x00\x03\xe8'
OTHER_OBJECT = b'\x10\x81\x00\x01\x0e\xf0\x01\x05\xff\x01\x73\x01\xe7\x04\x00\x00\x03\xe8'


def _frame(data=INF_FRAME, lqi=0x50, rssi=-82.27, side=0):
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='FE80::2', src_port=3610,
                         dst_port=3610, src_mac=b'', side=side, sec=2,
                         data=data, lqi=lqi, rssi=rssi)


def _make_mo():
    mo = Momonga('', '', '/dev/ttyUSB0')
    mo.session_manager = MagicMock()
    return mo


class TestItFollowsTheLastFrame(unittest.TestCase):

    def test_nothing_is_reported_before_a_frame_arrives(self):
        mo = _make_mo()
        self.assertIsNone(mo.lqi)
        self.assertIsNone(mo.rssi)

    def test_a_frame_updates_both(self):
        mo = _make_mo()
        mo._route_meter_frame(_frame())
        self.assertEqual(mo.lqi, 0x50)
        self.assertAlmostEqual(mo.rssi, -82.27)

    def test_the_newest_frame_wins(self):
        mo = _make_mo()
        mo._route_meter_frame(_frame(lqi=0x50, rssi=-82.27))
        mo._route_meter_frame(_frame(lqi=0x20, rssi=-95.27))
        self.assertEqual(mo.lqi, 0x20)
        self.assertAlmostEqual(mo.rssi, -95.27)

    def test_a_frame_from_another_object_still_counts(self):
        mo = _make_mo()
        mo._route_meter_frame(_frame(data=OTHER_OBJECT, lqi=0x33))
        self.assertEqual(mo.lqi, 0x33)  # the radio link is the same one

    def test_a_module_that_reports_neither_leaves_both_unset(self):
        mo = _make_mo()
        mo._route_meter_frame(_frame(lqi=None, rssi=None, side=None))  # BP35A1
        self.assertIsNone(mo.lqi)
        self.assertIsNone(mo.rssi)


class TestANewSessionStartsWithout(unittest.TestCase):

    def test_open_forgets_what_the_old_session_saw(self):
        mo = _make_mo()
        mo._route_meter_frame(_frame())
        mo._init_energy_unit = lambda: None
        mo.internal_xmit_interval = 0
        mo.open()
        self.assertIsNone(mo.lqi)
        self.assertIsNone(mo.rssi)


class TestItIsReadableFromAsync(unittest.TestCase):

    def test_async_reads_through_to_the_wrapped_momonga(self):
        mo = AsyncMomonga('', '', '/dev/ttyUSB0')
        try:
            mo._sync.session_manager = MagicMock()
            self.assertIsNone(mo.lqi)
            mo._sync._route_meter_frame(_frame())
            self.assertEqual(mo.lqi, 0x50)
            self.assertAlmostEqual(mo.rssi, -82.27)
        finally:
            mo._executor.shutdown(wait=False)
            mo._notif_executor.shutdown(wait=False)

    def test_async_does_not_let_them_be_set(self):
        mo = AsyncMomonga('', '', '/dev/ttyUSB0')
        try:
            with self.assertRaises(AttributeError):
                mo.lqi = 1
        finally:
            mo._executor.shutdown(wait=False)
            mo._notif_executor.shutdown(wait=False)


if __name__ == '__main__':
    unittest.main()
