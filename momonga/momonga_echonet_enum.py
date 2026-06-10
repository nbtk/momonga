import enum

# ECHONET Lite protocol constants
ECHONET_LITE_EHD  = b'\x10\x81'    # Frame header: EHD1=0x10 (ECHONET Lite), EHD2=0x81 (EDATA format 1)
ECHONET_LITE_PORT = 0x0E1A         # Standard UDP port (3610)

# ECHONET Lite frame field offsets (EDATA format 1)
ECHONET_EHD_SLICE  = slice(0, 2)   # Frame header (2 bytes)
ECHONET_TID_SLICE  = slice(2, 4)   # Transaction ID (2 bytes)
ECHONET_SEOJ_SLICE = slice(4, 7)   # Source EOJ (3 bytes)
ECHONET_DEOJ_SLICE = slice(7, 10)  # Destination EOJ (3 bytes)
ECHONET_ESV_OFFSET = 10            # Service code (1 byte)
ECHONET_OPC_OFFSET = 11            # Operation count (1 byte)

# ECHONET Lite Object Identifiers used in B-route communication
SMART_METER_EOJ = b'\x02\x88\x01'  # Low-voltage smart electric energy meter (class 0x0288, instance 0x01)
CONTROLLER_EOJ  = b'\x05\xFF\x01'  # Controller (home energy management system)


class EchonetServiceCode(enum.IntEnum):
    set_c = 0x61
    get = 0x62
    inf = 0x73
    infc = 0x74
    infc_res = 0x7A


class EchonetPropertyCode(enum.IntEnum):
    operation_status = 0x80
    installation_location = 0x81
    standard_version_information = 0x82
    fault_status = 0x88
    manufacturer_code = 0x8A
    serial_number = 0x8D
    current_time_setting = 0x97
    current_date_setting = 0x98
    properties_for_status_notification = 0x9D
    properties_to_set_values = 0x9E
    properties_to_get_values = 0x9F
    route_b_id = 0xC0
    one_minute_measured_cumulative_energy = 0xD0
    coefficient_for_cumulative_energy = 0xD3
    number_of_effective_digits_for_cumulative_energy = 0xD7
    measured_cumulative_energy = 0xE0
    measured_cumulative_energy_reversed = 0xE3
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
