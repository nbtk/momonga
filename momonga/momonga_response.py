from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from .momonga_exception import MomongaKeyError, MomongaSkResponseNotExpected


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




class MomongaSkResponseBase:
    def __init__(self, res: list[str]) -> None:
        self.raw_response = res
        try:
            self.decode()
        except MomongaSkResponseNotExpected:
            raise
        except (ValueError, LookupError) as err:
            raise MomongaSkResponseNotExpected(
                'Could not read the %s response: %s: %s'
                % (type(self).__name__, type(err).__name__, err)) from err

    def decode(self) -> None:
        pass

    def extract(self, key: str) -> str:
        for elm in reversed(self.raw_response):
            if key in elm:
                return elm
        raise MomongaKeyError(key)


class SkVerResponse(MomongaSkResponseBase):
    stack_ver: str

    def decode(self) -> None:
        res_list = self.extract('EVER').split()
        self.stack_ver = res_list[1]


class SkAppVerResponse(MomongaSkResponseBase):
    app_ver: str

    def decode(self) -> None:
        res_list = self.extract('EAPPVER').split()
        self.app_ver = res_list[1]


class SkInfoResponse(MomongaSkResponseBase):
    ip6_addr: str
    mac_addr: bytes
    channel: int
    pan_id: bytes
    side: int

    def decode(self) -> None:
        res_list = self.extract('EINFO').split()
        self.ip6_addr = res_list[1]
        self.mac_addr = bytes.fromhex(res_list[2])
        self.channel = int(res_list[3], 16)
        self.pan_id = bytes.fromhex(res_list[4])
        self.side = int(res_list[5], 16)


class SkScanResponse(MomongaSkResponseBase):
    channel: int
    channel_page: int
    pan_id: bytes
    mac_addr: bytes
    lqi: int
    rssi: float
    side: int | None
    pair_id: bytes

    def __init__(self,
                 res: list[str],
                 decode_side: Callable[[Callable[[str], str]], int | None],
                 ) -> None:
        self.decode_side = decode_side
        super().__init__(res)

    def decode(self) -> None:
        self.channel = int(self.extract('Channel:').split(':')[-1], 16)
        self.channel_page = int(self.extract('Channel Page:').split(':')[-1], 16)
        self.pan_id = bytes.fromhex(self.extract('Pan ID:').split(':')[-1])
        self.mac_addr = bytes.fromhex(self.extract('Addr:').split(':')[-1])
        self.lqi = int(self.extract('LQI:').split(':')[-1], 16)
        self.rssi = 0.275 * self.lqi - 104.27
        self.side = self.decode_side(self.extract)
        self.pair_id = bytes.fromhex(self.extract('PairID:').split(':')[-1])


class SkLl64Response(MomongaSkResponseBase):
    ip6_addr: str

    def decode(self) -> None:
        self.ip6_addr = self.extract('FE80:')
