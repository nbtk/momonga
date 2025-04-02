import datetime
import enum
import time
import queue
import logging

from typing import TypedDict, Any, Self

from .momonga_exception import (MomongaResponseNotExpected,
                                MomongaResponseNotPossible,
                                MomongaNeedToReopen)
from .momonga_response import SkEventRxUdp
from .momonga_session_manager import MomongaSessionManager
from .momonga_session_manager import logger as session_manager_logger
from .momonga_sk_wrapper import logger as sk_wrapper_logger

logger = logging.getLogger(__name__)


class EchonetServiceCode(enum.IntEnum):
    set_c = 0x61
    get = 0x62


class EchonetPropertyCode(enum.IntEnum):
    operation_status = 0x80
    coefficient_for_cumulative_energy = 0xD3
    number_of_effective_digits_for_cumulative_energy = 0xD7
    measured_cumulative_energy = 0xE0
    measured_cumulative_energy_reserved = 0xE3
    unit_for_cumulative_energy = 0xE1
    historical_cumulative_energy_1 = 0xE2
    historical_cumulative_energy_1_reversed = 0xE4
    day_for_historical_data_1 = 0xE5
    instantaneous_power = 0xE7
    instantaneous_current = 0xE8
    cumulative_energy_measured_at_fixed_time = 0xEA
    cumulative_energy_measured_at_fixed_time_reversed = 0xEB
    historical_cumulative_energy_2 = 0xEC
    time_for_historical_data_2 = 0xED
    historical_cumulative_energy_3 = 0xEE
    time_for_historical_data_3 = 0xEF


class EchonetProperty:
    def __init__(self,
                 epc: EchonetPropertyCode,
                 ):
        self.epc = epc


class EchonetPropertyWithData:
    def __init__(self,
                 epc: EchonetPropertyCode,
                 edt: bytes | None = None,
                 ):
        self.epc = epc
        self.edt = edt


class Momonga:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                 ) -> None:
        self.xmit_retry = 12
        self.recv_timeout = 12
        self.internal_xmit_interval = 5
        self.transaction_id = 0
        self.energy_coefficient = None
        self.energy_unit = None

        # the following value will be set a pyserial object.
        self.session_manager = MomongaSessionManager(rbid, pwd, dev, baudrate, reset_dev)

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, type, value, traceback) -> None:
        self.close()

    def open(self) -> Self:
        logger.info('Opening Momonga.')
        self.session_manager.open()
        time.sleep(self.internal_xmit_interval)
        self.__prepare_to_get_cumulative_energy()
        logger.info('Momonga is open.')
        return self

    def close(self):
        logger.info('Closing Momonga.')
        self.energy_coefficient = None
        self.energy_unit = None
        self.session_manager.close()
        logger.info('Momonga is closed.')

    def __get_transaction_id(self):
        self.transaction_id += 1
        return self.transaction_id

    @staticmethod
    def __build_request_header(tid: int, esv: EchonetServiceCode):
        ehd = b'\x10\x81'  # echonet lite edata format 1
        tid = tid.to_bytes(4, 'big')[-2:]
        seoj = b'\x05\xFF\x01'  # controller class
        deoj = b'\x02\x88\x01'  # low-voltage smart electric energy meter class
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
        if seoj != b'\x02\x88\x01':  # low-voltage smart electric energy meter class
            raise MomongaResponseNotExpected('The source is not a smart meter.')

        deoj = data[7:10]
        if deoj != b'\x05\xFF\x01':  # controller class
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
            epc = EchonetPropertyCode(data[cur])
            if epc != rp.epc:
                raise MomongaResponseNotExpected('The property code does not match. EPC: %X' % rp.epc)

            cur += 1
            pdc = data[cur]
            if pdc == 0:
                edt = None
            else:
                cur += 1
                edt_from = cur
                cur += pdc
                edt = data[edt_from:cur]

            properties.append(EchonetPropertyWithData(epc, edt))

        return properties

    def __request(self,
                  esv: EchonetServiceCode,
                  req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                  ) -> list[EchonetPropertyWithData]:
        tid = self.__get_transaction_id()
        if esv == EchonetServiceCode.set_c:
            tx_payload = self.__build_request_payload_with_data(tid, esv, req_properties)
        elif esv == EchonetServiceCode.get:
            tx_payload = self.__build_request_payload(tid, esv, req_properties)
        else:
            raise AssertionError('Unsupported service code')

        while not self.session_manager.recv_q.empty():
            self.session_manager.recv_q.get()  # drops stored data

        for _ in range(self.xmit_retry):
            self.session_manager.xmitter(tx_payload)
            while True:
                try:
                    res = self.session_manager.recv_q.get(timeout=self.recv_timeout)
                except queue.Empty:
                    logger.warning('The request for transaction id "%02X" timed out.' % tid)
                    break
                if res.startswith('EVENT 21'):
                    param = res.split()[-1]
                    if param == '00':
                        logger.info('Successfully transmitted a request packet for transaction id "%02X".' % tid)
                        continue
                    elif param == '01':
                        logger.info('Retransmitting the request packet for transaction id "%02X".' % tid)
                        time.sleep(self.internal_xmit_interval)
                        break  # to rexmit
                    elif param == '02':
                        logger.info('Transmitting neighbor solicitation packets.')
                        continue
                elif res.startswith('EVENT 02'):
                    logger.info('Received a neighbor advertisement packet.')
                    continue
                elif res.startswith('ERXUDP'):
                    udp_pkt = SkEventRxUdp([res])
                    if not (udp_pkt.src_port == udp_pkt.dst_port == 0x0E1A):
                        continue
                    elif udp_pkt.side != 0:
                        continue
                    elif udp_pkt.src_addr != self.session_manager.smart_meter_addr:
                        continue

                    try:
                        res_properties = self.__extract_response_payload(udp_pkt.data, tid, req_properties)
                    except MomongaResponseNotExpected:
                        continue

                    logger.info('Successfully received a response packet for transaction id "%02X".' % tid)
                    return res_properties

        logger.error('Gave up to obtain a response for transaction id "%02X". Close Momonga and open it again.' % tid)
        raise MomongaNeedToReopen('Gave up to obtain a response for transaction id "%02X".'
                                  ' Close Momonga and open it again.' % tid)

    def __request_to_set(self,
                         properties_with_data: list[EchonetPropertyWithData]
                         ) -> list[EchonetPropertyWithData]:
        return self.__request(EchonetServiceCode.set_c, properties_with_data)

    def __request_to_get(self,
                         properties: list[EchonetProperty],
                         ) -> list[EchonetPropertyWithData]:
        return self.__request(EchonetServiceCode.get, properties)

    def __prepare_to_get_cumulative_energy(self) -> None:
        try:
            self.energy_coefficient = self.get_coefficient_for_cumulative_energy()
            time.sleep(self.internal_xmit_interval)
        except MomongaResponseNotPossible:  # due to the property 0xD3 is optional.
            self.energy_coefficient = 1

        self.energy_unit = self.get_unit_for_cumulative_energy()
        time.sleep(self.internal_xmit_interval)

    @staticmethod
    def __build_edata_to_set_day_for_historical_data_1(day: int = 0) -> bytes:
        if day < 0 or day > 99:
            raise ValueError('The parameter "day" must be between 0 and 99.')

        return day.to_bytes(1, 'big')

    @staticmethod
    def __build_edata_to_set_time_for_historical_data_2(timestamp: datetime.datetime,
                                                        num_of_data_points: int = 12,
                                                        ) -> bytes:
        if num_of_data_points < 1 or num_of_data_points > 12:
            raise ValueError('The parameter "num_of_data_points" must be between 1 and 12.')

        if timestamp.year < 1 or timestamp.year > 9999:
            raise ValueError('The year specified by the parameter "timestamp" must be between 1 and 9999.')

        year = timestamp.year.to_bytes(2, 'big')
        month = timestamp.month.to_bytes(1, 'big')
        day = timestamp.day.to_bytes(1, 'big')
        hour = timestamp.hour.to_bytes(1, 'big')
        if 0 <= timestamp.minute < 30:
            minute = 0
        else:
            minute = 30

        minute = minute.to_bytes(1, 'big')
        num_of_data_points = num_of_data_points.to_bytes(1, 'big')
        return year + month + day + hour + minute + num_of_data_points

    @staticmethod
    def __build_edata_to_set_time_for_historical_data_3(timestamp: datetime.datetime,
                                                        num_of_data_points: int = 10,
                                                        ) -> bytes:
        if num_of_data_points < 1 or num_of_data_points > 10:
            raise ValueError('The parameter "num_of_data_points" must be between 1 and 10.')

        if timestamp.year < 1 or timestamp.year > 9999:
            raise ValueError('The year specified by the parameter "timestamp" must be between 1 and 9999.')

        year = timestamp.year.to_bytes(2, 'big')
        month = timestamp.month.to_bytes(1, 'big')
        day = timestamp.day.to_bytes(1, 'big')
        hour = timestamp.hour.to_bytes(1, 'big')
        minute = timestamp.minute.to_bytes(1, 'big')
        num_of_data_points = num_of_data_points.to_bytes(1, 'big')
        return year + month + day + hour + minute + num_of_data_points

    @staticmethod
    def __parse_operation_status(edt: bytes) -> bool | None:
        status = int.from_bytes(edt, 'big')
        if status == 0x30:  # turned on
            status = True
        elif status == 0x31:  # turned off
            status = False
        else:
            status = None  # unknown

        return status

    @staticmethod
    def __parse_coefficient_for_cumulative_energy(edt: bytes) -> int:
        coefficient = int.from_bytes(edt, 'big')
        return coefficient

    @staticmethod
    def __parse_number_of_effective_digits_for_cumulative_energy(edt: bytes) -> int:
        digits = int.from_bytes(edt, 'big')
        return digits

    def __parse_measured_cumulative_energy(self, edt: bytes) -> int | float:
        if self.energy_coefficient is None:
            raise AssertionError(
                'The parameter "energy_coefficient" must be resolved before parsing "cumulative_energy".')
        if self.energy_unit is None:
            raise AssertionError(
                'The parameter "energy_unit" must be resolved before parsing "cumulative_energy".')

        cumulative_energy = int.from_bytes(edt, 'big')
        cumulative_energy *= self.energy_coefficient
        cumulative_energy *= self.energy_unit
        return cumulative_energy

    @staticmethod
    def __parse_unit_for_cumulative_energy(edt: bytes) -> int | float:
        unit_index = int.from_bytes(edt, 'big')
        unit_map = {0x00: 1,
                    0x01: 0.1,
                    0x02: 0.01,
                    0x03: 0.001,
                    0x04: 0.0001,
                    0x0A: 10,
                    0x0B: 100,
                    0x0C: 1000,
                    0x0D: 10000}
        unit = unit_map.get(unit_index)
        if unit is None:
            raise AssertionError('Obtained unit for cumulative energy (%X) is not defined.' % unit_index)

        return unit

    def __parse_historical_cumulative_energy_1(self,
                                               edt: bytes,
                                               ) -> list[dict[str: datetime.datetime,
                                                         str: dict[str: int | float | None,
                                                              str: int | float | None]]]:
        if self.energy_coefficient is None:
            raise AssertionError(
                'The parameter "energy_coefficient" must be resolved before parsing "cumulative_energy".')
        if self.energy_unit is None:
            raise AssertionError(
                'The parameter "energy_unit" must be resolved before parsing "cumulative_energy".')

        day = int.from_bytes(edt[0:2], 'big')
        timestamp = datetime.datetime.combine(datetime.date.today(), datetime.datetime.min.time())
        timestamp -= datetime.timedelta(days=day)
        energy_data_points = edt[2:]
        historical_cumulative_energy = []
        for i in range(48):
            j = i * 4
            cumulative_energy = int.from_bytes(energy_data_points[j:j + 4], 'big')
            if cumulative_energy == 0xFFFFFFFE:
                cumulative_energy = None
            else:
                cumulative_energy *= self.energy_coefficient
                cumulative_energy *= self.energy_unit
            historical_cumulative_energy.append({'timestamp': timestamp, 'cumulative energy': cumulative_energy})
            timestamp += datetime.timedelta(minutes=30)

        return historical_cumulative_energy

    @staticmethod
    def __parse_day_for_historical_data_1(edt: bytes) -> int:
        day = int.from_bytes(edt, 'big')
        return day

    @staticmethod
    def __parse_instantaneous_power(edt: bytes) -> float:
        power = int.from_bytes(edt, 'big', signed=True)
        return power

    @staticmethod
    def __parse_instantaneous_current(edt: bytes) -> dict[str: float, str: float]:
        r_phase_current = int.from_bytes(edt[0:2], 'big', signed=True)
        t_phase_current = int.from_bytes(edt[2:4], 'big', signed=True)
        r_phase_current *= 0.1  # to Ampere
        t_phase_current *= 0.1  # to Ampere
        return {'r phase current': r_phase_current, 't phase current': t_phase_current}

    def __parse_cumulative_energy_measured_at_fixed_time(self,
                                                         edt: bytes,
                                                         ) -> dict[str: datetime.datetime,
                                                              str: int | float]:
        if self.energy_coefficient is None:
            raise AssertionError(
                'The parameter "energy_coefficient" must be resolved before parsing "cumulative_energy".')
        if self.energy_unit is None:
            raise AssertionError(
                'The parameter "energy_unit" must be resolved before parsing "cumulative_energy".')

        timestamp = datetime.datetime(int.from_bytes(edt[0:2], 'big'),
                                      edt[2], edt[3], edt[4], edt[5], edt[6])
        cumulative_energy = int.from_bytes(edt[7:], 'big')
        cumulative_energy *= self.energy_coefficient
        cumulative_energy *= self.energy_unit
        return {'timestamp': timestamp, 'cumulative_energy': cumulative_energy}

    def __parse_historical_cumulative_energy_2(self,
                                               edt: bytes,
                                               ) -> list[dict[str: datetime.datetime,
                                                         str: dict[str: int | float | None,
                                                              str: int | float | None]]]:
        if self.energy_coefficient is None:
            raise AssertionError(
                'The parameter "energy_coefficient" must be resolved before parsing "cumulative_energy".')
        if self.energy_unit is None:
            raise AssertionError(
                'The parameter "energy_unit" must be resolved before parsing "cumulative_energy".')

        year = int.from_bytes(edt[0:2], 'big')
        num_of_data_points = edt[6]
        energy_data_points = edt[7:]
        timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])
        historical_cumulative_energy = []
        for i in range(num_of_data_points):
            j = i * 8
            normal_direction_energy = int.from_bytes(energy_data_points[j:j + 4], 'big')
            if normal_direction_energy == 0xFFFFFFFE:
                normal_direction_energy = None
            else:
                normal_direction_energy *= self.energy_coefficient
                normal_direction_energy *= self.energy_unit

            reverse_direction_energy = int.from_bytes(energy_data_points[j + 4:j + 8], 'big')
            if reverse_direction_energy == 0xFFFFFFFE:
                reverse_direction_energy = None
            else:
                reverse_direction_energy *= self.energy_coefficient
                reverse_direction_energy *= self.energy_unit

            historical_cumulative_energy.append(
                {'timestamp': timestamp,
                 'cumulative energy': {'normal direction': normal_direction_energy,
                                       'reverse direction': reverse_direction_energy}})
            timestamp -= datetime.timedelta(minutes=30)

        return historical_cumulative_energy

    @staticmethod
    def __parse_time_for_historical_data_2(edt) -> dict[str: datetime.datetime | None, str: int]:
        year = int.from_bytes(edt[0:2], 'big')
        if year == 0xFFFF:
            timestamp = None
        else:
            timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])

        num_of_data_points = edt[6]
        return {'timestamp': timestamp,
                'number of data points': num_of_data_points}

    def __parse_historical_cumulative_energy_3(self,
                                               edt: bytes,
                                               ) -> list[dict[str: datetime.datetime,
                                                         str: dict[str: int | float | None,
                                                              str: int | float | None]]]:
        if self.energy_coefficient is None:
            raise AssertionError(
                'The parameter "energy_coefficient" must be resolved before parsing "cumulative_energy".')
        if self.energy_unit is None:
            raise AssertionError(
                'The parameter "energy_unit" must be resolved before parsing "cumulative_energy".')

        year = int.from_bytes(edt[0:2], 'big')
        num_of_data_points = edt[6]
        energy_data_points = edt[7:]
        timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])
        historical_cumulative_energy = []
        for i in range(num_of_data_points):
            j = i * 8
            normal_direction_energy = int.from_bytes(energy_data_points[j:j + 4], 'big')
            if normal_direction_energy == 0xFFFFFFFE:
                normal_direction_energy = None
            else:
                normal_direction_energy *= self.energy_coefficient
                normal_direction_energy *= self.energy_unit

            reverse_direction_energy = int.from_bytes(energy_data_points[j + 4:j + 8], 'big')
            if reverse_direction_energy == 0xFFFFFFFE:
                reverse_direction_energy = None
            else:
                reverse_direction_energy *= self.energy_coefficient
                reverse_direction_energy *= self.energy_unit

            historical_cumulative_energy.append(
                {'timestamp': timestamp,
                 'cumulative energy': {'normal direction': normal_direction_energy,
                                       'reverse direction': reverse_direction_energy}})
            timestamp -= datetime.timedelta(minutes=1)

        return historical_cumulative_energy

    @staticmethod
    def __parse_time_for_historical_data_3(edt) -> dict[str: datetime.datetime | None, str: int]:
        year = int.from_bytes(edt[0:2], 'big')
        if year == 0xFFFF:
            timestamp = None
        else:
            timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])

        num_of_data_points = edt[6]
        return {'timestamp': timestamp,
                'number of data points': num_of_data_points}

    def get_operation_status(self) -> bool | None:
        req = EchonetProperty(EchonetPropertyCode.operation_status)
        res = self.__request_to_get([req])[0]
        return self.__parse_operation_status(res.edt)

    def get_coefficient_for_cumulative_energy(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.coefficient_for_cumulative_energy)
        res = self.__request_to_get([req])[0]
        return self.__parse_coefficient_for_cumulative_energy(res.edt)

    def get_number_of_effective_digits_for_cumulative_energy(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.number_of_effective_digits_for_cumulative_energy)
        res = self.__request_to_get([req])[0]
        return self.__parse_number_of_effective_digits_for_cumulative_energy(res.edt)

    def get_measured_cumulative_energy(self,
                                       reverse: bool = False,
                                       ) -> int | float:
        if reverse is False:
            epc = EchonetPropertyCode.measured_cumulative_energy
        else:
            epc = EchonetPropertyCode.measured_cumulative_energy_reserved

        req = EchonetProperty(epc)
        res = self.__request_to_get([req])[0]
        return self.__parse_measured_cumulative_energy(res.edt)

    def get_unit_for_cumulative_energy(self) -> int | float:
        req = EchonetProperty(EchonetPropertyCode.unit_for_cumulative_energy)
        res = self.__request_to_get([req])[0]
        return self.__parse_unit_for_cumulative_energy(res.edt)

    def get_historical_cumulative_energy_1(self,
                                           day: int = 0,
                                           reverse: bool = False,
                                           ) -> list[dict[str: datetime.datetime,
                                                     str: dict[str: int | float | None,
                                                          str: int | float | None]]]:
        self.set_day_for_historical_data_1(day)

        if reverse is False:
            epc = EchonetPropertyCode.historical_cumulative_energy_1
        else:
            epc = EchonetPropertyCode.historical_cumulative_energy_1_reversed

        req = EchonetProperty(epc)
        res = self.__request_to_get([req])[0]
        return self.__parse_historical_cumulative_energy_1(res.edt)

    def set_day_for_historical_data_1(self,
                                      day: int = 0,
                                      ) -> None:
        edt = self.__build_edata_to_set_day_for_historical_data_1(day)
        req = EchonetPropertyWithData(EchonetPropertyCode.day_for_historical_data_1, edt)
        self.__request_to_set([req])

    def get_day_for_historical_data_1(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.day_for_historical_data_1)
        res = self.__request_to_get([req])[0]
        return self.__parse_day_for_historical_data_1(res.edt)

    def get_instantaneous_power(self) -> float:
        req = EchonetProperty(EchonetPropertyCode.instantaneous_power)
        res = self.__request_to_get([req])[0]
        return self.__parse_instantaneous_power(res.edt)

    def get_instantaneous_current(self) -> dict[str: float, str: float]:
        req = EchonetProperty(EchonetPropertyCode.instantaneous_current)
        res = self.__request_to_get([req])[0]
        return self.__parse_instantaneous_current(res.edt)

    def get_cumulative_energy_measured_at_fixed_time(self,
                                                     reverse: bool = False,
                                                     ) -> dict[str: datetime.datetime,
                                                          str: int | float]:

        if reverse is False:
            epc = EchonetPropertyCode.cumulative_energy_measured_at_fixed_time
        else:
            epc = EchonetPropertyCode.cumulative_energy_measured_at_fixed_time_reversed

        req = EchonetProperty(epc)
        res = self.__request_to_get([req])[0]
        return self.__parse_cumulative_energy_measured_at_fixed_time(res.edt)

    def get_historical_cumulative_energy_2(self,
                                           timestamp: datetime.datetime = None,
                                           num_of_data_points: int = 12,
                                           ) -> list[dict[str: datetime.datetime,
                                                     str: dict[str: int | float | None,
                                                          str: int | float | None]]]:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.set_time_for_historical_data_2(timestamp, num_of_data_points)

        req = EchonetProperty(EchonetPropertyCode.historical_cumulative_energy_2)
        res = self.__request_to_get([req])[0]
        return self.__parse_historical_cumulative_energy_2(res.edt)

    def set_time_for_historical_data_2(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 12,
                                       ) -> None:
        edt = self.__build_edata_to_set_time_for_historical_data_2(timestamp,
                                                                   num_of_data_points)
        req = EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_2, edt)
        self.__request_to_set([req])

    def get_time_for_historical_data_2(self) -> dict[str: datetime.datetime | None, str: int]:
        req = EchonetProperty(EchonetPropertyCode.time_for_historical_data_2)
        res = self.__request_to_get([req])[0]
        return self.__parse_time_for_historical_data_2(res.edt)

    def get_historical_cumulative_energy_3(self,
                                           timestamp: datetime.datetime = None,
                                           num_of_data_points: int = 10,
                                           ) -> list[dict[str: datetime.datetime,
                                                     str: dict[str: int | float | None,
                                                          str: int | float | None]]]:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.set_time_for_historical_data_3(timestamp, num_of_data_points)

        req = EchonetProperty(EchonetPropertyCode.historical_cumulative_energy_3)
        res = self.__request_to_get([req])[0]
        return self.__parse_historical_cumulative_energy_3(res.edt)

    def set_time_for_historical_data_3(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 10,
                                       ) -> None:
        edt = self.__build_edata_to_set_time_for_historical_data_3(timestamp,
                                                                   num_of_data_points)
        req = EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_3, edt)
        self.__request_to_set([req])

    def get_time_for_historical_data_3(self) -> dict[str: datetime.datetime | None, str: int]:
        req = EchonetProperty(EchonetPropertyCode.time_for_historical_data_3)
        res = self.__request_to_get([req])[0]
        return self.__parse_time_for_historical_data_3(res.edt)

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
        if day_for_historical_data_1 is not None:
            edt = self.__build_edata_to_set_day_for_historical_data_1(**day_for_historical_data_1)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.day_for_historical_data_1, edt))
        if time_for_historical_data_2 is not None:
            edt = self.__build_edata_to_set_time_for_historical_data_2(**time_for_historical_data_2)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_2, edt))
        if time_for_historical_data_3 is not None:
            edt = self.__build_edata_to_set_time_for_historical_data_3(**time_for_historical_data_3)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_3, edt))

        self.__request_to_set(properties_with_data)

    def request_to_get(self,
                       properties: set[EchonetPropertyCode]) -> dict[EchonetPropertyCode, Any]:
        results = self.__request_to_get([EchonetProperty(epc) for epc in properties])
        parsed_results = {}
        for r in results:
            if r.epc == EchonetPropertyCode.operation_status:
                parsed_results[r.epc] = self.__parse_operation_status(r.edt)
            elif r.epc == EchonetPropertyCode.coefficient_for_cumulative_energy:
                parsed_results[r.epc] = self.__parse_coefficient_for_cumulative_energy(r.edt)
            elif r.epc == EchonetPropertyCode.number_of_effective_digits_for_cumulative_energy:
                parsed_results[r.epc] = self.__parse_number_of_effective_digits_for_cumulative_energy(r.edt)
            elif r.epc == EchonetPropertyCode.measured_cumulative_energy:
                parsed_results[r.epc] = self.__parse_measured_cumulative_energy(r.edt)
            elif r.epc == EchonetPropertyCode.measured_cumulative_energy_reserved:
                parsed_results[r.epc] = self.__parse_measured_cumulative_energy(r.edt)
            elif r.epc == EchonetPropertyCode.unit_for_cumulative_energy:
                parsed_results[r.epc] = self.__parse_unit_for_cumulative_energy(r.edt)
            elif r.epc == EchonetPropertyCode.historical_cumulative_energy_1:
                parsed_results[r.epc] = self.__parse_historical_cumulative_energy_1(r.edt)
            elif r.epc == EchonetPropertyCode.historical_cumulative_energy_1_reversed:
                parsed_results[r.epc] = self.__parse_historical_cumulative_energy_1(r.edt)
            elif r.epc == EchonetPropertyCode.day_for_historical_data_1:
                parsed_results[r.epc] = self.__parse_day_for_historical_data_1(r.edt)
            elif r.epc == EchonetPropertyCode.instantaneous_power:
                parsed_results[r.epc] = self.__parse_instantaneous_power(r.edt)
            elif r.epc == EchonetPropertyCode.instantaneous_current:
                parsed_results[r.epc] = self.__parse_instantaneous_current(r.edt)
            elif r.epc == EchonetPropertyCode.cumulative_energy_measured_at_fixed_time:
                parsed_results[r.epc] = self.__parse_cumulative_energy_measured_at_fixed_time(r.edt)
            elif r.epc == EchonetPropertyCode.cumulative_energy_measured_at_fixed_time_reversed:
                parsed_results[r.epc] = self.__parse_cumulative_energy_measured_at_fixed_time(r.edt)
            elif r.epc == EchonetPropertyCode.historical_cumulative_energy_2:
                parsed_results[r.epc] = self.__parse_historical_cumulative_energy_2(r.edt)
            elif r.epc == EchonetPropertyCode.time_for_historical_data_2:
                parsed_results[r.epc] = self.__parse_time_for_historical_data_2(r.edt)
            elif r.epc == EchonetPropertyCode.historical_cumulative_energy_3:
                parsed_results[r.epc] = self.__parse_historical_cumulative_energy_3(r.edt)
            elif r.epc == EchonetPropertyCode.time_for_historical_data_3:
                parsed_results[r.epc] = self.__parse_time_for_historical_data_3(r.edt)
            else:
                raise AssertionError(f"No parser found for EPC: %02X" % r.epc)

        return parsed_results
