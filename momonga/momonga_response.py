import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

from .momonga_exception import MomongaKeyError
from .momonga_device_enum import DeviceType


class SkEventNum(IntEnum):
    """Wi-SUN module event numbers (parsed from hex strings, e.g. 'EVENT 21' → 0x21)."""
    neighbor_discovery  = 0x02
    tx_done             = 0x21
    rejoin_failed       = 0x24
    rejoined            = 0x25
    session_closed      = 0x27
    no_session          = 0x28
    session_lifetime    = 0x29
    rate_limit_exceeded = 0x32
    rate_limit_released = 0x33


class SkTxResult(IntEnum):
    """Param values for EVENT tx_done (0x21)."""
    success              = 0x00
    failure              = 0x01
    neighbor_solicitation = 0x02


@dataclass(frozen=True)
class SkParsedEvent:
    """Typed representation of an EVENT line from the Wi-SUN module."""
    num: int           # event number parsed as hex (e.g. 'EVENT 21' → int('21',16) = 33)
    src_addr: str
    side: int | None
    param: int | None  # present only for EVENT 21 (tx result)


@dataclass(frozen=True)
class SkParsedRxUdp:
    """Typed representation of an ERXUDP line from the Wi-SUN module."""
    src_addr: str
    dst_addr: str
    src_port: int
    dst_port: int
    src_mac: bytes
    side: int | None
    sec: int
    data: bytes
    lqi: int | None = None
    rssi: float | None = None


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


logger = logging.getLogger(__name__)


class MomongaSkResponseBase:
    def __init__(self, res):
        self.raw_response = res
        self.decode()

    def decode(self):
        pass

    def extract(self, key):
        for elm in reversed(self.raw_response):
            if key in elm:
                return elm
        raise MomongaKeyError(key)


class SkVerResponse(MomongaSkResponseBase):
    def decode(self):
        res_list = self.extract('EVER').split()
        self.stack_ver = res_list[1]


class SkAppVerResponse(MomongaSkResponseBase):
    def decode(self):
        res_list = self.extract('EAPPVER').split()
        self.app_ver = res_list[1]


class SkInfoResponse(MomongaSkResponseBase):
    def decode(self):
        res_list = self.extract('EINFO').split()
        self.ip6_addr = res_list[1]
        self.mac_addr = bytes.fromhex(res_list[2])
        self.channel = int(res_list[3], 16)
        self.pan_id = bytes.fromhex(res_list[4])
        self.side = int(res_list[5], 16)


class SkScanResponse(MomongaSkResponseBase):
    def __init__(self, res, strategy: DeviceStrategy):
        self.strategy = strategy
        super().__init__(res)

    def decode(self):
        self.channel = int(self.extract('Channel:').split(':')[-1], 16)
        self.channel_page = int(self.extract('Channel Page:').split(':')[-1], 16)
        self.pan_id = bytes.fromhex(self.extract('Pan ID:').split(':')[-1])
        self.mac_addr = bytes.fromhex(self.extract('Addr:').split(':')[-1])
        self.lqi = int(self.extract('LQI:').split(':')[-1], 16)
        self.rssi = 0.275 * self.lqi - 104.27
        self.side = self.strategy.decode_scan_side(self.extract)
        self.pair_id = bytes.fromhex(self.extract('PairID:').split(':')[-1])


class SkLl64Response(MomongaSkResponseBase):
    def decode(self):
        self.ip6_addr = self.extract('FE80:')
