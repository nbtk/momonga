import momonga
import os
import sys

def main():
    rbid = os.environ.get('MOMONGA_ROUTEB_ID')
    pwd = os.environ.get('MOMONGA_ROUTEB_PASSWORD')
    dev = os.environ.get('MOMONGA_DEV_PATH')

    if not rbid or not pwd or not dev:
        print("Please set MOMONGA_ROUTEB_ID, MOMONGA_ROUTEB_PASSWORD, and MOMONGA_DEV_PATH environment variables.", file=sys.stderr)
        sys.exit(1)

    print("Connecting to the smart meter... This may take a minute.")
    
    try:
        with momonga.Momonga(rbid, pwd, dev) as mo:
            print("\nConnection established.\n")
            
            def print_props(props, label):
                print(f"--- Supported Properties ({label}) ---")
                if props:
                    for prop in sorted(props, key=lambda x: x.value if hasattr(x, 'value') else x):
                        if type(prop) is int:
                            print(f"0x{prop:02X} : (Unknown/Unsupported by Momonga)")
                        else:
                            print(f"0x{prop.value:02X} : {prop.name}")
                else:
                    print("None found or not supported.")
                print()

            print_props(mo.get_properties_to_get_values(), "GET")
            print_props(mo.get_properties_to_set_values(), "SET")
            print_props(mo.get_properties_for_status_notification(), "Status Change Notification")
            
    except (momonga.MomongaSkScanFailure,
            momonga.MomongaSkJoinFailure,
            momonga.MomongaNeedToReopen) as e:
        print(f"Connection Failed - {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
