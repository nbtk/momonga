
import logging
import os
import sys
import time
import momonga

from pprint import pprint


log_fmt = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s - %(message)s')
log_hnd = logging.StreamHandler()
log_hnd.setFormatter(log_fmt)
momonga.logger.addHandler(log_hnd)
momonga.logger.setLevel(logging.DEBUG)
momonga.session_manager_logger.addHandler(log_hnd)
momonga.session_manager_logger.setLevel(logging.DEBUG)
momonga.sk_wrapper_logger.addHandler(log_hnd)
momonga.sk_wrapper_logger.setLevel(logging.DEBUG)

# set the following parameters before run.
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
            pprint(res)
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

            exit_code = 0
            break
    except (momonga.MomongaSkScanFailure,
            momonga.MomongaSkJoinFailure,
            momonga.MomongaNeedToReopen,
           ):
        time.sleep(60)
        continue
    except Exception as e:
        print('%s: %s' % (type(e).__name__, str(e)), file=sys.stderr)
        break

exit(exit_code)
