
import datetime
import queue
import logging

from .momonga_exception import *
from .momonga_response import *
from .momonga_session_manager import MomongaSessionManager
from .momonga_session_manager import logger as session_manager_logger
from .momonga_sk_wrapper import logger as sk_wrapper_logger


try:
    from typing import Self
except ImportError:
    Self = object


logger = logging.getLogger(__name__)


class Momonga:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                ) -> None:
        self.transaction_id = 0
        self.energy_coefficient = None
        self.energy_unit = None

        # the following value will be set a pyserial object.
        self.session_manager = MomongaSessionManager(rbid,
                                                 pwd,
                                                 dev,
                                                 baudrate,
                                                 reset_dev,
                                                )

#    def __enter__(self) -> Self:
    def __enter__(self):
        return self.open()

    def __exit__(self, type, value, traceback) -> None:
        self.close()

#    def open(self) -> Self:
    def open(self):
        logger.info('Opening Momonga.')
        self.session_manager.open()
        logger.info('Momonga is open.')
        return self

    def close(self):
        logger.info('Closing Momonga.')
        self.energy_coefficient = None
        self.energy_unit = None
        self.session_manager.close()
        logger.info('Momonga is closed.')

    def __build_request_payload(self,
                                tid: int,
                                epc: int,
                                edt: bytes = b'',
                               ) -> bytes:
        ehd  = b'\x10\x81'     # echonet lite edata format 1
        tid  = tid.to_bytes(4, 'big')[-2:]
        seoj = b'\x05\xFF\x01' # controller class
        deoj = b'\x02\x88\x01' # low-voltage smart electric energy meter class
        opc  = b'\x01'
        if edt:
            esv = b'\x61' # setc
        else:
            esv = b'\x62' # get
        epc  = epc.to_bytes(1, 'big')
        pdc = len(edt).to_bytes(1, 'big')

        return ehd + tid + seoj + deoj + esv + opc + epc + pdc + edt

    def __extract_response_payload(self,
                                   data: bytes,
                                   tid: int,
                                   epc: int,
                                  ):
        ehd = data[0:2]
        if  ehd != b'\x10\x81': # echonet lite edata format 1
            raise MomongaResponseNotExpected('The data format is not ECHONET Lite EDATA format 1')

        if int.from_bytes(data[2:4], 'big') != tid:
            raise MomongaResponseNotExpected('The transaction ID does not match.')

        seoj = data[4:7]
        if seoj != b'\x02\x88\x01': # low-voltage smart electric energy meter class
            raise MomongaResponseNotExpected('The source is not a smart meter.')

        deoj = data[7:10]
        if deoj != b'\x05\xFF\x01': # controller class
            raise MomongaResponseNotExpected('The destination is not a controller.')

        if data[12] != epc:
            raise MomongaResponseNotExpected('The property code does not match. EPC: %X' % epc)

        esv = data[10]
        if 0x50 <= esv <= 0x5F:
            raise MomongaResponseNotPossible('The target smart meter could not respond. ESV: %X' % esv)

        opc = data[11]
        assert opc == 1, 'Unexpected packet format. OPC is expected 1 but %d was set.' % opc

        pdc = data[13]
        if pdc == 0:
            edt = None
        else:
            edt = data[14:14+pdc]

        return {'ehd': ehd, 'tid': tid, 'seoj': seoj, 'deoj': deoj, 'esv': esv,
                'opc': opc, 'epc': epc, 'pdc': pdc, 'epc': epc, 'edt': edt}

    def __request(self,
                  epc: int,
                  edt: bytes = b'',
                 ) -> bytes:
        self.transaction_id += 1
        tx_payload = self.__build_request_payload(self.transaction_id,
                                                  epc,
                                                  edt)
        while not self.session_manager.recv_q.empty():
            self.session_manager.recv_q.get() # drops stored data

        for _ in range(12):
            self.session_manager.xmitter(tx_payload)
            while True:
                try:
                    res = self.session_manager.recv_q.get(timeout=12)
                except queue.Empty:
                    logger.warning('Timed out to obtain a response for "%X" request.' % (epc))
                    break
                if res.startswith('EVENT 21'):
                    param = res.split()[-1]
                    if param == '00':
                        logger.info('Successfully transmitted a packet for "%X" request.' % (epc))
                        continue
                    elif param == '01':
                        logger.warning('Retransmitting the packet for "%X" request.' % (epc))
                        break # to rexmit
                    elif param == '02':
                        logger.warning('Transmitting neighbor solicitation packets.' % (epc))
                        continue
                elif res.startswith('EVENT 02'):
                    logger.info('Received a neighbor advertisement packet.' % (epc))
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
                        rx_payload = self.__extract_response_payload(udp_pkt.data,
                                                                     self.transaction_id,
                                                                     epc)
                    except MomongaResponseNotExpected:
                        continue

                    logger.info('Successfully received a packet for "%X" response.' % (epc))
                    return rx_payload

        logger.error('Gave up to obtain a response for "%X" request. Close Momonga and open it again.' % (epc))
        raise MomongaNeedToReopen('Gave up to obtain a response for "%X" request. Close Momonga and open it again.' % (epc))

    def __prepare_to_get_cumulative_energy(self) -> None:
        if self.energy_coefficient is None:
            try:
                self.energy_coefficient = self.get_coefficient_for_cumulative_energy()
            except MomongaResponseNotPossible:
                self.energy_coefficient = 1
        if self.energy_unit is None:
            self.energy_unit = self.get_unit_for_cumulative_energy()

    def get_operation_status(self) -> int:
        res = self.__request(0x80)
        status = int.from_bytes(res.get('edt'), 'big')
        if status == 0x30:   # turned on
            status = True
        elif status == 0x31: # turned off
            status = False
        else:
            status = None
        return status

    def get_coefficient_for_cumulative_energy(self) -> int:
        res = self.__request(0xD3)
        coefficient = int.from_bytes(res.get('edt'), 'big')
        return coefficient

    def get_number_of_effective_digits_for_cumulative_energy(self) -> int:
        res = self.__request(0xD7)
        digits = int.from_bytes(res.get('edt'), 'big')
        return digits

    def get_measured_cumulative_energy(self,
                                       reverse: bool = False,
                                      ) -> int: 
        self.__prepare_to_get_cumulative_energy()

        if reverse is False:
            epc = 0xE0
        else:
            epc = 0xE3

        res = self.__request(epc)
        cumulative_energy = int.from_bytes(res.get('edt'), 'big')
        cumulative_energy *= self.energy_coefficient
        cumulative_energy *= self.energy_unit 
        return cumulative_energy

    def get_unit_for_cumulative_energy(self) -> int | float:
        res = self.__request(0xE1)
        unit_index = int.from_bytes(res.get('edt'), 'big')
        unit_map = {0x00:1,
                    0x01:0.1,
                    0x02:0.01,
                    0x03:0.001,
                    0x04:0.0001,
                    0x0A:10,
                    0x0B:100,
                    0x0C:1000,
                    0x0D:10000,
                   }
        return unit_map.get(unit_index)

    def get_historical_cumulative_energy_1(self,
                                           day: int = 0,
                                           reverse: bool = False,
                                          ) -> list:
        self.__prepare_to_get_cumulative_energy()
        self.set_day_for_historical_data_1(day)

        if reverse is False:
            epc = 0xE2
        else:
            epc = 0xE4

        res = self.__request(epc)
        edt = res.get('edt')
        day = int.from_bytes(edt[0:2], 'big')
        timestamp = datetime.datetime.combine(datetime.date.today(),
                                              datetime.datetime.min.time())
        timestamp -= datetime.timedelta(days=day)

        energy_data_points = edt[2:]
        historical_cumulative_energy = []
        for i in range(48):
            j = i * 4
            cumulative_energy = int.from_bytes(energy_data_points[j:j+4], 'big')
            if cumulative_energy == 0xFFFFFFFE:
                cumulative_energy = None
            else:
                cumulative_energy *= self.energy_coefficient
                cumulative_energy *= self.energy_unit 
            historical_cumulative_energy.append({'timestamp': timestamp, 'cumulative energy': cumulative_energy})
            timestamp += datetime.timedelta(minutes=30)
        return historical_cumulative_energy
 
    def set_day_for_historical_data_1(self,
                                      day: int = 0,
                                     ) -> None:
        self.__request(0xE5, day.to_bytes(1, 'big'))

    def get_day_for_historical_data_1(self) -> int:
        res = self.__request(0xE5)
        day = int.from_bytes(res.get('edt'), 'big')
        return day
 
    def get_instantaneous_power(self) -> float:
        res = self.__request(0xE7)
        power = int.from_bytes(res.get('edt'), 'big', signed=True)
        return power

    def get_instantaneous_current(self) -> dict:
        res = self.__request(0xE8)
        edt = res.get('edt')
        r_phase_current = int.from_bytes(edt[0:2], 'big', signed=True)
        t_phase_current = int.from_bytes(edt[2:4], 'big', signed=True)
        r_phase_current *= 0.1 # to Ampere
        t_phase_current *= 0.1 # to Ampere
        return {'r phase current': r_phase_current,
                't phase current': t_phase_current}

    def get_cumulative_energy_measured_at_fixed_time(self,
                                                     reverse: bool = False,
                                                    ) -> dict:
        self.__prepare_to_get_cumulative_energy()

        if reverse is False:
            epc = 0xEA
        else:
            epc = 0xEB

        res = self.__request(epc)
        edt = res.get('edt')
        timestamp = datetime.datetime(int.from_bytes(edt[0:2], 'big'),
                                      edt[2], edt[3], edt[4], edt[5], edt[6])
        cumulative_energy = int.from_bytes(edt[7:], 'big')
        cumulative_energy *= self.energy_coefficient
        cumulative_energy *= self.energy_unit 
        return {'timestamp': timestamp,
                'cumulative_energy': cumulative_energy}

    def get_historical_cumulative_energy_2(self,
                                           timestamp: datetime.datetime = None,
                                           num_of_data_points: int = 12,
                                          ) -> list:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.__prepare_to_get_cumulative_energy()
        self.set_time_for_historical_data_2(timestamp, num_of_data_points)

        res = self.__request(0xEC)
        edt = res.get('edt')
        year = int.from_bytes(edt[0:2], 'big')
        num_of_data_points = edt[6]
        energy_data_points = edt[7:]

        timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])
        historical_cumulative_energy = []
        for i in range(num_of_data_points):
            j = i * 8
            normal_direction_energy = int.from_bytes(energy_data_points[j:j+4], 'big')
            if normal_direction_energy == 0xFFFFFFFE:
                normal_direction_energy = None
            else:
                normal_direction_energy *= self.energy_coefficient
                normal_direction_energy *= self.energy_unit 

            reverse_direction_energy = int.from_bytes(energy_data_points[j+4:j+8], 'big')
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
 
    def set_time_for_historical_data_2(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 12,
                                      ) -> None:
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
        self.__request(0xED, year + month + day + hour + minute + num_of_data_points)

    def get_time_for_historical_data_2(self) -> dict:
        res = self.__request(0xED)
        edt = res.get('edt')
        year = int.from_bytes(edt[0:2], 'big')
        if year == 0xFFFF:
            timestamp = None
        else:
            timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])

        num_of_data_points = edt[6]
        return {'timestamp': timestamp,
                'number of data points': num_of_data_points}
