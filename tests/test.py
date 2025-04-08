import logging
import os
import sys
import time
import datetime
import traceback
import momonga

from pprint import pprint
from momonga import EchonetPropertyCode as EPC
from momonga import EchonetProperty, EchonetPropertyWithData

log_fmt = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s - %(message)s')
log_hnd = logging.StreamHandler()
log_hnd.setFormatter(log_fmt)
momonga.logger.addHandler(log_hnd)
momonga.logger.setLevel(logging.DEBUG)
momonga.session_manager_logger.addHandler(log_hnd)
momonga.session_manager_logger.setLevel(logging.DEBUG)
momonga.sk_wrapper_logger.addHandler(log_hnd)
momonga.sk_wrapper_logger.setLevel(logging.DEBUG)

# set the following environment values before run.
rbid = os.environ.get('MOMONGA_ROUTEB_ID')
pwd = os.environ.get('MOMONGA_ROUTEB_PASSWORD')
dev = os.environ.get('MOMONGA_DEV_PATH')
baudrate = os.environ.get('MOMONGA_DEV_BAUDRATE')

exit_code = 1

if rbid is None:
    print('Set a Route-B ID.', file=sys.stderr)
    exit(exit_code)
elif pwd is None:
    print('Set a Route-B password.', file=sys.stderr)
    exit(exit_code)
elif dev is None:
    print('Set the path of a Wi-SUN device.', file=sys.stderr)
    exit(exit_code)

if baudrate is None:
    baudrate = 115200
else:
    baudrate = int(baudrate)

while True:
    try:
        with momonga.Momonga(rbid, pwd, dev, baudrate) as mo:
            print('---- operation status of smart meter ----')
            res = mo.get_operation_status()
            if res is True:
                print('turned on')
            elif res is False:
                print('turned off')
            else:
                print('unknown')
            print('----')
            time.sleep(5)

            print('---- installation location ----')
            res = mo.get_installation_location()
            print(res)
            print('----')
            time.sleep(5)

            print('---- standard version information ----')
            res = mo.get_standard_version()
            print(res)
            print('----')
            time.sleep(5)

            print('---- fault status ----')
            res = mo.get_fault_status()
            if res is True:
                print('fault occurred')
            elif res is False:
                print('no fault occurred')
            else:
                print('unknown')
            print('----')

            print('---- manufacturer code ----')
            res = mo.get_manufacturer_code()
            print(res)
            print('----')
            time.sleep(5)

            print('---- serial number ----')
            res = mo.get_serial_number()
            print(res)
            print('----')
            time.sleep(5)

            print('---- current time setting ----')
            res = mo.get_current_time_setting()
            print(res)
            print('----')
            time.sleep(5)

            print('---- current date setting ----')
            res = mo.get_current_date_setting()
            print(res)
            print('----')
            time.sleep(5)

            print('---- properties for status notification ----')
            res = mo.get_properties_for_status_notification()
            print(res)
            print('----')
            time.sleep(5)

            print('---- properties to set values ----')
            res = mo.get_properties_to_set_values()
            print(res)
            print('----')
            time.sleep(5)

            print('---- properties to get values ----')
            res = mo.get_properties_to_get_values()
            print(res)
            print('----')
            time.sleep(5)

            print('---- number of effective digits for cumulative energy ----')
            res = mo.get_number_of_effective_digits_for_cumulative_energy()
            print(res)
            print('----')
            time.sleep(5)

            print('---- measured cumulative energy (normal direction) [kWh] ----')
            res = mo.get_measured_cumulative_energy()
            print(res, 'kWh')
            print('----')
            time.sleep(5)

            print('---- measured cumulative energy (reverse direction) [kWh] ----')
            res = mo.get_measured_cumulative_energy(reverse=True)
            print(res, 'kWh')
            print('----')
            time.sleep(5)

            print('---- historical cumulative energy 1 (normal direction) [kWh] ----')
            res = mo.get_historical_cumulative_energy_1()
            pprint(res)
            print('----')
            time.sleep(5)

            print('---- historical cumulative energy 1 (reverse direction) [kWh] ----')
            res = mo.get_historical_cumulative_energy_1(reverse=True)
            pprint(res)
            print('----')
            time.sleep(5)

            print('---- instantaneous power [W] ----')
            res = mo.get_instantaneous_power()
            print(res, 'W')
            print('----')
            time.sleep(5)

            print('---- instantaneous current [A] ----')
            res = mo.get_instantaneous_current()
            print(res, 'A')
            print('----')
            time.sleep(5)

            print('---- cumulative energy measured at fixed time (normal direction) [kWh] ----')
            res = mo.get_cumulative_energy_measured_at_fixed_time()
            pprint(res)
            print('----')
            time.sleep(5)

            print('---- cumulative energy measured at fixed time (reverse direction) [kWh] ----')
            res = mo.get_cumulative_energy_measured_at_fixed_time(reverse=True)
            pprint(res)
            print('----')
            time.sleep(5)

            print('---- historical_cumulative_energy_2 [kWh] ----')
            res = mo.get_historical_cumulative_energy_2()
            pprint(res)
            print('----')
            time.sleep(5)

            print('---- setting parameters with request_to_set() ----')
            now = datetime.datetime.now()
            mo.request_to_set(day_for_historical_data_1={'day': 0},
                              time_for_historical_data_2={'timestamp': now, 'num_of_data_points': 12},
                              )
            print('----')
            time.sleep(5)

            print('---- testing all EchonetPropertyCode using request_to_get() one by one ----')
            all_codes = [e for e in momonga.EchonetPropertyCode]
            for epc_req in all_codes:
                try:
                    epc, r = mo.request_to_get({epc_req}).popitem()
                    print(f'epc: {epc.name}, result: {r}')
                except momonga.MomongaResponseNotPossible:
                    print(f'epc: {epc_req.name} not possible')
            print('----')
            time.sleep(5)

            print('---- requesting with 4 EPCs using request_to_get() at once ----')
            res = mo.request_to_get({
                EPC.instantaneous_power,
                EPC.instantaneous_current,
                EPC.measured_cumulative_energy,
                EPC.measured_cumulative_energy_reversed,
            })
            for epc, r in res.items():
                print(f'epc: {epc.name}, result: {r}')
            print('----')
            time.sleep(5)

            print('---- requesting with multiple EPCs using request_to_set_raw() ----')
            now = datetime.datetime.now()
            mo.request_to_set_raw(
                [
                    EchonetPropertyWithData(
                        0xE5,
                        mo.build_edata_to_set_day_for_historical_data_1(0)
                    ),
                    EchonetPropertyWithData(
                        0xED,
                        mo.build_edata_to_set_time_for_historical_data_2(now, 12),
                    ),
                ]
            )
            print('----')
            time.sleep(5)

            print('---- requesting with multiple EPCs using request_to_get_raw() ----')
            res = mo.request_to_get_raw(
                [
                    EchonetProperty(0xD3),  # coefficient_for_cumulative_energy
                    EchonetProperty(0xE1),  # unit_for_cumulative_energy
                    EchonetProperty(0xE0),  # measured_cumulative_energy
                    EchonetProperty(0xE3),  # measured_cumulative_energy_reversed
                ]
            )
            for r in res:
                print(f'epc: {hex(r.epc)}, result: {mo.parser_map[r.epc](r.edt)}')
            print('----')
            time.sleep(5)

            print('---- closing the session ----')
            exit_code = 0
            break
    except (momonga.MomongaSkScanFailure,
            momonga.MomongaSkJoinFailure,
            momonga.MomongaNeedToReopen,
            ):
        time.sleep(60)
        continue
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        break

exit(exit_code)
