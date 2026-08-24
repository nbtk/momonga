"""
The paths left over once the main ones were covered.

Each of these encodes a decision - which property a request carries, what a
missing reading looks like, what makes a response unacceptable, what happens
when the module answers a rejoin with a refusal. What is deliberately left
uncovered is the other kind: repr() on the sentinels, `except Exception: pass`
cleanup, and the final else of parse_installation_location, which a byte
cannot reach because the branches above it cover 0x00 to 0xFF.

Run:
  python -m unittest tests/test_remaining_paths_unit.py -v
"""
import datetime
import queue
import threading
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga import Momonga
from momonga.momonga_device_strategy import BP35C2Strategy
from momonga.momonga_echonet_data import EchonetDataParser as Parser
from momonga.momonga_echonet_enum import (CONTROLLER_EOJ, ECHONET_LITE_EHD,
                                          ECHONET_LITE_PORT, EchonetPropertyCode,
                                          SMART_METER_EOJ)
from momonga.momonga_exception import (MomongaNeedToReopen, MomongaResponseNotExpected,
                                       MomongaRuntimeError, MomongaSkJoinFailure,
                                       MomongaValueError)
from momonga.momonga_response import SkParsedRxUdp
from momonga.momonga_session_manager import MomongaSessionManager, _STOP_RECEIVER
from tests._timebox import TimeBoxedTestCase

EPC = EchonetPropertyCode
UNIT, COEFFICIENT = 0.1, 1
MISSING = 0xFFFFFFFE


def _rx(data: bytes, port: int = ECHONET_LITE_PORT) -> SkParsedRxUdp:
    return SkParsedRxUdp(src_addr='FE80::1', dst_addr='', src_port=port, dst_port=port,
                         src_mac=b'', side=0, sec=2, data=data)


class TestAMissingReadingIsNoneNotZero(TimeBoxedTestCase):
    """0xFFFFFFFE is how the meter says it has no value for that slot. Series 1
    was covered; 2 and 3 were not, and they carry two directions each."""

    @staticmethod
    def _series(missing_at, points, minute):
        pairs = []
        for i in range(points):
            normal = MISSING if i == missing_at else i + 1
            reverse = MISSING if i == missing_at else i + 51
            pairs.append(normal.to_bytes(4, 'big') + reverse.to_bytes(4, 'big'))
        return (b'\x07\xea\x08\x17\x0c' + bytes([minute, points])) + b''.join(pairs)

    def test_series_two_reports_a_missing_slot_as_none(self):
        got = Parser.parse_historical_cumulative_energy_2(
            self._series(2, 12, 0x00), UNIT, COEFFICIENT)

        self.assertEqual(got[2]['cumulative energy'],
                         {'normal direction': None, 'reverse direction': None})
        self.assertIsNotNone(got[3]['cumulative energy']['normal direction'])

    def test_series_three_reports_a_missing_slot_as_none(self):
        got = Parser.parse_historical_cumulative_energy_3(
            self._series(1, 10, 0x05), UNIT, COEFFICIENT)

        self.assertEqual(got[1]['cumulative energy'],
                         {'normal direction': None, 'reverse direction': None})

    def test_the_one_minute_reading_reports_both_directions_missing(self):
        edt = (b'\x07\xea\x08\x17\x0c\x22\x00'
               + MISSING.to_bytes(4, 'big') + MISSING.to_bytes(4, 'big'))

        got = Parser.parse_one_minute_measured_cumulative_energy(edt, UNIT, COEFFICIENT)

        self.assertEqual(got['cumulative energy'],
                         {'normal direction': None, 'reverse direction': None})


class TestTheParserBranchesThatNeedTheRightByte(TimeBoxedTestCase):

    def test_an_installation_location_carrying_free_form_information(self):
        edt = b'\x01' + bytes(range(16))   # the spec sizes this form at 17 bytes

        self.assertEqual(Parser.parse_installation_location(edt),
                         'location information: ' + bytes(range(16)).hex())

    def test_a_position_code_without_the_position_is_refused(self):
        # 0x01 says a position follows; one byte of it says nothing, and used
        # to come back as 'location information: ' with an empty hex string
        with self.assertRaises(MomongaResponseNotExpected):
            Parser.parse_installation_location(b'\x01')
        with self.assertRaises(MomongaResponseNotExpected):
            Parser.parse_installation_location(b'\x01\xab\xcd')

    def test_the_one_byte_form_is_untouched(self):
        self.assertEqual(Parser.parse_installation_location(b'\x61'),
                         'garden/perimeter 1')

    def test_the_codes_reserved_for_later_say_so(self):
        for code in (0x02, 0x07, 0x80, 0xFE):
            with self.subTest(code=code):
                self.assertEqual(Parser.parse_installation_location(bytes([code])),
                                 'not implemented')

    def test_a_location_that_is_not_fixed(self):
        self.assertEqual(Parser.parse_installation_location(b'\xff'), 'location not fixed')

    def test_a_standard_version_with_a_two_character_prefix(self):
        self.assertEqual(Parser.parse_standard_version_information(b'AB\x43\x01'), 'ABC.1')

    def test_a_fault_status_the_meter_does_not_define(self):
        self.assertIsNone(Parser.parse_fault_status(b'\x00'))

    def test_an_operation_status_the_meter_does_not_define(self):
        self.assertIsNone(Parser.parse_operation_status(b'\x00'))

    def test_a_property_map_carrying_a_code_the_library_does_not_know(self):
        got = Parser.parse_property_map(b'\x02\x80\x01')

        self.assertEqual(got, {EPC.operation_status, 0x01})


class TestTheBuildersRefuseWhatTheMeterCannotTake(TimeBoxedTestCase):

    def test_a_day_outside_the_range(self):
        for day in (-1, 100):
            with self.subTest(day=day):
                with self.assertRaises(MomongaValueError):
                    Parser  # noqa: B018 - keeps the import honest
                    from momonga.momonga_echonet_data import EchonetDataBuilder
                    EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(day)

    def test_a_data_point_count_outside_the_range(self):
        from momonga.momonga_echonet_data import EchonetDataBuilder
        when = datetime.datetime(2026, 8, 23, 12, 0)
        for build, bad in ((EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2, 13),
                           (EchonetDataBuilder.build_edata_to_set_time_for_historical_data_3, 11)):
            with self.subTest(build=build.__name__):
                with self.assertRaises(MomongaValueError):
                    build(when, bad)
                with self.assertRaises(MomongaValueError):
                    build(when, 0)

    def test_a_year_the_two_byte_field_cannot_hold(self):
        from momonga.momonga_echonet_data import EchonetDataBuilder
        with patch('momonga.momonga_echonet_data.datetime') as dt:
            dt.timedelta = datetime.timedelta
            impossible = MagicMock(year=10000, month=1, day=1, hour=0, minute=0)
            for build in (EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2,
                          EchonetDataBuilder.build_edata_to_set_time_for_historical_data_3):
                with self.subTest(build=build.__name__):
                    with self.assertRaises(MomongaValueError):
                        build(impossible, 1)

    def test_the_half_hour_is_snapped_for_series_two_only(self):
        from momonga.momonga_echonet_data import EchonetDataBuilder
        at_47 = datetime.datetime(2026, 8, 23, 12, 47)

        self.assertEqual(
            EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(at_47, 12)[5], 30)
        self.assertEqual(
            EchonetDataBuilder.build_edata_to_set_time_for_historical_data_3(at_47, 10)[5], 47)


class _AMeter(TimeBoxedTestCase):

    def setUp(self):
        sm = MagicMock()
        sm.recv_q, sm.notif_q = queue.Queue(), queue.Queue()
        sm.smart_meter_addr = 'FE80::1'
        self.sent = []
        self.answer = None

        def xmitter(payload, timeout=None):
            self.sent.append(payload)
            if self.answer is not None:
                sm.recv_q.put(_rx(self.answer(payload)))

        sm.xmitter = xmitter
        mo = Momonga('', '', '/dev/ttyUSB0')
        mo.is_open = True
        mo.session_manager = sm
        mo.xmit_retries, mo.recv_timeout, mo.internal_xmit_interval = 1, 0.2, 0
        mo.energy_unit, mo.energy_coefficient = UNIT, COEFFICIENT
        self.mo, self.sm = mo, sm

    def _set_res(self, payload):
        tid, opc = payload[2:4], payload[11]
        out, cur = b'', 12
        for _ in range(opc):
            epc = payload[cur]
            cur += 2 + payload[cur + 1]
            out += bytes([epc, 0])
        return (ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                + bytes([0x71, opc]) + out)


class TestRequestToSetCarriesOnlyWhatItWasGiven(_AMeter):

    def setUp(self):
        super().setUp()
        self.answer = self._set_res

    def _epcs(self):
        payload = self.sent[0]
        epcs, cur = [], 12
        for _ in range(payload[11]):
            epcs.append(payload[cur])
            cur += 2 + payload[cur + 1]
        return epcs

    def test_nothing_at_all_sends_nothing(self):
        self.mo.request_to_set()

        self.assertEqual(self.sent, [])

    def test_one_argument_carries_one_property(self):
        self.mo.request_to_set(day_for_historical_data_1={'day': 1})

        self.assertEqual(self._epcs(), [EPC.day_for_historical_data_1])

    def test_all_three_travel_in_one_request(self):
        when = datetime.datetime(2026, 8, 23, 12, 0)
        self.mo.request_to_set(
            day_for_historical_data_1={'day': 1},
            time_for_historical_data_2={'timestamp': when, 'num_of_data_points': 12},
            time_for_historical_data_3={'timestamp': when, 'num_of_data_points': 10})

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self._epcs(), [EPC.day_for_historical_data_1,
                                        EPC.time_for_historical_data_2,
                                        EPC.time_for_historical_data_3])


class TestRequestToGetSaysWhatItCouldNotRead(_AMeter):

    def test_a_property_with_no_parser(self):
        def answer(payload):
            tid = payload[2:4]
            return (ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                    + b'\x72\x01' + bytes([0x01, 1, 0xFF]))

        self.answer = answer

        with self.assertRaises(MomongaRuntimeError):
            self.mo.request_to_get({0x01})

    def test_a_property_whose_edt_will_not_parse(self):
        def answer(payload):
            tid = payload[2:4]
            return (ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                    + b'\x72\x01' + bytes([EPC.unit_for_cumulative_energy, 1, 0x0F]))

        self.answer = answer

        with self.assertRaises(MomongaError := MomongaRuntimeError):
            self.mo.request_to_get({EPC.unit_for_cumulative_energy})


class TestAResponseHasToLookLikeOne(_AMeter):
    """The remaining frame checks, alongside the ones in test_frame_validation."""

    def _decoy_then_real(self, decoy):
        real = [None]

        def answer(payload):
            tid = payload[2:4]
            real[0] = (ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                       + b'\x72\x01\xe7\x04' + (-250).to_bytes(4, 'big', signed=True))
            self.sm.recv_q.put(_rx(decoy(tid)))
            return real[0]

        self.answer = answer
        return self.mo.get_instantaneous_power()

    def test_a_frame_that_is_not_echonet_lite_is_passed_over(self):
        got = self._decoy_then_real(
            lambda tid: b'\x10\x82' + tid + SMART_METER_EOJ + CONTROLLER_EOJ
            + b'\x72\x01\xe7\x04\x00\x00\x00\x63')

        self.assertEqual(got, -250)

    def test_a_frame_carrying_more_properties_than_asked_for_is_passed_over(self):
        got = self._decoy_then_real(
            lambda tid: ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
            + b'\x72\x02\xe7\x04\x00\x00\x00\x63\xe8\x04\x00\x00\x00\x00')

        self.assertEqual(got, -250)

    def test_a_frame_answering_a_different_property_is_passed_over(self):
        got = self._decoy_then_real(
            lambda tid: ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
            + b'\x72\x01\xe8\x04\x00\x00\x00\x63')

        self.assertEqual(got, -250)

    def test_a_frame_too_short_to_hold_a_header_is_passed_over(self):
        got = self._decoy_then_real(lambda tid: ECHONET_LITE_EHD + tid)

        self.assertEqual(got, -250)


class TestANotificationWithoutAValue(_AMeter):

    def test_a_property_carrying_no_edt_arrives_as_none(self):
        frame = (ECHONET_LITE_EHD + b'\x00\x01' + SMART_METER_EOJ + CONTROLLER_EOJ
                 + b'\x73\x01' + bytes([EPC.instantaneous_power, 0]))

        self.mo._route_meter_frame(_rx(frame))
        notif = self.mo.get_notification(timeout=1)

        self.assertIn(EPC.instantaneous_power, notif['properties'])
        self.assertIsNone(notif['properties'][EPC.instantaneous_power])

    def test_a_meter_frame_that_is_not_a_notification_goes_to_the_request_queue(self):
        frame = (ECHONET_LITE_EHD + b'\x00\x01' + SMART_METER_EOJ + CONTROLLER_EOJ
                 + b'\x72\x01\xe7\x04\x00\x00\x00\x63')

        self.mo._route_meter_frame(_rx(frame))

        self.assertFalse(self.sm.recv_q.empty())
        self.assertTrue(self.sm.notif_q.empty())


class TestARefusedRejoinEndsTheSession(TimeBoxedTestCase):
    """EVENT 24 with the lock free is the path that actually rejoins - the
    bounds tests hold the lock so they never enter it."""

    @staticmethod
    def _sm():
        sm = MomongaSessionManager('', '', '/dev/ttyUSB0', reset_dev=False)
        sm.smart_meter_addr = 'FE80::1'
        sm.skw = MagicMock()
        sm.skw.subscribers = {}
        sm.skw.device_strategy = BP35C2Strategy()
        sm.session_established = True
        return sm

    def _run_receiver(self, sm):
        th = threading.Thread(target=sm._receiver, daemon=True)
        th.start()
        sm._pkt_sbsc_q.put('EVENT 24 FE80::1 0')
        sm._pkt_sbsc_q.put(_STOP_RECEIVER)
        th.join(5)
        return th

    def test_a_rejoin_that_works_puts_the_session_back(self):
        sm = self._sm()

        self._run_receiver(sm)

        sm.skw.skjoin.assert_called_once_with('FE80::1')
        self.assertFalse(sm._rejoin_lock.locked())

    def test_a_rejoin_the_module_refuses_ends_the_receiver(self):
        sm = self._sm()
        sm.skw.skjoin.side_effect = MomongaSkJoinFailure('no PAN')

        self._run_receiver(sm)

        self.assertIsInstance(sm.receiver_exception, MomongaNeedToReopen)
        self.assertFalse(sm._rejoin_lock.locked())  # handed back on the way out


class TestALeftoverFrameDoesNotBecomeTheNextAnswer(_AMeter):

    def test_the_request_queue_is_drained_before_a_request_goes_out(self):
        # a reply that arrived after its own request gave up is still sitting
        # there; taking it would answer the new request with the old value
        stale = (ECHONET_LITE_EHD + b'\x00\x01' + SMART_METER_EOJ + CONTROLLER_EOJ
                 + b'\x72\x01\xe7\x04' + (999).to_bytes(4, 'big'))
        self.sm.recv_q.put(_rx(stale))

        def answer(payload):
            tid = payload[2:4]
            return (ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                    + b'\x72\x01\xe7\x04' + (-250).to_bytes(4, 'big', signed=True))

        self.answer = answer

        self.assertEqual(self.mo.get_instantaneous_power(), -250)

    def test_a_udp_frame_arriving_mid_command_is_not_the_command_reply(self):
        # ERXUDP lines reach the command queue too, and one of them must not be
        # read as the OK the command is waiting for
        from unittest.mock import MagicMock as _MagicMock
        from momonga.momonga_sk_wrapper import MomongaSkWrapper

        skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
        skw._ser = _MagicMock()

        def module(line, payload=None):
            q = skw.subscribers['cmd_exec_q']
            q.put('ERXUDP FE80::1 FE80::2 0E1A 0E1A AABB 50 00 00 0002 1081')
            q.put('OK')

        with patch.object(skw, '_writeline', module):
            self.assertEqual(skw.exec_command(['SKVER'])[-1], 'OK')


class TestAParserFailureThatIsNotAMomongaError(_AMeter):

    def test_it_is_named_and_wrapped_for_the_caller(self):
        # a month of 13 is a ValueError out of datetime, not a MomongaError
        def answer(payload):
            tid = payload[2:4]
            edt = b'\x07\xea\x0d\x17\x0c\x00\x00' + (3000).to_bytes(4, 'big')
            return (ECHONET_LITE_EHD + tid + SMART_METER_EOJ + CONTROLLER_EOJ
                    + b'\x72\x01' + bytes([EPC.cumulative_energy_measured_at_fixed_time,
                                           len(edt)]) + edt)

        self.answer = answer

        with self.assertRaises(MomongaResponseNotExpected) as caught:
            self.mo.request_to_get({EPC.cumulative_energy_measured_at_fixed_time})

        self.assertIn('EA', str(caught.exception))     # the EPC that failed
        self.assertIn('11 bytes', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
