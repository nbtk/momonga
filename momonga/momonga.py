import datetime
import enum
import time
import queue
import logging

from typing import TypedDict, Any, Self

from .momonga_exception import (MomongaResponseNotExpected,
                                MomongaResponseNotPossible,
                                MomongaNeedToReopen,
                                MomongaRuntimeError)
from .momonga_response import SkEventRxUdp
from .momonga_session_manager import MomongaSessionManager
from .momonga_session_manager import logger as session_manager_logger
from .momonga_sk_wrapper import logger as sk_wrapper_logger

logger = logging.getLogger(__name__)


class EchonetServiceCode(enum.IntEnum):
    set_c: int = 0x61
    get: int = 0x62


class EchonetPropertyCode(enum.IntEnum):
    operation_status: int = 0x80
    installation_location: int = 0x81
    standard_version_information: int = 0x82
    fault_status: int = 0x88
    manufacturer_code: int = 0x8A
    serial_number: int = 0x8D
    current_time_setting: int = 0x97
    current_date_setting: int = 0x98
    properties_for_status_notification: int = 0x9D
    properties_to_set_values: int = 0x9E
    properties_to_get_values: int = 0x9F
    route_b_id: int = 0xC0
    one_minute_measured_cumulative_energy: int = 0xD0
    coefficient_for_cumulative_energy: int = 0xD3
    number_of_effective_digits_for_cumulative_energy: int = 0xD7
    measured_cumulative_energy: int = 0xE0
    measured_cumulative_energy_reversed: int = 0xE3
    unit_for_cumulative_energy: int = 0xE1
    historical_cumulative_energy_1: int = 0xE2
    historical_cumulative_energy_1_reversed: int = 0xE4
    day_for_historical_data_1: int = 0xE5
    instantaneous_power: int = 0xE7
    instantaneous_current: int = 0xE8
    cumulative_energy_measured_at_fixed_time: int = 0xEA
    cumulative_energy_measured_at_fixed_time_reversed: int = 0xEB
    historical_cumulative_energy_2: int = 0xEC
    time_for_historical_data_2: int = 0xED
    historical_cumulative_energy_3: int = 0xEE
    time_for_historical_data_3: int = 0xEF


class EchonetProperty:
    def __init__(self,
                 epc: EchonetPropertyCode | int,
                 ) -> None:
        self.epc = epc


class EchonetPropertyWithData:
    def __init__(self,
                 epc: EchonetPropertyCode | int,
                 edt: bytes | None = None,
                 ) -> None:
        self.epc = epc
        self.edt = edt


class DayForHistoricalData1(TypedDict, total=False):
    day: int


class TimeForHistoricalData2(TypedDict, total=False):
    timestamp: datetime.datetime
    num_of_data_points: int


class TimeForHistoricalData3(TypedDict, total=False):
    timestamp: datetime.datetime
    num_of_data_points: int


class Momonga:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                 ) -> None:
        self.xmit_retries: int = 12
        self.recv_timeout: int | float = 12
        self.internal_xmit_interval: int | float = 5
        self.transaction_id: int = 0
        self.energy_coefficient: int | None = None
        self.energy_unit: int | float | None = None

        self.parser_map: dict[EchonetPropertyCode, callable] = {
            EchonetPropertyCode.operation_status: self.parse_operation_status,
            EchonetPropertyCode.installation_location: self.parse_installation_location,
            EchonetPropertyCode.standard_version_information: self.parse_standard_version_information,
            EchonetPropertyCode.fault_status: self.parse_fault_status,
            EchonetPropertyCode.manufacturer_code: self.parse_manufacturer_code,
            EchonetPropertyCode.serial_number: self.parse_serial_number,
            EchonetPropertyCode.current_time_setting: self.parse_current_time_setting,
            EchonetPropertyCode.current_date_setting: self.parse_current_date_setting,
            EchonetPropertyCode.properties_for_status_notification: self.parse_property_map,
            EchonetPropertyCode.properties_to_set_values: self.parse_property_map,
            EchonetPropertyCode.properties_to_get_values: self.parse_property_map,
            EchonetPropertyCode.route_b_id: self.parse_route_b_id,
            EchonetPropertyCode.one_minute_measured_cumulative_energy: self.parse_one_minute_measured_cumulative_energy,
            EchonetPropertyCode.coefficient_for_cumulative_energy: self.parse_coefficient_for_cumulative_energy,
            EchonetPropertyCode.number_of_effective_digits_for_cumulative_energy: self.parse_number_of_effective_digits_for_cumulative_energy,
            EchonetPropertyCode.measured_cumulative_energy: self.parse_measured_cumulative_energy,
            EchonetPropertyCode.measured_cumulative_energy_reversed: self.parse_measured_cumulative_energy,
            EchonetPropertyCode.unit_for_cumulative_energy: self.parse_unit_for_cumulative_energy,
            EchonetPropertyCode.historical_cumulative_energy_1: self.parse_historical_cumulative_energy_1,
            EchonetPropertyCode.historical_cumulative_energy_1_reversed: self.parse_historical_cumulative_energy_1,
            EchonetPropertyCode.day_for_historical_data_1: self.parse_day_for_historical_data_1,
            EchonetPropertyCode.instantaneous_power: self.parse_instantaneous_power,
            EchonetPropertyCode.instantaneous_current: self.parse_instantaneous_current,
            EchonetPropertyCode.cumulative_energy_measured_at_fixed_time: self.parse_cumulative_energy_measured_at_fixed_time,
            EchonetPropertyCode.cumulative_energy_measured_at_fixed_time_reversed: self.parse_cumulative_energy_measured_at_fixed_time,
            EchonetPropertyCode.historical_cumulative_energy_2: self.parse_historical_cumulative_energy_2,
            EchonetPropertyCode.time_for_historical_data_2: self.parse_time_for_historical_data_2,
            EchonetPropertyCode.historical_cumulative_energy_3: self.parse_historical_cumulative_energy_3,
            EchonetPropertyCode.time_for_historical_data_3: self.parse_time_for_historical_data_3,
        }

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
        self.__init_coefficient_for_cumulative_energy()
        logger.info('Momonga is open.')
        return self

    def close(self) -> None:
        logger.info('Closing Momonga.')
        self.energy_coefficient = None
        self.energy_unit = None
        self.session_manager.close()
        logger.info('Momonga is closed.')

    def __get_transaction_id(self) -> int:
        self.transaction_id += 1
        return self.transaction_id

    @staticmethod
    def __build_request_header(tid: int, esv: EchonetServiceCode) -> bytes:
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

                    logger.info('Successfully received a response packet for transaction id "%04X".' % tid)
                    return res_properties
                else:
                    # this line should never be reached.
                    continue
        logger.error('Gave up to obtain a response for transaction id "%04X". Close Momonga and open it again.' % tid)
        raise MomongaNeedToReopen('Gave up to obtain a response for transaction id "%04X".'
                                  ' Close Momonga and open it again.' % tid)

    def request_to_set_raw(self,
                           properties_with_data: list[EchonetPropertyWithData]
                           ) -> None:
        self.__request(EchonetServiceCode.set_c, properties_with_data)

    def request_to_get_raw(self,
                           properties: list[EchonetProperty],
                           ) -> list[EchonetPropertyWithData]:
        return self.__request(EchonetServiceCode.get, properties)

    def __init_coefficient_for_cumulative_energy(self, force_reload: bool = False) -> int | float:
        if self.energy_coefficient is None or force_reload is True:
            try:
                self.energy_coefficient = self.get_coefficient_for_cumulative_energy()
                time.sleep(self.internal_xmit_interval)
            except MomongaResponseNotPossible:  # due to the property 0xD3 is optional.
                self.energy_coefficient = 1

        if self.energy_unit is None or force_reload is True:
            self.energy_unit = self.get_unit_for_cumulative_energy()
            time.sleep(self.internal_xmit_interval)

        return self.energy_coefficient * self.energy_unit

    @staticmethod
    def build_edata_to_set_day_for_historical_data_1(day: int = 0) -> bytes:
        if day < 0 or day > 99:
            raise ValueError('The parameter "day" must be between 0 and 99.')

        return day.to_bytes(1, 'big')

    @staticmethod
    def build_edata_to_set_time_for_historical_data_2(timestamp: datetime.datetime,
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
    def build_edata_to_set_time_for_historical_data_3(timestamp: datetime.datetime,
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
    def parse_operation_status(edt: bytes) -> bool | None:
        status = int.from_bytes(edt, 'big')
        if status == 0x30:  # turned on
            status = True
        elif status == 0x31:  # turned off
            status = False
        else:
            status = None  # unknown

        return status

    @staticmethod
    def parse_installation_location(edt: bytes) -> str:
        code = edt[0]
        if code == 0x00:
            location = 'location not set'
        elif code == 0x01:
            location = 'location information: ' + edt[1:].hex()
        elif 0x02 <= code <= 0x07:  # reserved for future use
            location = 'not implemented'
        elif 0x08 <= code <= 0x7F:
            location_map = {
                1: 'living room',
                2: 'dining room',
                3: 'kitchen',
                4: 'bathroom',
                5: 'toilet',
                6: 'washroom',
                7: 'hallway',
                8: 'room',
                9: 'stairs',
                10: 'entrance',
                11: 'storage room',
                12: 'garden/perimeter',
                13: 'garage',
                14: 'veranda',
                15: 'other',
            }
            location_code = code >> 3
            location = location_map[location_code]
            location += ' ' + str(code & 0x07)
        elif 0x80 <= code <= 0xFE:  # reserved for future use
            location = 'not implemented'
        elif code == 0xFF:
            location = 'location not fixed'
        else:
            location = 'unknown'

        return location

    @staticmethod
    def parse_standard_version_information(edt: bytes) -> str:
        version = ''
        if edt[0] > 0:
            version += chr(edt[0])
        if edt[1] > 0:
            version += chr(edt[1])
        return version + chr(edt[2]) + '.' + str(edt[3])

    @staticmethod
    def parse_fault_status(edt: bytes) -> bool:
        status_code = int.from_bytes(edt, 'big')
        if status_code == 0x41:
            status = True  # fault occurred
        elif status_code == 0x42:
            status = False  # no fault occurred
        else:
            status = None  # unknown

        return status

    @staticmethod
    def parse_manufacturer_code(edt: bytes) -> bytes:
        return edt

    @staticmethod
    def parse_serial_number(edt: bytes) -> str:
        return edt.decode()

    @staticmethod
    def parse_current_time_setting(edt: bytes) -> datetime.time:
        hour = edt[0]
        minute = edt[1]
        return datetime.time(hour=hour, minute=minute, second=0)

    @staticmethod
    def parse_current_date_setting(edt: bytes) -> datetime.date:
        year = int.from_bytes(edt[0:2], 'big')
        month = edt[2]
        day = edt[3]
        return datetime.date(year=year, month=month, day=day)

    @staticmethod
    def parse_property_map(edt: bytes) -> set[EchonetPropertyCode | int]:
        num_of_properties = edt[0]
        property_map = edt[1:]
        properties = set()
        if num_of_properties < 16:
            for prop_code in property_map:
                try:
                    prop_code = EchonetPropertyCode(prop_code)
                except ValueError:
                    pass

                properties.add(prop_code)
        else:
            for i in range(len(property_map)):
                b = property_map[i]
                for j in range(8):
                    if b & 1 << j:
                        prop_code = (j + 0x08 << 4) + i
                        try:
                            prop_code = EchonetPropertyCode(prop_code)
                        except ValueError:
                            pass

                        properties.add(prop_code)

        return properties

    @staticmethod
    def parse_route_b_id(edt: bytes) -> dict[str, bytes]:
        manufacturer_code = edt[1:4]
        authentication_id = edt[4:]
        return {'manufacturer code': manufacturer_code, 'authentication id': authentication_id}

    def parse_one_minute_measured_cumulative_energy(self, edt: bytes) -> dict[str, datetime.datetime |
                                                                                   dict[str, int | float | None]]:
        coefficient_for_cumulative_energy = self.__init_coefficient_for_cumulative_energy()
        timestamp = datetime.datetime(int.from_bytes(edt[0:2], 'big'),
                                      edt[2], edt[3], edt[4], edt[5], edt[6])

        normal_direction_energy = int.from_bytes(edt[7:11], 'big')
        if normal_direction_energy == 0xFFFFFFFE:
            normal_direction_energy = None
        else:
            normal_direction_energy *= coefficient_for_cumulative_energy

        reverse_direction_energy = int.from_bytes(edt[11:15], 'big')
        if reverse_direction_energy == 0xFFFFFFFE:
            reverse_direction_energy = None
        else:
            reverse_direction_energy *= coefficient_for_cumulative_energy

        return {'timestamp': timestamp,
                'cumulative energy': {'normal direction': normal_direction_energy,
                                      'reverse direction': reverse_direction_energy}}

    @staticmethod
    def parse_coefficient_for_cumulative_energy(edt: bytes) -> int:
        coefficient = int.from_bytes(edt, 'big')
        return coefficient

    @staticmethod
    def parse_number_of_effective_digits_for_cumulative_energy(edt: bytes) -> int:
        digits = int.from_bytes(edt, 'big')
        return digits

    def parse_measured_cumulative_energy(self, edt: bytes) -> int | float:
        coefficient_for_cumulative_energy = self.__init_coefficient_for_cumulative_energy()
        cumulative_energy = int.from_bytes(edt, 'big')
        cumulative_energy *= coefficient_for_cumulative_energy
        return cumulative_energy

    @staticmethod
    def parse_unit_for_cumulative_energy(edt: bytes) -> int | float:
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
            raise MomongaRuntimeError('Obtained unit for cumulative energy (%X) is not defined.' % unit_index)

        return unit

    def parse_historical_cumulative_energy_1(self,
                                             edt: bytes,
                                             ) -> list[dict[str, datetime.datetime | int | float | None]]:
        coefficient_for_cumulative_energy = self.__init_coefficient_for_cumulative_energy()
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
                cumulative_energy *= coefficient_for_cumulative_energy
            historical_cumulative_energy.append({'timestamp': timestamp, 'cumulative energy': cumulative_energy})
            timestamp += datetime.timedelta(minutes=30)

        return historical_cumulative_energy

    @staticmethod
    def parse_day_for_historical_data_1(edt: bytes) -> int:
        day = int.from_bytes(edt, 'big')
        return day

    @staticmethod
    def parse_instantaneous_power(edt: bytes) -> float:
        power = int.from_bytes(edt, 'big', signed=True)
        return power

    @staticmethod
    def parse_instantaneous_current(edt: bytes) -> dict[str, float]:
        r_phase_current = int.from_bytes(edt[0:2], 'big', signed=True)
        t_phase_current = int.from_bytes(edt[2:4], 'big', signed=True)
        r_phase_current *= 0.1  # to Ampere
        t_phase_current *= 0.1  # to Ampere
        return {'r phase current': r_phase_current, 't phase current': t_phase_current}

    def parse_cumulative_energy_measured_at_fixed_time(self,
                                                       edt: bytes,
                                                       ) -> dict[str, datetime.datetime | int | float]:
        coefficient_for_cumulative_energy = self.__init_coefficient_for_cumulative_energy()
        timestamp = datetime.datetime(int.from_bytes(edt[0:2], 'big'),
                                      edt[2], edt[3], edt[4], edt[5], edt[6])
        cumulative_energy = int.from_bytes(edt[7:], 'big')
        cumulative_energy *= coefficient_for_cumulative_energy
        return {'timestamp': timestamp, 'cumulative energy': cumulative_energy}

    def parse_historical_cumulative_energy_2(self,
                                             edt: bytes,
                                             ) -> list[dict[str, datetime.datetime |
                                                                 dict[str, int | float | None]]]:
        coefficient_for_cumulative_energy = self.__init_coefficient_for_cumulative_energy()
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
                normal_direction_energy *= coefficient_for_cumulative_energy

            reverse_direction_energy = int.from_bytes(energy_data_points[j + 4:j + 8], 'big')
            if reverse_direction_energy == 0xFFFFFFFE:
                reverse_direction_energy = None
            else:
                reverse_direction_energy *= coefficient_for_cumulative_energy

            historical_cumulative_energy.append(
                {'timestamp': timestamp,
                 'cumulative energy': {'normal direction': normal_direction_energy,
                                       'reverse direction': reverse_direction_energy}})
            timestamp -= datetime.timedelta(minutes=30)

        return historical_cumulative_energy

    @staticmethod
    def parse_time_for_historical_data_2(edt: bytes) -> dict[str, datetime.datetime | None | int]:
        year = int.from_bytes(edt[0:2], 'big')
        if year == 0xFFFF:
            timestamp = None
        else:
            timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])

        num_of_data_points = edt[6]
        return {'timestamp': timestamp,
                'number of data points': num_of_data_points}

    def parse_historical_cumulative_energy_3(self,
                                             edt: bytes,
                                             ) -> list[dict[str, datetime.datetime |
                                                                 dict[str, dict[str, int | float | None]]]]:
        coefficient_for_cumulative_energy = self.__init_coefficient_for_cumulative_energy()
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
                normal_direction_energy *= coefficient_for_cumulative_energy

            reverse_direction_energy = int.from_bytes(energy_data_points[j + 4:j + 8], 'big')
            if reverse_direction_energy == 0xFFFFFFFE:
                reverse_direction_energy = None
            else:
                reverse_direction_energy *= coefficient_for_cumulative_energy

            historical_cumulative_energy.append(
                {'timestamp': timestamp,
                 'cumulative energy': {'normal direction': normal_direction_energy,
                                       'reverse direction': reverse_direction_energy}})
            timestamp -= datetime.timedelta(minutes=1)

        return historical_cumulative_energy

    @staticmethod
    def parse_time_for_historical_data_3(edt: bytes) -> dict[str, datetime.datetime | None | int]:
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
        res = self.request_to_get_raw([req])[0]
        return self.parse_operation_status(res.edt)

    def get_installation_location(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.installation_location)
        res = self.request_to_get_raw([req])[0]
        return self.parse_installation_location(res.edt)

    def get_standard_version(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.standard_version_information)
        res = self.request_to_get_raw([req])[0]
        return self.parse_standard_version_information(res.edt)

    def get_fault_status(self) -> bool | None:
        req = EchonetProperty(EchonetPropertyCode.fault_status)
        res = self.request_to_get_raw([req])[0]
        return self.parse_fault_status(res.edt)

    def get_manufacturer_code(self) -> bytes:
        req = EchonetProperty(EchonetPropertyCode.manufacturer_code)
        res = self.request_to_get_raw([req])[0]
        return self.parse_manufacturer_code(res.edt)

    def get_serial_number(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.serial_number)
        res = self.request_to_get_raw([req])[0]
        return self.parse_serial_number(res.edt)

    def get_current_time_setting(self) -> datetime.time:
        req = EchonetProperty(EchonetPropertyCode.current_time_setting)
        res = self.request_to_get_raw([req])[0]
        return self.parse_current_time_setting(res.edt)

    def get_current_date_setting(self) -> datetime.date:
        req = EchonetProperty(EchonetPropertyCode.current_date_setting)
        res = self.request_to_get_raw([req])[0]
        return self.parse_current_date_setting(res.edt)

    def get_properties_for_status_notification(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_for_status_notification)
        res = self.request_to_get_raw([req])[0]
        return self.parse_property_map(res.edt)

    def get_properties_to_set_values(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_to_set_values)
        res = self.request_to_get_raw([req])[0]
        return self.parse_property_map(res.edt)

    def get_properties_to_get_values(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_to_get_values)
        res = self.request_to_get_raw([req])[0]
        return self.parse_property_map(res.edt)

    def get_route_b_id(self) -> dict[str, bytes]:
        req = EchonetProperty(EchonetPropertyCode.route_b_id)
        res = self.request_to_get_raw([req])[0]
        return self.parse_route_b_id(res.edt)

    def get_one_minute_measured_cumulative_energy(self) -> dict[str, datetime.datetime |
                                                                     dict[str, int | float | None]]:
        req = EchonetProperty(EchonetPropertyCode.one_minute_measured_cumulative_energy)
        res = self.request_to_get_raw([req])[0]
        return self.parse_one_minute_measured_cumulative_energy(res.edt)

    def get_coefficient_for_cumulative_energy(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.coefficient_for_cumulative_energy)
        res = self.request_to_get_raw([req])[0]
        return self.parse_coefficient_for_cumulative_energy(res.edt)

    def get_number_of_effective_digits_for_cumulative_energy(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.number_of_effective_digits_for_cumulative_energy)
        res = self.request_to_get_raw([req])[0]
        return self.parse_number_of_effective_digits_for_cumulative_energy(res.edt)

    def get_measured_cumulative_energy(self,
                                       reverse: bool = False,
                                       ) -> int | float:
        if reverse is False:
            epc = EchonetPropertyCode.measured_cumulative_energy
        else:
            epc = EchonetPropertyCode.measured_cumulative_energy_reversed

        req = EchonetProperty(epc)
        res = self.request_to_get_raw([req])[0]
        return self.parse_measured_cumulative_energy(res.edt)

    def get_unit_for_cumulative_energy(self) -> int | float:
        req = EchonetProperty(EchonetPropertyCode.unit_for_cumulative_energy)
        res = self.request_to_get_raw([req])[0]
        return self.parse_unit_for_cumulative_energy(res.edt)

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
        res = self.request_to_get_raw([req])[0]
        return self.parse_historical_cumulative_energy_1(res.edt)

    def set_day_for_historical_data_1(self,
                                      day: int = 0,
                                      ) -> None:
        edt = self.build_edata_to_set_day_for_historical_data_1(day)
        req = EchonetPropertyWithData(EchonetPropertyCode.day_for_historical_data_1, edt)
        self.request_to_set_raw([req])

    def get_day_for_historical_data_1(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.day_for_historical_data_1)
        res = self.request_to_get_raw([req])[0]
        return self.parse_day_for_historical_data_1(res.edt)

    def get_instantaneous_power(self) -> float:
        req = EchonetProperty(EchonetPropertyCode.instantaneous_power)
        res = self.request_to_get_raw([req])[0]
        return self.parse_instantaneous_power(res.edt)

    def get_instantaneous_current(self) -> dict[str, float]:
        req = EchonetProperty(EchonetPropertyCode.instantaneous_current)
        res = self.request_to_get_raw([req])[0]
        return self.parse_instantaneous_current(res.edt)

    def get_cumulative_energy_measured_at_fixed_time(self,
                                                     reverse: bool = False,
                                                     ) -> dict[str, datetime.datetime | int | float]:
        if reverse is False:
            epc = EchonetPropertyCode.cumulative_energy_measured_at_fixed_time
        else:
            epc = EchonetPropertyCode.cumulative_energy_measured_at_fixed_time_reversed

        req = EchonetProperty(epc)
        res = self.request_to_get_raw([req])[0]
        return self.parse_cumulative_energy_measured_at_fixed_time(res.edt)

    def get_historical_cumulative_energy_2(self,
                                           timestamp: datetime.datetime = None,
                                           num_of_data_points: int = 12,
                                           ) -> list[dict[str, datetime.datetime |
                                                               dict[str, int | float | None]]]:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.set_time_for_historical_data_2(timestamp, num_of_data_points)

        req = EchonetProperty(EchonetPropertyCode.historical_cumulative_energy_2)
        res = self.request_to_get_raw([req])[0]
        return self.parse_historical_cumulative_energy_2(res.edt)

    def set_time_for_historical_data_2(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 12,
                                       ) -> None:
        edt = self.build_edata_to_set_time_for_historical_data_2(timestamp,
                                                                 num_of_data_points)
        req = EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_2, edt)
        self.request_to_set_raw([req])

    def get_time_for_historical_data_2(self) -> dict[str: datetime.datetime | None,
                                                     str: int]:
        req = EchonetProperty(EchonetPropertyCode.time_for_historical_data_2)
        res = self.request_to_get_raw([req])[0]
        return self.parse_time_for_historical_data_2(res.edt)

    def get_historical_cumulative_energy_3(self,
                                           timestamp: datetime.datetime = None,
                                           num_of_data_points: int = 10,
                                           ) -> list[dict[str, datetime.datetime |
                                                               dict[str, int | float | None]]]:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.set_time_for_historical_data_3(timestamp, num_of_data_points)

        req = EchonetProperty(EchonetPropertyCode.historical_cumulative_energy_3)
        res = self.request_to_get_raw([req])[0]
        return self.parse_historical_cumulative_energy_3(res.edt)

    def set_time_for_historical_data_3(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 10,
                                       ) -> None:
        edt = self.build_edata_to_set_time_for_historical_data_3(timestamp,
                                                                 num_of_data_points)
        req = EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_3, edt)
        self.request_to_set_raw([req])

    def get_time_for_historical_data_3(self) -> dict[str, datetime.datetime | None | int]:
        req = EchonetProperty(EchonetPropertyCode.time_for_historical_data_3)
        res = self.request_to_get_raw([req])[0]
        return self.parse_time_for_historical_data_3(res.edt)


    def request_to_set(self,
                       day_for_historical_data_1: DayForHistoricalData1 | None = None,
                       time_for_historical_data_2: TimeForHistoricalData2 | None = None,
                       time_for_historical_data_3: TimeForHistoricalData3 | None = None) -> None:
        properties_with_data = []
        if day_for_historical_data_1 is not None:
            edt = self.build_edata_to_set_day_for_historical_data_1(**day_for_historical_data_1)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.day_for_historical_data_1, edt))
        if time_for_historical_data_2 is not None:
            edt = self.build_edata_to_set_time_for_historical_data_2(**time_for_historical_data_2)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_2, edt))
        if time_for_historical_data_3 is not None:
            edt = self.build_edata_to_set_time_for_historical_data_3(**time_for_historical_data_3)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_3, edt))

        self.request_to_set_raw(properties_with_data)

    def request_to_get(self,
                       properties: set[EchonetPropertyCode]) -> dict[EchonetPropertyCode, Any]:
        results = self.request_to_get_raw([EchonetProperty(epc) for epc in properties])
        parsed_results = {}
        for r in results:
            try:
                parsed_results[r.epc] = self.parser_map[r.epc](r.edt)
            except KeyError:
                raise MomongaRuntimeError(f"No parser found for EPC: %X" % r.epc)

        return parsed_results
