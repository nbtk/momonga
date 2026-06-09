import datetime
import queue
import threading
import unittest
from unittest.mock import MagicMock

import momonga
from momonga.momonga import Momonga
from momonga.momonga_async import AsyncMomonga
from momonga.momonga_echonet_data import EchonetDataBuilder, EchonetDataParser
from momonga.momonga_echonet_enum import EchonetPropertyCode, EchonetServiceCode
from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_response import SkParsedRxUdp


# ---------------------------------------------------------------------------
# EchonetDataParser
# ---------------------------------------------------------------------------

class TestEchonetDataParser(unittest.TestCase):

    def test_parse_operation_status_on(self):
        self.assertTrue(EchonetDataParser.parse_operation_status(b'\x30'))

    def test_parse_operation_status_off(self):
        self.assertFalse(EchonetDataParser.parse_operation_status(b'\x31'))

    def test_parse_operation_status_unknown(self):
        self.assertIsNone(EchonetDataParser.parse_operation_status(b'\x00'))

    def test_parse_fault_status_fault(self):
        self.assertTrue(EchonetDataParser.parse_fault_status(b'\x41'))

    def test_parse_fault_status_no_fault(self):
        self.assertFalse(EchonetDataParser.parse_fault_status(b'\x42'))

    def test_parse_instantaneous_power_positive(self):
        self.assertEqual(EchonetDataParser.parse_instantaneous_power(b'\x00\x00\x03\xe8'), 1000)

    def test_parse_instantaneous_power_negative(self):
        self.assertEqual(EchonetDataParser.parse_instantaneous_power(b'\xff\xff\xfc\x18'), -1000)

    def test_parse_instantaneous_current(self):
        result = EchonetDataParser.parse_instantaneous_current(b'\x00\x64\x00\x32')
        self.assertAlmostEqual(result['r phase current'], 10.0)
        self.assertAlmostEqual(result['t phase current'], 5.0)

    def test_parse_measured_cumulative_energy(self):
        result = EchonetDataParser.parse_measured_cumulative_energy(b'\x00\x00\x00\x64', 0.1, 1)
        self.assertAlmostEqual(result, 10.0)

    def test_parse_unit_for_cumulative_energy_kwh(self):
        self.assertEqual(EchonetDataParser.parse_unit_for_cumulative_energy(b'\x00'), 1)

    def test_parse_unit_for_cumulative_energy_01kwh(self):
        self.assertAlmostEqual(EchonetDataParser.parse_unit_for_cumulative_energy(b'\x01'), 0.1)

    def test_parse_unit_for_cumulative_energy_10kwh(self):
        self.assertEqual(EchonetDataParser.parse_unit_for_cumulative_energy(b'\x0A'), 10)

    def test_parse_unit_for_cumulative_energy_unknown_raises(self):
        with self.assertRaises(momonga.MomongaRuntimeError):
            EchonetDataParser.parse_unit_for_cumulative_energy(b'\xFF')

    def test_parse_current_time_setting(self):
        result = EchonetDataParser.parse_current_time_setting(b'\x0c\x1e')
        self.assertEqual(result, datetime.time(12, 30, 0))

    def test_parse_current_date_setting(self):
        result = EchonetDataParser.parse_current_date_setting(b'\x07\xd6\x06\x05')
        self.assertEqual(result, datetime.date(2006, 6, 5))

    def test_parse_serial_number(self):
        self.assertEqual(EchonetDataParser.parse_serial_number(b'ABC123'), 'ABC123')

    def test_parse_manufacturer_code(self):
        raw = b'\x00\x01\x02'
        self.assertEqual(EchonetDataParser.parse_manufacturer_code(raw), raw)

    def test_parse_coefficient_for_cumulative_energy(self):
        self.assertEqual(EchonetDataParser.parse_coefficient_for_cumulative_energy(b'\x00\x00\x00\x01'), 1)

    def test_parse_property_map_small(self):
        # num_of_properties=2 (<16), then two property codes
        edt = b'\x02\x80\x88'
        result = EchonetDataParser.parse_property_map(edt)
        self.assertIn(EchonetPropertyCode.operation_status, result)
        self.assertIn(EchonetPropertyCode.fault_status, result)

    def test_parse_property_map_bitmap(self):
        # num_of_properties=16 triggers bitmap format (16 bytes follow)
        # Set bit 0 of byte 0 → EPC = ((0 + 8) << 4) | 0 = 0x80 (operation_status)
        # Set bit 0 of byte 8 → EPC = ((0 + 8) << 4) | 8 = 0x88 (fault_status)
        bitmap = bytearray(16)
        bitmap[0] = 0x01   # bit 0 set → EPC 0x80
        bitmap[8] = 0x01   # bit 0 set → EPC 0x88
        edt = bytes([16]) + bytes(bitmap)
        result = EchonetDataParser.parse_property_map(edt)
        self.assertIn(EchonetPropertyCode.operation_status, result)
        self.assertIn(EchonetPropertyCode.fault_status, result)

    def test_parse_installation_location_not_set(self):
        self.assertEqual(EchonetDataParser.parse_installation_location(b'\x00'), 'location not set')

    def test_parse_installation_location_room(self):
        # 0x0F: code>>3=1 (living room), code&0x07=7 → 'living room 7'
        self.assertEqual(EchonetDataParser.parse_installation_location(b'\x0F'), 'living room 7')

    def test_parse_installation_location_not_fixed(self):
        self.assertEqual(EchonetDataParser.parse_installation_location(b'\xFF'), 'location not fixed')

    def test_parse_standard_version_information(self):
        # edt[0]=0 (skip), edt[1]=0 (skip), edt[2]=0x46='F', edt[3]=1 → 'F.1'
        self.assertEqual(EchonetDataParser.parse_standard_version_information(b'\x00\x00\x46\x01'), 'F.1')

    def test_parse_route_b_id(self):
        edt = b'\x00\x11\x22\x33\xAA\xBB\xCC'
        result = EchonetDataParser.parse_route_b_id(edt)
        self.assertEqual(result['manufacturer code'], b'\x11\x22\x33')
        self.assertEqual(result['authentication id'], b'\xAA\xBB\xCC')

    def test_parse_day_for_historical_data_1(self):
        self.assertEqual(EchonetDataParser.parse_day_for_historical_data_1(b'\x05'), 5)

    def test_parse_cumulative_energy_measured_at_fixed_time(self):
        ts = b'\x07\xe8\x06\x05\x0c\x00\x00'   # 2024-06-05 12:00:00
        energy = b'\x00\x00\x00\x64'             # 100 raw
        result = EchonetDataParser.parse_cumulative_energy_measured_at_fixed_time(
            ts + energy, energy_unit=1, energy_coefficient=1)
        self.assertEqual(result['timestamp'], datetime.datetime(2024, 6, 5, 12, 0, 0))
        self.assertAlmostEqual(result['cumulative energy'], 100.0)

    def test_parse_time_for_historical_data_2(self):
        edt = b'\x07\xe8\x06\x05\x0c\x1e\x06'  # 2024-06-05 12:30, 6 points
        result = EchonetDataParser.parse_time_for_historical_data_2(edt)
        self.assertEqual(result['timestamp'], datetime.datetime(2024, 6, 5, 12, 30))
        self.assertEqual(result['number of data points'], 6)

    def test_parse_time_for_historical_data_2_missing_year(self):
        edt = b'\xFF\xFF\x01\x01\x00\x00\x06'   # year=0xFFFF → None
        result = EchonetDataParser.parse_time_for_historical_data_2(edt)
        self.assertIsNone(result['timestamp'])

    def test_parse_time_for_historical_data_3(self):
        edt = b'\x07\xe8\x06\x05\x0c\x1e\x0A'  # 2024-06-05 12:30, 10 points
        result = EchonetDataParser.parse_time_for_historical_data_3(edt)
        self.assertEqual(result['timestamp'], datetime.datetime(2024, 6, 5, 12, 30))
        self.assertEqual(result['number of data points'], 10)

    def test_parse_time_for_historical_data_3_missing_year(self):
        edt = b'\xFF\xFF\x01\x01\x00\x00\x0A'
        result = EchonetDataParser.parse_time_for_historical_data_3(edt)
        self.assertIsNone(result['timestamp'])

    def test_parse_one_minute_measured_cumulative_energy_missing_values(self):
        # 0xFFFFFFFE sentinel → None for both directions
        ts = b'\x07\xe8\x06\x05\x0c\x00\x00'
        missing = b'\xFF\xFF\xFF\xFE'
        result = EchonetDataParser.parse_one_minute_measured_cumulative_energy(
            ts + missing + missing, energy_unit=1, energy_coefficient=1)
        self.assertIsNone(result['cumulative energy']['normal direction'])
        self.assertIsNone(result['cumulative energy']['reverse direction'])

    def test_parse_one_minute_measured_cumulative_energy_values(self):
        ts = b'\x07\xe8\x06\x05\x0c\x00\x00'
        normal = b'\x00\x00\x00\x64'   # 100
        reverse = b'\x00\x00\x00\xC8'  # 200
        result = EchonetDataParser.parse_one_minute_measured_cumulative_energy(
            ts + normal + reverse, energy_unit=0.1, energy_coefficient=2)
        self.assertAlmostEqual(result['cumulative energy']['normal direction'], 20.0)
        self.assertAlmostEqual(result['cumulative energy']['reverse direction'], 40.0)


# ---------------------------------------------------------------------------
# EchonetDataBuilder
# ---------------------------------------------------------------------------

class TestEchonetDataBuilder(unittest.TestCase):

    def test_build_day_for_historical_data_1(self):
        self.assertEqual(EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(3), b'\x03')

    def test_build_day_boundary_zero(self):
        self.assertEqual(EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(0), b'\x00')

    def test_build_day_boundary_max(self):
        self.assertEqual(EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(99), b'\x63')

    def test_build_day_negative_raises(self):
        with self.assertRaises(momonga.MomongaValueError):
            EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(-1)

    def test_build_day_too_large_raises(self):
        with self.assertRaises(momonga.MomongaValueError):
            EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(100)

    def test_build_time_for_historical_data_2_invalid_num_low(self):
        with self.assertRaises(momonga.MomongaValueError):
            EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(
                datetime.datetime(2024, 1, 1), 0)

    def test_build_time_for_historical_data_2_invalid_num_high(self):
        with self.assertRaises(momonga.MomongaValueError):
            EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(
                datetime.datetime(2024, 1, 1), 13)

    def test_build_time_for_historical_data_3_invalid_num_high(self):
        with self.assertRaises(momonga.MomongaValueError):
            EchonetDataBuilder.build_edata_to_set_time_for_historical_data_3(
                datetime.datetime(2024, 1, 1), 11)

    def test_build_time_for_historical_data_2_minute_snaps_to_30(self):
        ts = datetime.datetime(2024, 6, 1, 12, 45)
        result = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(ts, 6)
        # minute byte is at index 5, should be 30 (0x1e) not 45
        self.assertEqual(result[5], 30)

    def test_build_time_for_historical_data_2_minute_snaps_to_0(self):
        ts = datetime.datetime(2024, 6, 1, 12, 10)
        result = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(ts, 6)
        self.assertEqual(result[5], 0)


# ---------------------------------------------------------------------------
# Momonga.get_notification()
# ---------------------------------------------------------------------------

def _make_echonet_frame(esv: int, epc: int, edt: bytes) -> bytes:
    """Build a minimal ECHONET Lite frame for use as notification data."""
    header = b'\x10\x81\x00\x01\x02\x88\x01\x05\xff\x01'  # EHD + TID + SEOJ + DEOJ
    return (header
            + esv.to_bytes(1, 'big')
            + b'\x01'                          # OPC = 1 property
            + epc.to_bytes(1, 'big')
            + len(edt).to_bytes(1, 'big')
            + edt)


class TestGetNotification(unittest.TestCase):

    def _make_momonga(self):
        mo = object.__new__(Momonga)
        mo.is_open = True
        mo.energy_unit = 1
        mo.energy_coefficient = 1
        mo.session_manager = MagicMock()
        return mo

    def test_raises_when_not_open(self):
        mo = self._make_momonga()
        mo.is_open = False
        with self.assertRaises(momonga.MomongaRuntimeError):
            mo.get_notification()

    def test_returns_none_on_empty_queue(self):
        mo = self._make_momonga()
        mo.session_manager.notif_q.get.side_effect = queue.Empty
        self.assertIsNone(mo.get_notification(timeout=0))

    @staticmethod
    def _make_frame(data: bytes) -> SkParsedRxUdp:
        return SkParsedRxUdp(src_addr='', dst_addr='', src_port=0, dst_port=0,
                              src_mac=b'', side=0, sec=0, data=data)

    def test_inf_notification_parsed(self):
        mo = self._make_momonga()
        data = _make_echonet_frame(0x73, 0xE7, b'\x00\x00\x03\xe8')  # INF, instantaneous_power=1000
        mo.session_manager.notif_q.get.return_value = self._make_frame(data)
        result = mo.get_notification(timeout=1)
        self.assertEqual(result['esv'], EchonetServiceCode.inf)
        self.assertIn(EchonetPropertyCode.instantaneous_power, result['properties'])
        self.assertEqual(result['properties'][EchonetPropertyCode.instantaneous_power], 1000)

    def test_infc_triggers_infc_res(self):
        mo = self._make_momonga()
        data = _make_echonet_frame(0x74, 0xE7, b'\x00\x00\x03\xe8')  # INFC
        mo.session_manager.notif_q.get.return_value = self._make_frame(data)
        mo.session_manager.xmitter.return_value = None
        result = mo.get_notification(timeout=1)
        mo.session_manager.xmitter.assert_called_once()
        self.assertEqual(result['esv'], EchonetServiceCode.infc)

    def test_infc_res_xmit_failure_does_not_raise(self):
        mo = self._make_momonga()
        data = _make_echonet_frame(0x74, 0xE7, b'\x00\x00\x03\xe8')
        mo.session_manager.notif_q.get.return_value = self._make_frame(data)
        mo.session_manager.xmitter.side_effect = Exception('xmit failed')
        result = mo.get_notification(timeout=1)  # must not raise
        self.assertEqual(result['esv'], EchonetServiceCode.infc)

    def test_unknown_epc_stored_as_raw_bytes(self):
        mo = self._make_momonga()
        data = _make_echonet_frame(0x73, 0x01, b'\xAB\xCD')  # EPC 0x01 not in enum
        mo.session_manager.notif_q.get.return_value = self._make_frame(data)
        result = mo.get_notification(timeout=1)
        self.assertEqual(result['properties'][0x01], b'\xAB\xCD')

    def test_energy_epc_uses_energy_unit_and_coefficient(self):
        mo = self._make_momonga()
        mo.energy_unit = 0.1
        mo.energy_coefficient = 2
        # cumulative_energy_measured_at_fixed_time: timestamp(7B) + energy(4B)
        ts_bytes = b'\x07\xe8\x06\x05\x0c\x00\x00'  # 2024-06-05 12:00:00
        energy_bytes = b'\x00\x00\x00\x64'           # 100 raw → 100 * 0.1 * 2 = 20.0
        edt = ts_bytes + energy_bytes
        data = _make_echonet_frame(0x73, 0xEA, edt)
        mo.session_manager.notif_q.get.return_value = self._make_frame(data)
        result = mo.get_notification(timeout=1)
        parsed = result['properties'][EchonetPropertyCode.cumulative_energy_measured_at_fixed_time]
        self.assertAlmostEqual(parsed['cumulative energy'], 20.0)

    def test_malformed_frame_too_short_returns_none(self):
        mo = self._make_momonga()
        mo.session_manager.notif_q.get.return_value = self._make_frame(b'\x10\x81\x00\x01')
        self.assertIsNone(mo.get_notification(timeout=1))


# ---------------------------------------------------------------------------
# AsyncMomonga
# ---------------------------------------------------------------------------

class TestAsyncMomonga(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_sync = MagicMock()
        self.async_mo = object.__new__(AsyncMomonga)
        self.async_mo._sync = self.mock_sync

    async def test_open_delegates_to_sync(self):
        self.mock_sync.open.return_value = None
        await self.async_mo.open()
        self.mock_sync.open.assert_called_once()

    async def test_close_delegates_to_sync(self):
        self.mock_sync.close.return_value = None
        await self.async_mo.close()
        self.mock_sync.close.assert_called_once()

    async def test_aenter_returns_self(self):
        self.mock_sync.open.return_value = None
        result = await self.async_mo.__aenter__()
        self.assertIs(result, self.async_mo)

    async def test_aexit_calls_close(self):
        self.mock_sync.close.return_value = None
        await self.async_mo.__aexit__(None, None, None)
        self.mock_sync.close.assert_called_once()

    async def test_get_instantaneous_power(self):
        self.mock_sync.get_instantaneous_power.return_value = 500.0
        result = await self.async_mo.get_instantaneous_power()
        self.assertEqual(result, 500.0)

    async def test_get_measured_cumulative_energy_with_reverse(self):
        self.mock_sync.get_measured_cumulative_energy.return_value = 123.4
        result = await self.async_mo.get_measured_cumulative_energy(reverse=True)
        self.assertEqual(result, 123.4)
        self.mock_sync.get_measured_cumulative_energy.assert_called_once_with(True)

    async def test_get_notification_none_on_timeout(self):
        self.mock_sync.get_notification.return_value = None
        result = await self.async_mo.get_notification(timeout=0)
        self.assertIsNone(result)
        self.mock_sync.get_notification.assert_called_once_with(0)

    async def test_get_notification_returns_dict(self):
        fake = {'esv': EchonetServiceCode.inf, 'properties': {}}
        self.mock_sync.get_notification.return_value = fake
        result = await self.async_mo.get_notification(timeout=5)
        self.assertEqual(result, fake)

    async def test_notifications_skips_none_yields_dicts(self):
        fake = {'esv': EchonetServiceCode.inf, 'properties': {}}
        call_n = 0

        def side_effect(timeout):
            nonlocal call_n
            call_n += 1
            return None if call_n % 2 == 1 else fake

        self.mock_sync.get_notification.side_effect = side_effect

        collected = []
        async for notif in self.async_mo.notifications(timeout=1):
            collected.append(notif)
            if len(collected) >= 2:
                break

        self.assertEqual(collected, [fake, fake])

    async def test_request_to_set_delegates(self):
        self.mock_sync.request_to_set.return_value = None
        day = {'day': 1}
        await self.async_mo.request_to_set(day_for_historical_data_1=day)
        self.mock_sync.request_to_set.assert_called_once_with(day, None, None)

    async def test_request_to_get_delegates(self):
        epcs = {EchonetPropertyCode.instantaneous_power}
        expected = {EchonetPropertyCode.instantaneous_power: 300.0}
        self.mock_sync.request_to_get.return_value = expected
        result = await self.async_mo.request_to_get(epcs)
        self.assertEqual(result, expected)
        self.mock_sync.request_to_get.assert_called_once_with(epcs)


# ---------------------------------------------------------------------------
# Receiver ERXUDP routing (SEOJ filter)
# ---------------------------------------------------------------------------

class TestReceiverRouting(unittest.TestCase):

    def _make_session_manager(self):
        from momonga.momonga_session_manager import MomongaSessionManager
        from momonga.momonga_echonet_enum import EchonetServiceCode, SMART_METER_EOJ
        sm = object.__new__(MomongaSessionManager)
        sm.pkt_sbsc_q = queue.Queue()
        sm.recv_q = queue.Queue()
        sm.notif_q = queue.Queue()
        sm.xmit_lock = threading.Lock()
        sm.xmit_restriction_cnt = 0
        sm.session_established = True
        sm.receiver_exception = None
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = MagicMock()
        sm.skw.device_strategy = BP35C2Strategy()

        def route(frame):
            seoj = frame.data[4:7] if len(frame.data) >= 7 else b''
            if seoj != SMART_METER_EOJ:
                return
            esv = frame.data[10] if len(frame.data) > 10 else -1
            if esv in (EchonetServiceCode.inf, EchonetServiceCode.infc):
                sm.notif_q.put(frame)
            else:
                sm.recv_q.put(frame)
        sm.on_meter_frame = route
        return sm

    @staticmethod
    def _erxudp(seoj: str, esv: int) -> str:
        # EHD(4) + TID(4) + SEOJ(6) + DEOJ(6) + ESV(2) + OPC+EPC+PDC+EDT
        payload = '1081' + '0001' + seoj + '05FF01' + ('%02X' % esv) + '01E70400000000'
        # BP35C2 format: ERXUDP src dst sport dport mac lqi sec side data_len data
        return ('ERXUDP FE80::1 FE80::2 0E1A 0E1A AABBCCDDEEFF '
                '50 00 00 %04X %s' % (len(payload) // 2, payload))

    def _route(self, sm, packet):
        th = threading.Thread(target=sm.receiver, daemon=True)
        th.start()
        sm.pkt_sbsc_q.put(packet)
        sm.pkt_sbsc_q.put('__CLOSE__')
        th.join(timeout=2)

    def test_non_smart_meter_inf_discarded(self):
        sm = self._make_session_manager()
        self._route(sm, self._erxudp('0EF001', 0x73))  # node profile INF
        self.assertTrue(sm.notif_q.empty())
        self.assertTrue(sm.recv_q.empty())

    def test_smart_meter_inf_to_notif_q(self):
        sm = self._make_session_manager()
        self._route(sm, self._erxudp('028801', 0x73))
        self.assertFalse(sm.notif_q.empty())
        self.assertTrue(sm.recv_q.empty())

    def test_smart_meter_infc_to_notif_q(self):
        sm = self._make_session_manager()
        self._route(sm, self._erxudp('028801', 0x74))
        self.assertFalse(sm.notif_q.empty())
        self.assertTrue(sm.recv_q.empty())

    def test_smart_meter_get_response_to_recv_q(self):
        sm = self._make_session_manager()
        self._route(sm, self._erxudp('028801', 0x72))  # GET_RES
        self.assertTrue(sm.notif_q.empty())
        self.assertFalse(sm.recv_q.empty())

    def test_erxudp_wrong_ip_discarded(self):
        sm = self._make_session_manager()
        payload = '1081000102880105FF017301E70400000000'
        data_len = '%04X' % (len(payload) // 2)
        wrong_ip = ('ERXUDP FE80::FFFF FE80::2 0E1A 0E1A AABBCCDDEEFF '
                    '50 00 00 %s %s' % (data_len, payload))
        self._route(sm, wrong_ip)
        self.assertTrue(sm.notif_q.empty())
        self.assertTrue(sm.recv_q.empty())

    def test_event_tx_done_to_recv_q(self):
        from momonga.momonga_response import SkParsedEvent, SkEventNum
        sm = self._make_session_manager()
        self._route(sm, 'EVENT 21 FE80::1 0 00')
        self.assertTrue(sm.notif_q.empty())
        self.assertFalse(sm.recv_q.empty())
        item = sm.recv_q.get_nowait()
        self.assertIsInstance(item, SkParsedEvent)
        self.assertEqual(item.num, SkEventNum.tx_done)

    def test_event_neighbor_discovery_to_recv_q(self):
        from momonga.momonga_response import SkParsedEvent, SkEventNum
        sm = self._make_session_manager()
        self._route(sm, 'EVENT 02 FE80::1 0')
        self.assertTrue(sm.notif_q.empty())
        self.assertFalse(sm.recv_q.empty())
        item = sm.recv_q.get_nowait()
        self.assertIsInstance(item, SkParsedEvent)
        self.assertEqual(item.num, SkEventNum.neighbor_discovery)

    def test_unrecognized_line_ignored(self):
        sm = self._make_session_manager()
        self._route(sm, 'OK')
        self.assertTrue(sm.notif_q.empty())
        self.assertTrue(sm.recv_q.empty())

    def test_on_meter_frame_exception_does_not_kill_receiver(self):
        sm = self._make_session_manager()

        def bad_callback(frame):
            raise RuntimeError('boom')
        sm.on_meter_frame = bad_callback

        self._route(sm, self._erxudp('028801', 0x73))
        self.assertIsNone(sm.receiver_exception)


if __name__ == '__main__':
    unittest.main()
