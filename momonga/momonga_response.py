import logging
from dataclasses import dataclass
from enum import IntEnum

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


def parse_sk_line(line: str, device_type: DeviceType) -> SkParsedEvent | SkParsedRxUdp | None:
    """Parse a raw Wi-SUN serial line into a typed event object.

    Returns None for lines that are not EVENT or ERXUDP (e.g. OK, EPANDESC).
    """
    parts = line.split()
    if not parts:
        return None

    if parts[0] == 'EVENT' and len(parts) >= 3:
        try:
            num = int(parts[1], 16)
            src_addr = parts[2]
            match device_type:
                case DeviceType.BP35A1:
                    side = None
                    param = int(parts[3], 16) if len(parts) > 3 else None
                case _:
                    side = int(parts[3], 16) if len(parts) > 3 else None
                    param = int(parts[4], 16) if len(parts) > 4 else None
            return SkParsedEvent(num=num, src_addr=src_addr, side=side, param=param)
        except (ValueError, IndexError):
            return None

    if parts[0] == 'ERXUDP':
        try:
            match device_type:
                case DeviceType.BP35A1:
                    if len(parts) < 9:
                        return None
                    return SkParsedRxUdp(
                        src_addr=parts[1], dst_addr=parts[2],
                        src_port=int(parts[3], 16), dst_port=int(parts[4], 16),
                        src_mac=bytes.fromhex(parts[5]),
                        sec=int(parts[6], 16), side=None,
                        data=bytes.fromhex(parts[8]),
                    )
                case _:  # BP35C2
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


class MomongaSkDeviceDependentResponse(MomongaSkResponseBase):
    def __init__(self, res, device_type: DeviceType):
        self.device_type = device_type
        super().__init__(res)


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


class SkScanResponse(MomongaSkDeviceDependentResponse):
    def decode(self):
        self.channel = int(self.extract('Channel:').split(':')[-1], 16)
        self.channel_page = int(self.extract('Channel Page:').split(':')[-1], 16)
        self.pan_id = bytes.fromhex(self.extract('Pan ID:').split(':')[-1])
        self.mac_addr = bytes.fromhex(self.extract('Addr:').split(':')[-1])
        self.lqi = int(self.extract('LQI:').split(':')[-1], 16)
        self.rssi = 0.275 * self.lqi - 104.27
        match self.device_type:
            case DeviceType.BP35A1:
                self.side = None
            case DeviceType.BP35C2:
                self.side = int(self.extract('Side:').split(':')[-1], 16)
            case _:
                logger.warning('Unknown device type "%s" detected in SkScanResponse. Assuming BP35C2 behavior.', self.device_type)
                self.side = int(self.extract('Side:').split(':')[-1], 16)
        self.pair_id = bytes.fromhex(self.extract('PairID:').split(':')[-1])


class SkLl64Response(MomongaSkResponseBase):
    def decode(self):
        self.ip6_addr = self.extract('FE80:')


class SkSendToResponse(MomongaSkDeviceDependentResponse):
    def decode(self):
        self.res_list = self.extract('EVENT 21').split()
        self.event_num = int(self.res_list[1], 16)
        self.src_addr = self.res_list[2]
        match self.device_type:
            case DeviceType.BP35A1:
                self.side = None
                self.param = int(self.res_list[3], 16)
            case DeviceType.BP35C2:
                self.side = int(self.res_list[3], 16)
                self.param = int(self.res_list[4], 16)
            case _:
                logger.warning('Unknown device type "%s" detected in SkSendToResponse. Assuming BP35C2 behavior.', self.device_type)
                self.side = int(self.res_list[3], 16)
                self.param = int(self.res_list[4], 16)


class SkEventRxUdp(MomongaSkDeviceDependentResponse):
    def decode(self):
        self.res_list = self.extract('ERXUDP').split()
        self.src_addr = self.res_list[1]
        self.des_addr = self.res_list[2]
        self.src_port = int(self.res_list[3], 16)
        self.dst_port = int(self.res_list[4], 16)
        self.src_mac = bytes.fromhex(self.res_list[5])
        match self.device_type:
            case DeviceType.BP35A1:
                self.lqi = None
                self.rssi = None
                self.sec = int(self.res_list[6], 16)
                self.side = None
                self.data_len = int(self.res_list[7], 16)
                self.data = bytes.fromhex(self.res_list[8])
            case DeviceType.BP35C2:
                self.lqi = int(self.res_list[6], 16)
                self.rssi = 0.275 * self.lqi - 104.27
                self.sec = int(self.res_list[7], 16)
                self.side = int(self.res_list[8], 16)
                self.data_len = int(self.res_list[9], 16)
                self.data = bytes.fromhex(self.res_list[10])
            case _:
                logger.warning('Unknown device type "%s" detected in SkEventRxUdp. Assuming BP35C2 behavior.', self.device_type)
                self.lqi = int(self.res_list[6], 16)
                self.rssi = 0.275 * self.lqi - 104.27
                self.sec = int(self.res_list[7], 16)
                self.side = int(self.res_list[8], 16)
                self.data_len = int(self.res_list[9], 16)
                self.data = bytes.fromhex(self.res_list[10])
