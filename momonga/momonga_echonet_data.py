import datetime
import inspect

from .momonga_echonet_enum import EchonetPropertyCode
from .momonga_exception import MomongaRuntimeError, MomongaValueError


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


class EchonetDataParser:
    @classmethod
    def parse_operation_status(cls, edt: bytes) -> bool | None:
        status = int.from_bytes(edt, 'big')
        if status == 0x30:  # turned on
            status = True
        elif status == 0x31:  # turned off
            status = False
        else:
            status = None  # unknown

        return status

    @classmethod
    def parse_installation_location(cls, edt: bytes) -> str:
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

    @classmethod
    def parse_standard_version_information(cls, edt: bytes) -> str:
        version = ''
        if edt[0] > 0:
            version += chr(edt[0])
        if edt[1] > 0:
            version += chr(edt[1])
        return version + chr(edt[2]) + '.' + str(edt[3])

    @classmethod
    def parse_fault_status(cls, edt: bytes) -> bool | None:
        status_code = int.from_bytes(edt, 'big')
        if status_code == 0x41:
            status = True  # fault occurred
        elif status_code == 0x42:
            status = False  # no fault occurred
        else:
            status = None  # unknown

        return status

    @classmethod
    def parse_manufacturer_code(cls, edt: bytes) -> bytes:
        return edt

    @classmethod
    def parse_serial_number(cls, edt: bytes) -> str:
        return edt.decode()

    @classmethod
    def parse_current_time_setting(cls, edt: bytes) -> datetime.time:
        hour = edt[0]
        minute = edt[1]
        return datetime.time(hour=hour, minute=minute, second=0)

    @classmethod
    def parse_current_date_setting(cls, edt: bytes) -> datetime.date:
        year = int.from_bytes(edt[0:2], 'big')
        month = edt[2]
        day = edt[3]
        return datetime.date(year=year, month=month, day=day)

    @classmethod
    def parse_property_map(cls, edt: bytes) -> set[EchonetPropertyCode | int]:
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
                        prop_code = ((j + 0x08) << 4) + i
                        try:
                            prop_code = EchonetPropertyCode(prop_code)
                        except ValueError:
                            pass

                        properties.add(prop_code)

        return properties

    @classmethod
    def parse_route_b_id(cls, edt: bytes) -> dict[str, bytes]:
        manufacturer_code = edt[1:4]
        authentication_id = edt[4:]
        return {'manufacturer code': manufacturer_code, 'authentication id': authentication_id}

    @classmethod
    def parse_one_minute_measured_cumulative_energy(
            cls,
            edt: bytes,
            energy_unit: int | float,
            energy_coefficient: int,
    ) -> dict[str, datetime.datetime | dict[str, int | float | None]]:
        timestamp = datetime.datetime(int.from_bytes(edt[0:2], 'big'),
                                      edt[2], edt[3], edt[4], edt[5], edt[6])

        normal_direction_energy = int.from_bytes(edt[7:11], 'big')
        if normal_direction_energy == 0xFFFFFFFE:
            normal_direction_energy = None
        else:
            normal_direction_energy *= energy_unit
            normal_direction_energy *= energy_coefficient

        reverse_direction_energy = int.from_bytes(edt[11:15], 'big')
        if reverse_direction_energy == 0xFFFFFFFE:
            reverse_direction_energy = None
        else:
            reverse_direction_energy *= energy_unit
            reverse_direction_energy *= energy_coefficient

        return {'timestamp': timestamp,
                'cumulative energy': {'normal direction': normal_direction_energy,
                                      'reverse direction': reverse_direction_energy}}

    @classmethod
    def parse_coefficient_for_cumulative_energy(cls, edt: bytes) -> int:
        coefficient = int.from_bytes(edt, 'big')
        return coefficient

    @classmethod
    def parse_number_of_effective_digits_for_cumulative_energy(cls, edt: bytes) -> int:
        digits = int.from_bytes(edt, 'big')
        return digits

    @classmethod
    def parse_measured_cumulative_energy(
            cls,
            edt: bytes,
            energy_unit: int | float,
            energy_coefficient: int,
    ) -> int | float:
        cumulative_energy = int.from_bytes(edt, 'big')
        cumulative_energy *= energy_unit
        cumulative_energy *= energy_coefficient
        return cumulative_energy

    @classmethod
    def parse_unit_for_cumulative_energy(cls, edt: bytes) -> int | float:
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

    @classmethod
    def parse_historical_cumulative_energy_1(
            cls,
            edt: bytes,
            energy_unit: int | float,
            energy_coefficient: int,
    ) -> list[dict[str, datetime.datetime | int | float | None]]:
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
                cumulative_energy *= energy_unit
                cumulative_energy *= energy_coefficient
            historical_cumulative_energy.append({'timestamp': timestamp, 'cumulative energy': cumulative_energy})
            timestamp += datetime.timedelta(minutes=30)

        return historical_cumulative_energy

    @classmethod
    def parse_day_for_historical_data_1(cls, edt: bytes) -> int:
        day = int.from_bytes(edt, 'big')
        return day

    @classmethod
    def parse_instantaneous_power(cls, edt: bytes) -> float:
        power = int.from_bytes(edt, 'big', signed=True)
        return power

    @classmethod
    def parse_instantaneous_current(cls, edt: bytes) -> dict[str, float]:
        r_phase_current = int.from_bytes(edt[0:2], 'big', signed=True)
        t_phase_current = int.from_bytes(edt[2:4], 'big', signed=True)
        r_phase_current *= 0.1  # to Ampere
        t_phase_current *= 0.1  # to Ampere
        return {'r phase current': r_phase_current, 't phase current': t_phase_current}

    @classmethod
    def parse_cumulative_energy_measured_at_fixed_time(
            cls,
            edt: bytes,
            energy_unit: int | float,
            energy_coefficient: int,
    ) -> dict[str, datetime.datetime | int | float]:
        timestamp = datetime.datetime(int.from_bytes(edt[0:2], 'big'),
                                      edt[2], edt[3], edt[4], edt[5], edt[6])
        cumulative_energy = int.from_bytes(edt[7:], 'big')
        cumulative_energy *= energy_unit
        cumulative_energy *= energy_coefficient
        return {'timestamp': timestamp, 'cumulative energy': cumulative_energy}

    @classmethod
    def parse_historical_cumulative_energy_2(
            cls,
            edt: bytes,
            energy_unit: int | float,
            energy_coefficient: int,
    ) -> list[dict[str, datetime.datetime |
                         dict[str, int | float | None]]]:
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
                normal_direction_energy *= energy_unit
                normal_direction_energy *= energy_coefficient

            reverse_direction_energy = int.from_bytes(energy_data_points[j + 4:j + 8], 'big')
            if reverse_direction_energy == 0xFFFFFFFE:
                reverse_direction_energy = None
            else:
                reverse_direction_energy *= energy_unit
                reverse_direction_energy *= energy_coefficient

            historical_cumulative_energy.append(
                {'timestamp': timestamp,
                 'cumulative energy': {'normal direction': normal_direction_energy,
                                       'reverse direction': reverse_direction_energy}})
            timestamp -= datetime.timedelta(minutes=30)

        return historical_cumulative_energy

    @classmethod
    def parse_time_for_historical_data_2(cls, edt: bytes) -> dict[str, datetime.datetime | None | int]:
        year = int.from_bytes(edt[0:2], 'big')
        if year == 0xFFFF:
            timestamp = None
        else:
            timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])

        num_of_data_points = edt[6]
        return {'timestamp': timestamp,
                'number of data points': num_of_data_points}

    @classmethod
    def parse_historical_cumulative_energy_3(
            cls,
            edt: bytes,
            energy_unit: int | float,
            energy_coefficient: int,
    ) -> list[dict[str, datetime.datetime |
                        dict[str, dict[str, int | float | None]]]]:
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
                normal_direction_energy *= energy_unit
                normal_direction_energy *= energy_coefficient

            reverse_direction_energy = int.from_bytes(energy_data_points[j + 4:j + 8], 'big')
            if reverse_direction_energy == 0xFFFFFFFE:
                reverse_direction_energy = None
            else:
                reverse_direction_energy *= energy_unit
                reverse_direction_energy *= energy_coefficient

            historical_cumulative_energy.append(
                {'timestamp': timestamp,
                 'cumulative energy': {'normal direction': normal_direction_energy,
                                       'reverse direction': reverse_direction_energy}})
            timestamp -= datetime.timedelta(minutes=1)

        return historical_cumulative_energy

    @classmethod
    def parse_time_for_historical_data_3(cls, edt: bytes) -> dict[str, datetime.datetime | None | int]:
        year = int.from_bytes(edt[0:2], 'big')
        if year == 0xFFFF:
            timestamp = None
        else:
            timestamp = datetime.datetime(year, edt[2], edt[3], edt[4], edt[5])

        num_of_data_points = edt[6]
        return {'timestamp': timestamp,
                'number of data points': num_of_data_points}


parser_map: dict[EchonetPropertyCode, callable] = {
    EchonetPropertyCode.operation_status: EchonetDataParser.parse_operation_status,
    EchonetPropertyCode.installation_location: EchonetDataParser.parse_installation_location,
    EchonetPropertyCode.standard_version_information: EchonetDataParser.parse_standard_version_information,
    EchonetPropertyCode.fault_status: EchonetDataParser.parse_fault_status,
    EchonetPropertyCode.manufacturer_code: EchonetDataParser.parse_manufacturer_code,
    EchonetPropertyCode.serial_number: EchonetDataParser.parse_serial_number,
    EchonetPropertyCode.current_time_setting: EchonetDataParser.parse_current_time_setting,
    EchonetPropertyCode.current_date_setting: EchonetDataParser.parse_current_date_setting,
    EchonetPropertyCode.properties_for_status_notification: EchonetDataParser.parse_property_map,
    EchonetPropertyCode.properties_to_set_values: EchonetDataParser.parse_property_map,
    EchonetPropertyCode.properties_to_get_values: EchonetDataParser.parse_property_map,
    EchonetPropertyCode.route_b_id: EchonetDataParser.parse_route_b_id,
    EchonetPropertyCode.one_minute_measured_cumulative_energy: EchonetDataParser.parse_one_minute_measured_cumulative_energy,
    EchonetPropertyCode.coefficient_for_cumulative_energy: EchonetDataParser.parse_coefficient_for_cumulative_energy,
    EchonetPropertyCode.number_of_effective_digits_for_cumulative_energy: EchonetDataParser.parse_number_of_effective_digits_for_cumulative_energy,
    EchonetPropertyCode.measured_cumulative_energy: EchonetDataParser.parse_measured_cumulative_energy,
    EchonetPropertyCode.measured_cumulative_energy_reversed: EchonetDataParser.parse_measured_cumulative_energy,
    EchonetPropertyCode.unit_for_cumulative_energy: EchonetDataParser.parse_unit_for_cumulative_energy,
    EchonetPropertyCode.historical_cumulative_energy_1: EchonetDataParser.parse_historical_cumulative_energy_1,
    EchonetPropertyCode.historical_cumulative_energy_1_reversed: EchonetDataParser.parse_historical_cumulative_energy_1,
    EchonetPropertyCode.day_for_historical_data_1: EchonetDataParser.parse_day_for_historical_data_1,
    EchonetPropertyCode.instantaneous_power: EchonetDataParser.parse_instantaneous_power,
    EchonetPropertyCode.instantaneous_current: EchonetDataParser.parse_instantaneous_current,
    EchonetPropertyCode.cumulative_energy_measured_at_fixed_time: EchonetDataParser.parse_cumulative_energy_measured_at_fixed_time,
    EchonetPropertyCode.cumulative_energy_measured_at_fixed_time_reversed: EchonetDataParser.parse_cumulative_energy_measured_at_fixed_time,
    EchonetPropertyCode.historical_cumulative_energy_2: EchonetDataParser.parse_historical_cumulative_energy_2,
    EchonetPropertyCode.time_for_historical_data_2: EchonetDataParser.parse_time_for_historical_data_2,
    EchonetPropertyCode.historical_cumulative_energy_3: EchonetDataParser.parse_historical_cumulative_energy_3,
    EchonetPropertyCode.time_for_historical_data_3: EchonetDataParser.parse_time_for_historical_data_3,
}

energy_parsers: frozenset = frozenset(
    fn for fn in parser_map.values()
    if 'energy_unit' in inspect.signature(fn).parameters
)


class EchonetDataBuilder:
    @classmethod
    def build_edata_to_set_day_for_historical_data_1(cls, day: int = 0) -> bytes:
        if day < 0 or day > 99:
            raise MomongaValueError('The parameter "day" must be between 0 and 99.')

        return day.to_bytes(1, 'big')

    @classmethod
    def build_edata_to_set_time_for_historical_data_2(cls,
                                                      timestamp: datetime.datetime,
                                                      num_of_data_points: int = 12,
                                                      ) -> bytes:
        if num_of_data_points < 1 or num_of_data_points > 12:
            raise MomongaValueError('The parameter "num_of_data_points" must be between 1 and 12.')

        if timestamp.year < 1 or timestamp.year > 9999:
            raise MomongaValueError('The year specified by the parameter "timestamp" must be between 1 and 9999.')

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

    @classmethod
    def build_edata_to_set_time_for_historical_data_3(cls,
                                                      timestamp: datetime.datetime,
                                                      num_of_data_points: int = 10,
                                                      ) -> bytes:
        if num_of_data_points < 1 or num_of_data_points > 10:
            raise MomongaValueError('The parameter "num_of_data_points" must be between 1 and 10.')

        if timestamp.year < 1 or timestamp.year > 9999:
            raise MomongaValueError('The year specified by the parameter "timestamp" must be between 1 and 9999.')

        year = timestamp.year.to_bytes(2, 'big')
        month = timestamp.month.to_bytes(1, 'big')
        day = timestamp.day.to_bytes(1, 'big')
        hour = timestamp.hour.to_bytes(1, 'big')
        minute = timestamp.minute.to_bytes(1, 'big')
        num_of_data_points = num_of_data_points.to_bytes(1, 'big')
        return year + month + day + hour + minute + num_of_data_points
