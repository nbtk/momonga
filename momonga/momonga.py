import datetime
import logging
import queue
import threading
import time

from collections.abc import Iterable
from typing import TypedDict, Any, Self

from .momonga_echonet_data import (EchonetProperty,
                                   EchonetPropertyWithData,
                                   EchonetDataParser,
                                   EchonetDataBuilder,
                                   parser_map,
                                   energy_parsers)
from .momonga_echonet_enum import EchonetServiceCode, EchonetPropertyCode, SMART_METER_EOJ, CONTROLLER_EOJ
from .momonga_exception import (MomongaError,
                                MomongaResponseNotExpected,
                                MomongaResponseNotPossible,
                                MomongaNeedToReopen,
                                MomongaValueError,
                                MomongaRuntimeError)
from .momonga_response import SkEventRxUdp
from .momonga_session_manager import MomongaSessionManager
from .momonga_session_manager import logger as session_manager_logger
from .momonga_sk_wrapper import logger as sk_wrapper_logger

logger = logging.getLogger(__name__)


class Momonga:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                 reopen_delays: Iterable[float] | None = None,
                 ) -> None:
        self.xmit_retries: int = 12
        self.recv_timeout: int | float = 12
        self.internal_xmit_interval: int | float = 5
        self.transaction_id: int = 0
        self.energy_unit: int | float = 1
        self.energy_coefficient: int = 1
        self.is_open: bool = False
        self.reopen_delays: Iterable[float] | None = reopen_delays
        self._request_lock: threading.Lock = threading.Lock()
        self._rbid: str = rbid
        self._pwd: str = pwd
        self._dev: str = dev
        self._baudrate: int = baudrate
        self._reset_dev: bool = reset_dev
        self.session_manager = MomongaSessionManager(rbid, pwd, dev, baudrate, reset_dev)

    def __init_energy_unit(self) -> None:
        logger.debug('Initializing the energy unit and coefficient.')
        self.energy_unit = self.get_unit_for_cumulative_energy()
        try:
            self.energy_coefficient = self.get_coefficient_for_cumulative_energy()
            time.sleep(self.internal_xmit_interval)
        except MomongaResponseNotPossible:  # due to the property 0xD3 is optional.
            self.energy_coefficient = 1
        time.sleep(self.internal_xmit_interval)

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, type, value, traceback) -> None:
        self.close()

    def open(self) -> Self:
        logger.info('Opening Momonga.')
        self.session_manager.open()
        time.sleep(self.internal_xmit_interval)
        self.is_open = True
        try:
            self.__init_energy_unit()
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise
        logger.info('Momonga is open.')
        return self

    def close(self) -> None:
        logger.info('Closing Momonga.')
        self.is_open = False
        self.session_manager.close()
        logger.info('Momonga is closed.')

    def reopen(self) -> None:
        logger.info('Reopening Momonga session.')
        try:
            self.close()
        except Exception:
            logger.debug('Error closing Momonga during reopen (ignored)', exc_info=True)

        self.session_manager = MomongaSessionManager(
            self._rbid, self._pwd, self._dev, self._baudrate, self._reset_dev
        )
        self.open()
        logger.info('Momonga session reopened successfully.')

    def get_notification(self, timeout: int | float | None = None) -> dict | None:
        if self.is_open is not True:
            raise MomongaRuntimeError('Momonga is not open.')

        try:
            raw = self.session_manager.notif_q.get(timeout=timeout)
        except queue.Empty:
            return None

        pkt = SkEventRxUdp([raw], self.session_manager.skw.device_type)
        data = pkt.data
        esv = data[10]
        opc = data[11]

        if esv == EchonetServiceCode.infc:
            self.__send_infc_res(data)

        properties = {}
        cur = 12
        for _ in range(opc):
            try:
                epc = EchonetPropertyCode(data[cur])
            except ValueError:
                epc = data[cur]
            cur += 1
            pdc = data[cur]
            cur += 1
            edt = data[cur:cur + pdc] if pdc > 0 else None
            cur += pdc

            if edt is not None:
                try:
                    parser = parser_map[epc]
                    if parser in energy_parsers:
                        properties[epc] = parser(edt, self.energy_unit, self.energy_coefficient)
                    else:
                        properties[epc] = parser(edt)
                except KeyError:
                    properties[epc] = edt
            else:
                properties[epc] = None

        return {'esv': EchonetServiceCode(esv), 'properties': properties}

    def __send_infc_res(self, infc_data: bytes) -> None:
        tid_int = int.from_bytes(infc_data[2:4], 'big')
        header = self.__build_request_header(tid_int, EchonetServiceCode.infc_res)
        opc = infc_data[11]
        props = b''
        cur = 12
        for _ in range(opc):
            props += infc_data[cur:cur + 1]  # EPC
            cur += 1
            pdc = infc_data[cur]
            cur += 1 + pdc
            props += b'\x00'               # PDC = 0, no EDT in response
        payload = header + opc.to_bytes(1, 'big') + props
        try:
            self.session_manager.xmitter(payload)
        except Exception:
            logger.warning('Failed to send INFC_Res.', exc_info=True)

    def __get_transaction_id(self) -> int:
        self.transaction_id += 1
        return self.transaction_id

    @staticmethod
    def __build_request_header(tid: int, esv: EchonetServiceCode) -> bytes:
        ehd = b'\x10\x81'  # echonet lite edata format 1
        tid = tid.to_bytes(4, 'big')[-2:]
        seoj = CONTROLLER_EOJ
        deoj = SMART_METER_EOJ
        esv = esv.to_bytes(1, 'big')
        return ehd + tid + seoj + deoj + esv

    def __build_request_payload_with_data(self,
                                          tid: int,
                                          esv: EchonetServiceCode,
                                          properties_with_data: list[EchonetPropertyWithData],
                                          ) -> bytes:
        header = self.__build_request_header(tid, esv)
        opc = len(properties_with_data).to_bytes(1, 'big')
        payload = header + opc
        for pd in properties_with_data:
            epc = pd.epc.to_bytes(1, 'big')
            pdc = len(pd.edt).to_bytes(1, 'big')
            edt = pd.edt
            payload += epc + pdc + edt

        return payload

    def __build_request_payload(self,
                                tid: int,
                                esv: EchonetServiceCode,
                                properties: list[EchonetProperty],
                                ) -> bytes:
        header = self.__build_request_header(tid, esv)  # get
        opc = len(properties).to_bytes(1, 'big')
        payload = header + opc
        for p in properties:
            epc = p.epc.to_bytes(1, 'big')
            pdc = b'\x00'
            payload += epc + pdc

        return payload

    @staticmethod
    def __extract_response_payload(data: bytes,
                                   tid: int,
                                   req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                                   ) -> list[EchonetPropertyWithData]:
        ehd = data[0:2]
        if ehd != b'\x10\x81':  # echonet lite edata format 1
            raise MomongaResponseNotExpected('The data format is not ECHONET Lite EDATA format 1')

        if data[2:4] != tid.to_bytes(4, 'big')[-2:]:
            raise MomongaResponseNotExpected('The transaction ID does not match.')

        seoj = data[4:7]
        if seoj != SMART_METER_EOJ:
            raise MomongaResponseNotExpected('The source is not a smart meter.')

        deoj = data[7:10]
        if deoj != CONTROLLER_EOJ:
            raise MomongaResponseNotExpected('The destination is not a controller.')

        esv = data[10]
        if 0x50 <= esv <= 0x5F:
            raise MomongaResponseNotPossible('The target smart meter could not respond. ESV: %X' % esv)

        opc = data[11]
        req_opc = len(req_properties)
        if opc != req_opc:
            raise MomongaResponseNotExpected(
                'Unexpected packet format. OPC is expected %s but %d was set.' % (req_opc, opc))

        properties = []
        cur = 12
        for rp in req_properties:
            try:
                epc = EchonetPropertyCode(data[cur])
            except ValueError:
                epc = data[cur]

            if epc != rp.epc:
                raise MomongaResponseNotExpected('The property code does not match. EPC: %X' % rp.epc)

            cur += 1
            pdc = data[cur]
            cur += 1
            if pdc == 0:
                edt = None
            else:
                edt_from = cur
                cur += pdc
                edt = data[edt_from:cur]

            properties.append(EchonetPropertyWithData(epc, edt))

        return properties

    def __request(self,
                  esv: EchonetServiceCode,
                  req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                  ) -> list[EchonetPropertyWithData]:
        logger.debug('Checking if Momonga is open: is_open=%s', self.is_open)
        if self.is_open is not True:
            raise MomongaRuntimeError('Momonga is not open.')

        with self._request_lock:
            return self.__request_locked(esv, req_properties)

    def __request_locked(self,
                         esv: EchonetServiceCode,
                         req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                         ) -> list[EchonetPropertyWithData]:
        tid = self.__get_transaction_id()
        if esv == EchonetServiceCode.set_c:
            tx_payload = self.__build_request_payload_with_data(tid, esv, req_properties)
        elif esv == EchonetServiceCode.get:
            tx_payload = self.__build_request_payload(tid, esv, req_properties)
        else:
            raise MomongaRuntimeError('Unsupported service code.')

        while not self.session_manager.recv_q.empty():
            self.session_manager.recv_q.get()  # drops stored data

        for _ in range(self.xmit_retries):
            self.session_manager.xmitter(tx_payload)
            while True:
                try:
                    res = self.session_manager.recv_q.get(timeout=self.recv_timeout)
                except queue.Empty:
                    logger.warning('The request for transaction id "%04X" timed out.' % tid)
                    break  # to rexmit the request.

                # messages of event types 21, 02, and received udp payloads will only be delivered.
                if res.startswith('EVENT 21'):
                    param = res.split()[-1]
                    if param == '00':
                        logger.info('Successfully transmitted a request packet for transaction id "%04X".' % tid)
                        continue
                    elif param == '01':
                        logger.info('Retransmitting the request packet for transaction id "%04X".' % tid)
                        time.sleep(self.internal_xmit_interval)
                        break  # to rexmit the request.
                    elif param == '02':
                        logger.info('Transmitting neighbor solicitation packets.')
                        continue
                    else:
                        logger.debug('A message for event 21 with an unknown parameter "%s" will be ignored.' % param)
                        continue
                elif res.startswith('EVENT 02'):
                    logger.info('Received a neighbor advertisement packet.')
                    continue
                elif res.startswith('ERXUDP'):
                    udp_pkt = SkEventRxUdp([res], self.session_manager.skw.device_type)
                    if not (udp_pkt.src_port == udp_pkt.dst_port == 0x0E1A):
                        continue
                    elif udp_pkt.side:
                        continue
                    elif udp_pkt.src_addr != self.session_manager.smart_meter_addr:
                        continue

                    try:
                        res_properties = self.__extract_response_payload(udp_pkt.data, tid, req_properties)
                    except MomongaResponseNotExpected:
                        continue

                    logger.info('Successfully received a response packet for transaction id "%04X".' % tid)
                    return res_properties
                else:
                    # this line should never be reached.
                    continue
        logger.error('Gave up to obtain a response for transaction id "%04X". Close Momonga and open it again.' % tid)
        raise MomongaNeedToReopen('Gave up to obtain a response for transaction id "%04X".'
                                  ' Close Momonga and open it again.' % tid)

    def __request_with_recovery(self,
                                esv: EchonetServiceCode,
                                req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                                ) -> list[EchonetPropertyWithData]:
        if self.reopen_delays is None:
            return self.__request(esv, req_properties)

        try:
            return self.__request(esv, req_properties)
        except MomongaNeedToReopen as initial_err:
            last_error: MomongaNeedToReopen = initial_err
            logger.warning('Session needs reopen, attempting recovery.')

        for delay in self.reopen_delays:
            delay = float(delay)
            if delay < 0:
                raise MomongaValueError('reopen_delays must not contain negative values.')

            time.sleep(delay)
            try:
                self.reopen()
                return self.__request(esv, req_properties)
            except MomongaNeedToReopen as err:
                last_error = err
                logger.warning('Reopen attempt failed after waiting %s seconds: %s', delay, err)
            except (MomongaError, OSError) as err:
                logger.warning('Reopen attempt failed after waiting %s seconds: %s: %s',
                               delay, type(err).__name__, err)
                last_error = MomongaNeedToReopen(str(err))

        logger.error('All reopen attempts exhausted.')
        raise last_error

    def __request_to_set(self,
                         properties_with_data: list[EchonetPropertyWithData]
                         ) -> None:
        self.__request_with_recovery(EchonetServiceCode.set_c, properties_with_data)

    def __request_to_get(self,
                         properties: list[EchonetProperty],
                         ) -> list[EchonetPropertyWithData]:
        return self.__request_with_recovery(EchonetServiceCode.get, properties)

    def get_operation_status(self) -> bool | None:
        req = EchonetProperty(EchonetPropertyCode.operation_status)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_operation_status(res.edt)

    def get_installation_location(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.installation_location)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_installation_location(res.edt)

    def get_standard_version(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.standard_version_information)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_standard_version_information(res.edt)

    def get_fault_status(self) -> bool | None:
        req = EchonetProperty(EchonetPropertyCode.fault_status)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_fault_status(res.edt)

    def get_manufacturer_code(self) -> bytes:
        req = EchonetProperty(EchonetPropertyCode.manufacturer_code)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_manufacturer_code(res.edt)

    def get_serial_number(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.serial_number)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_serial_number(res.edt)

    def get_current_time_setting(self) -> datetime.time:
        req = EchonetProperty(EchonetPropertyCode.current_time_setting)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_current_time_setting(res.edt)

    def get_current_date_setting(self) -> datetime.date:
        req = EchonetProperty(EchonetPropertyCode.current_date_setting)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_current_date_setting(res.edt)

    def get_properties_for_status_notification(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_for_status_notification)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_property_map(res.edt)

    def get_properties_to_set_values(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_to_set_values)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_property_map(res.edt)

    def get_properties_to_get_values(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_to_get_values)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_property_map(res.edt)

    def get_route_b_id(self) -> dict[str, bytes]:
        req = EchonetProperty(EchonetPropertyCode.route_b_id)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_route_b_id(res.edt)

    def get_one_minute_measured_cumulative_energy(self) -> dict[str, datetime.datetime |
                                                                     dict[str, int | float | None]]:
        req = EchonetProperty(EchonetPropertyCode.one_minute_measured_cumulative_energy)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_one_minute_measured_cumulative_energy(res.edt,
                                                                             self.energy_unit,
                                                                             self.energy_coefficient)

    def get_coefficient_for_cumulative_energy(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.coefficient_for_cumulative_energy)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_coefficient_for_cumulative_energy(res.edt)

    def get_number_of_effective_digits_for_cumulative_energy(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.number_of_effective_digits_for_cumulative_energy)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_number_of_effective_digits_for_cumulative_energy(res.edt)

    def get_measured_cumulative_energy(self,
                                       reverse: bool = False,
                                       ) -> int | float:
        if reverse is False:
            epc = EchonetPropertyCode.measured_cumulative_energy
        else:
            epc = EchonetPropertyCode.measured_cumulative_energy_reversed

        req = EchonetProperty(epc)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_measured_cumulative_energy(res.edt,
                                                                  self.energy_unit,
                                                                  self.energy_coefficient)

    def get_unit_for_cumulative_energy(self) -> int | float:
        req = EchonetProperty(EchonetPropertyCode.unit_for_cumulative_energy)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_unit_for_cumulative_energy(res.edt)

    def get_historical_cumulative_energy_1(self,
                                           day: int = 0,
                                           reverse: bool = False,
                                           ) -> list[dict[str, datetime.datetime | dict[str, int | float | None]]]:
        self.set_day_for_historical_data_1(day)

        if reverse is False:
            epc = EchonetPropertyCode.historical_cumulative_energy_1
        else:
            epc = EchonetPropertyCode.historical_cumulative_energy_1_reversed

        req = EchonetProperty(epc)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_historical_cumulative_energy_1(res.edt,
                                                                      self.energy_unit,
                                                                      self.energy_coefficient)

    def set_day_for_historical_data_1(self,
                                      day: int = 0,
                                      ) -> None:
        edt = EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(day)
        req = EchonetPropertyWithData(EchonetPropertyCode.day_for_historical_data_1, edt)
        self.__request_to_set([req])

    def get_day_for_historical_data_1(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.day_for_historical_data_1)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_day_for_historical_data_1(res.edt)

    def get_instantaneous_power(self) -> float:
        req = EchonetProperty(EchonetPropertyCode.instantaneous_power)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_instantaneous_power(res.edt)

    def get_instantaneous_current(self) -> dict[str, float]:
        req = EchonetProperty(EchonetPropertyCode.instantaneous_current)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_instantaneous_current(res.edt)

    def get_cumulative_energy_measured_at_fixed_time(self,
                                                     reverse: bool = False,
                                                     ) -> dict[str, datetime.datetime | int | float]:
        if reverse is False:
            epc = EchonetPropertyCode.cumulative_energy_measured_at_fixed_time
        else:
            epc = EchonetPropertyCode.cumulative_energy_measured_at_fixed_time_reversed

        req = EchonetProperty(epc)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_cumulative_energy_measured_at_fixed_time(res.edt,
                                                                                self.energy_unit,
                                                                                self.energy_coefficient)

    def get_historical_cumulative_energy_2(self,
                                           timestamp: datetime.datetime | None = None,
                                           num_of_data_points: int = 12,
                                           ) -> list[dict[str, datetime.datetime |
                                                               dict[str, int | float | None]]]:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.set_time_for_historical_data_2(timestamp, num_of_data_points)

        req = EchonetProperty(EchonetPropertyCode.historical_cumulative_energy_2)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_historical_cumulative_energy_2(res.edt,
                                                                      self.energy_unit,
                                                                      self.energy_coefficient)

    def set_time_for_historical_data_2(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 12,
                                       ) -> None:
        edt = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(timestamp,
                                                                               num_of_data_points)
        req = EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_2, edt)
        self.__request_to_set([req])

    def get_time_for_historical_data_2(self) -> dict[str, datetime.datetime | None | int]:
        req = EchonetProperty(EchonetPropertyCode.time_for_historical_data_2)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_time_for_historical_data_2(res.edt)

    def get_historical_cumulative_energy_3(self,
                                           timestamp: datetime.datetime | None = None,
                                           num_of_data_points: int = 10,
                                           ) -> list[dict[str, datetime.datetime |
                                                               dict[str, int | float | None]]]:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.set_time_for_historical_data_3(timestamp, num_of_data_points)

        req = EchonetProperty(EchonetPropertyCode.historical_cumulative_energy_3)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_historical_cumulative_energy_3(res.edt,
                                                                      self.energy_unit,
                                                                      self.energy_coefficient)

    def set_time_for_historical_data_3(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 10,
                                       ) -> None:
        edt = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_3(timestamp,
                                                                               num_of_data_points)
        req = EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_3, edt)
        self.__request_to_set([req])

    def get_time_for_historical_data_3(self) -> dict[str, datetime.datetime | None | int]:
        req = EchonetProperty(EchonetPropertyCode.time_for_historical_data_3)
        res = self.__request_to_get([req])[0]
        return EchonetDataParser.parse_time_for_historical_data_3(res.edt)

    class DayForHistoricalData1(TypedDict, total=False):
        day: int

    class TimeForHistoricalData2(TypedDict, total=False):
        timestamp: datetime.datetime
        num_of_data_points: int

    class TimeForHistoricalData3(TypedDict, total=False):
        timestamp: datetime.datetime
        num_of_data_points: int

    def request_to_set(self,
                       day_for_historical_data_1: DayForHistoricalData1 | None = None,
                       time_for_historical_data_2: TimeForHistoricalData2 | None = None,
                       time_for_historical_data_3: TimeForHistoricalData3 | None = None) -> None:
        properties_with_data = []
        if day_for_historical_data_1 is None and time_for_historical_data_2 is None and time_for_historical_data_3 is None:
            return
        if day_for_historical_data_1 is not None:
            edt = EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(**day_for_historical_data_1)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.day_for_historical_data_1, edt))
        if time_for_historical_data_2 is not None:
            edt = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(**time_for_historical_data_2)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_2, edt))
        if time_for_historical_data_3 is not None:
            edt = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_3(**time_for_historical_data_3)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_3, edt))

        self.__request_to_set(properties_with_data)

    def request_to_get(self,
                       properties: set[EchonetPropertyCode]) -> dict[EchonetPropertyCode, Any]:
        results = self.__request_to_get([EchonetProperty(epc) for epc in properties])
        parsed_results = {}
        for r in results:
            try:
                parser = parser_map[r.epc]
            except KeyError:
                raise MomongaRuntimeError('No parser found for EPC: %X' % r.epc)

            if parser in energy_parsers:
                parsed_results[r.epc] = parser(r.edt, self.energy_unit, self.energy_coefficient)
            else:
                parsed_results[r.epc] = parser(r.edt)

        return parsed_results
