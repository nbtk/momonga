from collections.abc import Callable
from enum import Enum
from typing import Protocol

from .momonga_response import SkParsedEvent, SkParsedRxUdp


class DeviceType(Enum):
    """Which ROHM module is on the other end of the serial port.

    The two that are supported do not speak the same SK dialect. BP35C2
    puts a side field in EVENT and ERXUDP, takes an extra argument in
    SKSCAN and SKSENDTO, and reports the link quality of each frame;
    BP35A1 does none of those, which is why lqi and rssi stay None on it.
    Holding that difference is what DeviceStrategy is for.

    SKINFO tells the two apart: where BP35C2 answers with a side of 0 or 1,
    BP35A1 answers 0xFFFE, having no side to report. BP35C0 and BP35C1 are
    names here with no strategy behind them.
    """
    BP35A1 = 1  # inside RL7023 Stick-D/IPS, discontinued
    BP35C0 = 2  # Wi-SUN/HAN, no strategy
    BP35C1 = 3  # Wi-SUN/E-HAN, no strategy
    BP35C2 = 4  # inside RS-WSUHA-P and RL7023 Stick-D/DSS


class DeviceStrategy(Protocol):
    """Encapsulates all behavior that differs between Wi-SUN module models."""
    device_type: DeviceType

    def parse_event(self, parts: list[str]) -> SkParsedEvent | None: ...
    def parse_erxudp(self, parts: list[str]) -> SkParsedRxUdp | None: ...
    def skscan_command(self, duration: int) -> list[str]: ...
    def sksendto_args(self, handle: int, ip6_addr: str, port: int, sec: int, side: int, length: int) -> list[str]: ...
    def decode_scan_side(self, extract: Callable[[str], str]) -> int | None: ...


def parse_sk_line(line: str, strategy: DeviceStrategy) -> SkParsedEvent | SkParsedRxUdp | None:
    """Parse a raw Wi-SUN serial line into a typed event object.

    Returns None for lines that are not EVENT or ERXUDP (e.g. OK, EPANDESC).
    """
    parts = line.split()
    if not parts:
        return None

    if parts[0] == 'EVENT':
        try:
            return strategy.parse_event(parts)
        except (ValueError, IndexError):
            return None

    if parts[0] == 'ERXUDP':
        try:
            return strategy.parse_erxudp(parts)
        except (ValueError, IndexError):
            return None

    return None


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
