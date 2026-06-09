from collections.abc import Callable

from .momonga_device_enum import DeviceType
from .momonga_response import DeviceStrategy, SkParsedEvent, SkParsedRxUdp


class BP35C2Strategy(DeviceStrategy):
    device_type = DeviceType.BP35C2

    def parse_event(self, parts: list[str]) -> SkParsedEvent | None:
        # BP35C2 EVENT format includes SIDE: EVENT num addr side [param]
        # Verified on hardware: b'EVENT 21 FE80:... 0 00\r\n'
        if len(parts) < 3:
            return None
        side = int(parts[3], 16) if len(parts) > 3 else None
        param = int(parts[4], 16) if len(parts) > 4 else None
        return SkParsedEvent(num=int(parts[1], 16), src_addr=parts[2], side=side, param=param)

    def parse_erxudp(self, parts: list[str]) -> SkParsedRxUdp | None:
        if len(parts) < 11:
            return None
        lqi = int(parts[6], 16)
        return SkParsedRxUdp(
            src_addr=parts[1], dst_addr=parts[2],
            src_port=int(parts[3], 16), dst_port=int(parts[4], 16),
            src_mac=bytes.fromhex(parts[5]),
            lqi=lqi, rssi=0.275 * lqi - 104.27,
            sec=int(parts[7], 16), side=int(parts[8], 16),
            data=bytes.fromhex(parts[10]),
        )

    def skscan_command(self, duration: int) -> list[str]:
        return ['SKSCAN', '2', 'FFFFFFFF', str(duration), '0']

    def sksendto_args(self, handle: int, ip6_addr: str, port: int, sec: int, side: int, length: int) -> list[str]:
        return ['SKSENDTO', str(handle), ip6_addr, '%04X' % port, str(sec), str(side), '%04X' % length]

    def decode_scan_side(self, extract: Callable[[str], str]) -> int | None:
        return int(extract('Side:').split(':')[-1], 16)


class BP35A1Strategy(DeviceStrategy):
    device_type = DeviceType.BP35A1

    def parse_event(self, parts: list[str]) -> SkParsedEvent | None:
        if len(parts) < 3:
            return None
        param = int(parts[3], 16) if len(parts) > 3 else None
        return SkParsedEvent(num=int(parts[1], 16), src_addr=parts[2], side=None, param=param)

    def parse_erxudp(self, parts: list[str]) -> SkParsedRxUdp | None:
        if len(parts) < 9:
            return None
        return SkParsedRxUdp(
            src_addr=parts[1], dst_addr=parts[2],
            src_port=int(parts[3], 16), dst_port=int(parts[4], 16),
            src_mac=bytes.fromhex(parts[5]),
            sec=int(parts[6], 16), side=None,
            data=bytes.fromhex(parts[8]),
        )

    def skscan_command(self, duration: int) -> list[str]:
        return ['SKSCAN', '2', 'FFFFFFFF', str(duration)]

    def sksendto_args(self, handle: int, ip6_addr: str, port: int, sec: int, side: int, length: int) -> list[str]:
        return ['SKSENDTO', str(handle), ip6_addr, '%04X' % port, str(sec), '%04X' % length]

    def decode_scan_side(self, extract: Callable[[str], str]) -> int | None:
        return None
