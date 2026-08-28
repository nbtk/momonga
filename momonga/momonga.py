import datetime
import logging
import queue
import threading
import time

from collections.abc import Callable, Iterable
from typing import TypedDict, Any, Self

from .momonga_echonet_data import (EchonetProperty,
                                   EchonetPropertyWithData,
                                   EchonetDataParser,
                                   EchonetDataBuilder,
                                   PARSER_MAP,
                                   ENERGY_PARSERS)
from .momonga_echonet_enum import (EchonetServiceCode, EchonetPropertyCode,
                                   ECHONET_LITE_EHD, ECHONET_LITE_PORT,
                                   ECHONET_EHD_SLICE, ECHONET_TID_SLICE,
                                   ECHONET_SEOJ_SLICE, ECHONET_DEOJ_SLICE,
                                   ECHONET_ESV_OFFSET, ECHONET_OPC_OFFSET,
                                   SMART_METER_EOJ, CONTROLLER_EOJ)
from .momonga_exception import (MomongaError,
                                MomongaResponseNotExpected,
                                MomongaResponseNotPossible,
                                MomongaNeedToReopen,
                                MomongaValueError,
                                MomongaRuntimeError)
from .momonga_response import SkEventNum, SkTxResult, SkParsedEvent, SkParsedRxUdp
from .momonga_session_manager import MomongaSessionManager, SESSION_ENDED
from .momonga_session_manager import logger as session_manager_logger
from .momonga_sk_wrapper import logger as sk_wrapper_logger

logger = logging.getLogger(__name__)

_INFC_RES_XMIT_LIMIT = 15


class _ReplayableIterator:
    def __init__(self, source: Iterable[float]) -> None:
        self._source = source
        self._seen: list[float] = []

    def __iter__(self):
        yield from self._seen
        for delay in self._source:
            self._seen.append(delay)
            yield delay


class Momonga:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                 reopen_delays: Iterable[float] | Callable[[], Iterable[float]] | None = None,
                 scan_retries: int = 3,
                 join_retries: int = 3,
                 ) -> None:
        self.xmit_retries: int = 12
        self.recv_timeout: int | float = 12
        self.xmit_timeout: int | float = 300
        self.internal_xmit_interval: int | float = 5
        self._transaction_id: int = 0
        self.energy_unit: int | float = 1
        self.energy_coefficient: int = 1
        self.lqi: int | None = None
        self.rssi: float | None = None
        self.is_open: bool = False
        # A one-shot iterator would be spent after the first outage, so it is
        # wrapped to replay. That only holds inside this instance: hand the
        # same iterator to a second Momonga and it carries on from where this
        # one left it. Pass a callable to say "a fresh schedule each time" and
        # have it hold everywhere.
        if (reopen_delays is not None and not callable(reopen_delays)
                and iter(reopen_delays) is reopen_delays):
            reopen_delays = _ReplayableIterator(reopen_delays)
        self.reopen_delays: Iterable[float] | Callable[[], Iterable[float]] | None = reopen_delays
        self._request_lock: threading.Lock = threading.Lock()
        self._reopen_lock: threading.Lock = threading.Lock()
        self._reopen_done: threading.Event = threading.Event()
        self._reopen_done.set()
        self._local: threading.local = threading.local()
        self._rbid: str = rbid
        self._pwd: str = pwd
        self._dev: str = dev
        self._baudrate: int = baudrate
        self._reset_dev: bool = reset_dev
        self._scan_retries = scan_retries
        self._join_retries = join_retries
        self.session_manager = MomongaSessionManager(rbid, pwd, dev, baudrate, reset_dev,
                                                     scan_retries, join_retries)

    def _init_energy_unit(self) -> None:
        logger.debug('Initializing the energy unit and coefficient.')
        self.energy_unit = self.get_unit_for_cumulative_energy()
        time.sleep(self.internal_xmit_interval)
        try:
            self.energy_coefficient = self.get_coefficient_for_cumulative_energy()
        except MomongaResponseNotPossible:  # due to the property 0xD3 is optional.
            self.energy_coefficient = 1
        time.sleep(self.internal_xmit_interval)

    @property
    def xmit_timeout(self) -> int | float:
        return self._xmit_timeout

    @xmit_timeout.setter
    def xmit_timeout(self, value: int | float) -> None:
        """Seconds one request may spend waiting to transmit.

        None used to mean no ceiling. It never did: the gate wait ran its own
        schedule of sixty waits of a minute and then raised MomongaNeedToReopen
        rather than MomongaXmitTimeout, while the SK command underneath was
        handed no deadline at all and waited on the command lock without one.
        Setting 3600 gives that same schedule with neither surprise, so the
        value that meant "no ceiling" is gone rather than documented.
        """
        if value is None:
            raise MomongaValueError('xmit_timeout must be a number of seconds.'
                                    ' Use 3600 for the longest wait it used to allow.')
        if value < 0:
            raise MomongaValueError('xmit_timeout must not be negative, not %s' % value)
        self._xmit_timeout = value

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, type, value, traceback) -> None:
        if value is None:
            self.close()
            return

        try:
            self.close()
        except Exception:
            logger.warning('Failed to close the session while %s was propagating. '
                           'Keeping the original exception.',
                           type.__name__, exc_info=True)

    def _route_meter_frame(self, frame: SkParsedRxUdp) -> None:
        self.lqi = frame.lqi
        self.rssi = frame.rssi
        seoj = frame.data[ECHONET_SEOJ_SLICE] if len(frame.data) >= 7 else b''
        if seoj != SMART_METER_EOJ:
            return
        esv = frame.data[ECHONET_ESV_OFFSET] if len(frame.data) > 10 else -1
        if esv in (EchonetServiceCode.inf, EchonetServiceCode.infc):
            self.session_manager.notif_q.put(frame)
        else:
            self.session_manager.recv_q.put(frame)

    def open(self) -> Self:
        logger.info('Opening Momonga.')
        self.lqi = None
        self.rssi = None
        self.session_manager.on_meter_frame = self._route_meter_frame
        self.session_manager.open()
        time.sleep(self.internal_xmit_interval)
        self.is_open = True
        try:
            self._init_energy_unit()
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise
        logger.info('Momonga is open.')
        return self

    def close(self) -> None:
        logger.info('Closing Momonga.')
        self.is_open = False
        self.session_manager.close()
        logger.info('Momonga is closed.')

    def reopen(self) -> None:
        logger.info('Reopening Momonga session.')
        self._local.reopening = True
        self._reopen_done.clear()
        try:
            try:
                self.close()
            except Exception:
                logger.debug('Error closing Momonga during reopen (ignored)', exc_info=True)

            self.session_manager = MomongaSessionManager(
                self._rbid, self._pwd, self._dev, self._baudrate, self._reset_dev,
                self._scan_retries, self._join_retries
            )
            self.open()
        finally:
            self._local.reopening = False
            self._reopen_done.set()
        logger.info('Momonga session reopened successfully.')

    @staticmethod
    def _property_block_is_complete(data: bytes, opc: int) -> bool:
        cur = 12
        for _ in range(opc):
            if cur + 2 > len(data):
                return False
            cur += 2 + data[cur + 1]
            if cur > len(data):
                return False
        return True

    @staticmethod
    def _remaining(deadline: int | float | None) -> float | None:
        return None if deadline is None else max(0.0, deadline - time.monotonic())

    def _reopen_in_progress(self) -> bool:
        return not self._reopen_done.is_set() and not getattr(self._local, 'reopening', False)

    def get_notification(self, timeout: int | float | None = None) -> dict | None:
        return self._read_notification(timeout, None)

    def _read_notification(self,
                            timeout: int | float | None,
                            reply_budget: int | float | None,
                            ) -> dict | None:
        deadline = None if timeout is None else time.monotonic() + timeout

        if not self.is_open:
            if not self._reopen_in_progress():
                raise MomongaRuntimeError('Momonga is not open.')
            if not self._reopen_done.wait(timeout=self._remaining(deadline)):
                return None
            if not self.is_open:
                raise MomongaRuntimeError('Momonga is not open.')

        session_manager = self.session_manager
        session_manager.raise_if_receiver_died()

        try:
            frame = session_manager.notif_q.get(timeout=self._remaining(deadline))
        except queue.Empty:
            return None

        if frame is SESSION_ENDED:
            return None

        data = frame.data
        if len(data) < 12:
            logger.warning('Received a malformed notification frame (too short: %d bytes). Discarding.' % len(data))
            return None
        esv = data[ECHONET_ESV_OFFSET]
        opc = data[ECHONET_OPC_OFFSET]

        if not self._property_block_is_complete(data, opc):
            logger.warning('Received a truncated notification frame (%d bytes for OPC %d). Discarding.'
                           % (len(data), opc))
            return None

        if esv == EchonetServiceCode.infc:
            self._send_infc_res(data, self._remaining(deadline)
                                if reply_budget is None else reply_budget)

        properties = {}
        cur = 12
        for _ in range(opc):
            try:
                epc = EchonetPropertyCode(data[cur])
            except ValueError:
                epc = data[cur]
            cur += 1
            pdc = data[cur]
            cur += 1
            edt = data[cur:cur + pdc] if pdc > 0 else None
            cur += pdc

            if edt is not None:
                properties[epc] = self._parse_or_keep_raw(epc, edt)
            else:
                properties[epc] = None

        return {'esv': EchonetServiceCode(esv), 'properties': properties}

    @staticmethod
    def _parse_or_wrap(epc: EchonetPropertyCode | int,
                       edt: bytes | None,
                       parser: Callable[..., Any],
                       *args: Any) -> Any:
        """Read one property, or say that the response could not be read.

        A parser hands an EDT that got past its length check to datetime, int
        and bytes.decode, and those raise ValueError or UnicodeDecodeError for
        a value out of range - a month of 13, a year of 0, a serial number
        that is not UTF-8. Those are the meter being unreadable, which is what
        MomongaResponseNotExpected means, and a caller told to catch
        MomongaError has no reason to expect anything else.
        """
        try:
            return parser(edt, *args)
        except MomongaError:
            raise
        except Exception as e:
            raise MomongaResponseNotExpected(
                'Could not read EPC %02X out of the response (%s bytes). %s: %s'
                % (epc, 'no' if edt is None else len(edt), type(e).__name__, e)) from e

    def _parse_or_keep_raw(self, epc: EchonetPropertyCode | int, edt: bytes) -> Any:
        parser = PARSER_MAP.get(epc)
        if parser is None:
            return edt
        try:
            if parser in ENERGY_PARSERS:
                return parser(edt, self.energy_unit, self.energy_coefficient)
            return parser(edt)
        except Exception as e:
            logger.warning('Could not read EPC %02X out of a notification (%d bytes). '
                           'Keeping the raw value. %s: %s'
                           % (epc, len(edt), type(e).__name__, e))
            return edt

    def _send_infc_res(self,
                        infc_data: bytes,
                        timeout: int | float | None = None,
                        ) -> None:
        tid_int = int.from_bytes(infc_data[ECHONET_TID_SLICE], 'big')
        header = self._build_request_header(tid_int, EchonetServiceCode.infc_res)
        opc = infc_data[ECHONET_OPC_OFFSET]
        props = b''
        cur = 12
        for _ in range(opc):
            props += infc_data[cur:cur + 1]  # EPC
            cur += 1
            pdc = infc_data[cur]
            cur += 1 + pdc
            props += b'\x00'               # PDC = 0, no EDT in response
        payload = header + opc.to_bytes(1, 'big') + props
        budget = _INFC_RES_XMIT_LIMIT if timeout is None else min(_INFC_RES_XMIT_LIMIT, timeout)
        try:
            self.session_manager.xmitter(payload, timeout=budget)
        except Exception:
            logger.warning('Failed to send INFC_Res.', exc_info=True)

    def _get_transaction_id(self) -> int:
        self._transaction_id += 1
        return self._transaction_id

    @staticmethod
    def _build_request_header(tid: int, esv: EchonetServiceCode) -> bytes:
        ehd = ECHONET_LITE_EHD
        tid = tid.to_bytes(4, 'big')[-2:]
        seoj = CONTROLLER_EOJ
        deoj = SMART_METER_EOJ
        esv = esv.to_bytes(1, 'big')
        return ehd + tid + seoj + deoj + esv

    def _build_request_payload_with_data(self,
                                          tid: int,
                                          esv: EchonetServiceCode,
                                          properties_with_data: list[EchonetPropertyWithData],
                                          ) -> bytes:
        header = self._build_request_header(tid, esv)
        opc = len(properties_with_data).to_bytes(1, 'big')
        payload = header + opc
        for pd in properties_with_data:
            epc = pd.epc.to_bytes(1, 'big')
            pdc = len(pd.edt).to_bytes(1, 'big')
            edt = pd.edt
            payload += epc + pdc + edt

        return payload

    def _build_request_payload(self,
                                tid: int,
                                esv: EchonetServiceCode,
                                properties: list[EchonetProperty],
                                ) -> bytes:
        header = self._build_request_header(tid, esv)  # get
        opc = len(properties).to_bytes(1, 'big')
        payload = header + opc
        for p in properties:
            epc = p.epc.to_bytes(1, 'big')
            pdc = b'\x00'
            payload += epc + pdc

        return payload

    @staticmethod
    def _extract_response_payload(data: bytes,
                                   tid: int,
                                   req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                                   ) -> list[EchonetPropertyWithData]:
        if len(data) < 12:
            raise MomongaResponseNotExpected('The response is too short: %d bytes.' % len(data))

        ehd = data[ECHONET_EHD_SLICE]
        if ehd != ECHONET_LITE_EHD:
            raise MomongaResponseNotExpected('The data format is not ECHONET Lite EDATA format 1.')

        if data[ECHONET_TID_SLICE] != tid.to_bytes(4, 'big')[-2:]:
            raise MomongaResponseNotExpected('The transaction ID does not match.')

        seoj = data[ECHONET_SEOJ_SLICE]
        if seoj != SMART_METER_EOJ:
            raise MomongaResponseNotExpected('The source is not a smart meter.')

        deoj = data[ECHONET_DEOJ_SLICE]
        if deoj != CONTROLLER_EOJ:
            raise MomongaResponseNotExpected('The destination is not a controller.')

        esv = data[ECHONET_ESV_OFFSET]
        if 0x50 <= esv <= 0x5F:
            raise MomongaResponseNotPossible('The smart meter answered that it could not do this. ESV: %X' % esv)

        opc = data[ECHONET_OPC_OFFSET]
        req_opc = len(req_properties)
        if opc != req_opc:
            raise MomongaResponseNotExpected(
                'Unexpected packet format. OPC was expected to be %s but %d was set.' % (req_opc, opc))

        if not Momonga._property_block_is_complete(data, opc):
            raise MomongaResponseNotExpected('The response is truncated: %d bytes for OPC %d.' % (len(data), opc))

        properties = []
        cur = 12
        for rp in req_properties:
            try:
                epc = EchonetPropertyCode(data[cur])
            except ValueError:
                epc = data[cur]

            if epc != rp.epc:
                raise MomongaResponseNotExpected('The property code does not match. EPC: %X' % rp.epc)

            cur += 1
            pdc = data[cur]
            cur += 1
            if pdc == 0:
                edt = None
            else:
                edt_from = cur
                cur += pdc
                edt = data[edt_from:cur]

            properties.append(EchonetPropertyWithData(epc, edt))

        return properties

    def _request(self,
                  esv: EchonetServiceCode,
                  req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                  ) -> list[EchonetPropertyWithData]:
        logger.debug('Checking if Momonga is open: is_open=%s', self.is_open)
        if not self.is_open:
            if self._reopen_in_progress():
                raise MomongaNeedToReopen('A reopen of the Momonga session is in progress.')
            raise MomongaRuntimeError('Momonga is not open.')

        with self._request_lock:
            return self._request_locked(esv, req_properties)

    def _request_locked(self,
                         esv: EchonetServiceCode,
                         req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                         ) -> list[EchonetPropertyWithData]:
        tid = self._get_transaction_id()
        if esv == EchonetServiceCode.set_c:
            tx_payload = self._build_request_payload_with_data(tid, esv, req_properties)
        elif esv == EchonetServiceCode.get:
            tx_payload = self._build_request_payload(tid, esv, req_properties)
        else:
            raise MomongaRuntimeError('Unsupported service code.')

        while not self.session_manager.recv_q.empty():
            self.session_manager.recv_q.get()  # drops stored data

        xmit_budget = self.xmit_timeout
        for _ in range(self.xmit_retries):
            xmit_started = time.monotonic()
            try:
                self.session_manager.xmitter(tx_payload, timeout=xmit_budget)
            finally:
                if xmit_budget is not None:
                    xmit_budget = max(0.0, xmit_budget - (time.monotonic() - xmit_started))
            while True:
                try:
                    res = self.session_manager.recv_q.get(timeout=self.recv_timeout)
                except queue.Empty:
                    logger.warning('The request for transaction id "%04X" timed out.' % tid)
                    break  # to rexmit the request.

                if isinstance(res, SkParsedEvent):
                    if res.num == SkEventNum.tx_done:
                        param = res.param
                        if param == SkTxResult.success:
                            logger.info('Successfully transmitted a request packet for transaction id "%04X".' % tid)
                            continue
                        elif param == SkTxResult.failure:
                            logger.info('Retransmitting the request packet for transaction id "%04X".' % tid)
                            time.sleep(self.internal_xmit_interval)
                            break  # to rexmit the request.
                        elif param == SkTxResult.neighbor_solicitation:
                            logger.info('Transmitting neighbor solicitation packets.')
                            continue
                        else:
                            logger.debug('A message for event 21 with an unknown parameter "%s" will be ignored.' % param)
                            continue
                    elif res.num == SkEventNum.neighbor_discovery:
                        logger.info('Received a neighbor advertisement packet.')
                        continue
                    else:
                        continue
                elif isinstance(res, SkParsedRxUdp):
                    if not (res.src_port == res.dst_port == ECHONET_LITE_PORT):
                        continue
                    elif res.side:
                        continue
                    elif res.src_addr != self.session_manager.smart_meter_addr:
                        continue

                    try:
                        res_properties = self._extract_response_payload(res.data, tid, req_properties)
                    except MomongaResponseNotExpected:
                        continue

                    logger.info('Successfully received a response packet for transaction id "%04X".' % tid)
                    return res_properties
                else:
                    continue
        logger.error('Gave up obtaining a response for transaction id "%04X". Close Momonga and open it again.' % tid)
        raise MomongaNeedToReopen('Gave up obtaining a response for transaction id "%04X".'
                                  ' Close Momonga and open it again.' % tid)

    def _reopen_once(self, failed_session_manager: MomongaSessionManager) -> None:
        with self._reopen_lock:
            if self.session_manager is failed_session_manager:
                self.reopen()

    @staticmethod
    def _as_reopen_error(err: Exception) -> MomongaNeedToReopen:
        if isinstance(err, MomongaNeedToReopen):
            return err
        return MomongaNeedToReopen('%s: %s' % (type(err).__name__, err))

    def _request_with_recovery(self,
                                esv: EchonetServiceCode,
                                req_properties: list[EchonetPropertyWithData] | list[EchonetProperty],
                                ) -> list[EchonetPropertyWithData]:
        if self.reopen_delays is None or getattr(self._local, 'reopening', False):
            return self._request(esv, req_properties)

        failed_session_manager = self.session_manager
        try:
            return self._request(esv, req_properties)
        except (MomongaNeedToReopen, OSError) as initial_err:
            last_error: MomongaNeedToReopen = self._as_reopen_error(initial_err)
            logger.warning('Session needs reopen, attempting recovery.')

        schedule = (self.reopen_delays() if callable(self.reopen_delays)
                    else self.reopen_delays)
        for delay in schedule:
            delay = float(delay)
            if delay < 0:
                raise MomongaValueError('reopen_delays must not contain negative values.')

            time.sleep(delay)
            try:
                self._reopen_once(failed_session_manager)
            except (MomongaError, OSError) as err:
                logger.warning('Reopen attempt failed after waiting %s seconds: %s: %s',
                               delay, type(err).__name__, err)
                last_error = self._as_reopen_error(err)
                failed_session_manager = self.session_manager
                continue

            try:
                return self._request(esv, req_properties)
            except (MomongaNeedToReopen, OSError) as err:
                logger.warning('The request failed again after reopening: %s: %s',
                               type(err).__name__, err)
                last_error = self._as_reopen_error(err)
            failed_session_manager = self.session_manager

        logger.error('All reopen attempts exhausted.')
        raise last_error

    def _request_to_set(self,
                         properties_with_data: list[EchonetPropertyWithData]
                         ) -> None:
        self._request_with_recovery(EchonetServiceCode.set_c, properties_with_data)

    def _request_to_get(self,
                         properties: list[EchonetProperty],
                         ) -> list[EchonetPropertyWithData]:
        return self._request_with_recovery(EchonetServiceCode.get, properties)

    def get_operation_status(self) -> bool | None:
        req = EchonetProperty(EchonetPropertyCode.operation_status)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_operation_status)

    def get_installation_location(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.installation_location)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_installation_location)

    def get_standard_version(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.standard_version_information)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_standard_version_information)

    def get_fault_status(self) -> bool | None:
        req = EchonetProperty(EchonetPropertyCode.fault_status)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_fault_status)

    def get_manufacturer_code(self) -> bytes:
        req = EchonetProperty(EchonetPropertyCode.manufacturer_code)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_manufacturer_code)

    def get_serial_number(self) -> str:
        req = EchonetProperty(EchonetPropertyCode.serial_number)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_serial_number)

    def get_current_time_setting(self) -> datetime.time:
        req = EchonetProperty(EchonetPropertyCode.current_time_setting)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_current_time_setting)

    def get_current_date_setting(self) -> datetime.date:
        req = EchonetProperty(EchonetPropertyCode.current_date_setting)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_current_date_setting)

    def get_properties_for_status_notification(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_for_status_notification)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_property_map)

    def get_properties_to_set_values(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_to_set_values)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_property_map)

    def get_properties_to_get_values(self) -> set[EchonetPropertyCode | int]:
        req = EchonetProperty(EchonetPropertyCode.properties_to_get_values)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_property_map)

    def get_route_b_id(self) -> dict[str, bytes]:
        req = EchonetProperty(EchonetPropertyCode.route_b_id)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_route_b_id)

    def get_one_minute_measured_cumulative_energy(self) -> dict[str, datetime.datetime |
                                                                     dict[str, int | float | None]]:
        req = EchonetProperty(EchonetPropertyCode.one_minute_measured_cumulative_energy)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_one_minute_measured_cumulative_energy,
                                   self.energy_unit,
                                   self.energy_coefficient)

    def get_coefficient_for_cumulative_energy(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.coefficient_for_cumulative_energy)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_coefficient_for_cumulative_energy)

    def get_number_of_effective_digits_for_cumulative_energy(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.number_of_effective_digits_for_cumulative_energy)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_number_of_effective_digits_for_cumulative_energy)

    def get_measured_cumulative_energy(self,
                                       reverse: bool = False,
                                       ) -> int | float | None:
        if reverse is False:
            epc = EchonetPropertyCode.measured_cumulative_energy
        else:
            epc = EchonetPropertyCode.measured_cumulative_energy_reversed

        req = EchonetProperty(epc)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_measured_cumulative_energy,
                                   self.energy_unit,
                                   self.energy_coefficient)

    def get_unit_for_cumulative_energy(self) -> int | float:
        req = EchonetProperty(EchonetPropertyCode.unit_for_cumulative_energy)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_unit_for_cumulative_energy)

    def get_historical_cumulative_energy_1(self,
                                           day: int = 0,
                                           reverse: bool = False,
                                           ) -> list[dict[str, datetime.datetime | dict[str, int | float | None]]]:
        self.set_day_for_historical_data_1(day)

        if reverse is False:
            epc = EchonetPropertyCode.historical_cumulative_energy_1
        else:
            epc = EchonetPropertyCode.historical_cumulative_energy_1_reversed

        req = EchonetProperty(epc)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_historical_cumulative_energy_1,
                                   self.energy_unit,
                                   self.energy_coefficient)

    def set_day_for_historical_data_1(self,
                                      day: int = 0,
                                      ) -> None:
        edt = EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(day)
        req = EchonetPropertyWithData(EchonetPropertyCode.day_for_historical_data_1, edt)
        self._request_to_set([req])

    def get_day_for_historical_data_1(self) -> int:
        req = EchonetProperty(EchonetPropertyCode.day_for_historical_data_1)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_day_for_historical_data_1)

    def get_instantaneous_power(self) -> int | None:
        req = EchonetProperty(EchonetPropertyCode.instantaneous_power)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_instantaneous_power)

    def get_instantaneous_current(self) -> dict[str, float | None]:
        req = EchonetProperty(EchonetPropertyCode.instantaneous_current)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_instantaneous_current)

    def get_cumulative_energy_measured_at_fixed_time(self,
                                                     reverse: bool = False,
                                                     ) -> dict[str, datetime.datetime | int | float | None]:
        if reverse is False:
            epc = EchonetPropertyCode.cumulative_energy_measured_at_fixed_time
        else:
            epc = EchonetPropertyCode.cumulative_energy_measured_at_fixed_time_reversed

        req = EchonetProperty(epc)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_cumulative_energy_measured_at_fixed_time,
                                   self.energy_unit,
                                   self.energy_coefficient)

    def get_historical_cumulative_energy_2(self,
                                           timestamp: datetime.datetime | None = None,
                                           num_of_data_points: int = 12,
                                           ) -> list[dict[str, datetime.datetime |
                                                               dict[str, int | float | None]]]:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.set_time_for_historical_data_2(timestamp, num_of_data_points)

        req = EchonetProperty(EchonetPropertyCode.historical_cumulative_energy_2)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_historical_cumulative_energy_2,
                                   self.energy_unit,
                                   self.energy_coefficient)

    def set_time_for_historical_data_2(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 12,
                                       ) -> None:
        edt = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(timestamp,
                                                                               num_of_data_points)
        req = EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_2, edt)
        self._request_to_set([req])

    def get_time_for_historical_data_2(self) -> dict[str, datetime.datetime | None | int]:
        req = EchonetProperty(EchonetPropertyCode.time_for_historical_data_2)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_time_for_historical_data_2)

    def get_historical_cumulative_energy_3(self,
                                           timestamp: datetime.datetime | None = None,
                                           num_of_data_points: int = 10,
                                           ) -> list[dict[str, datetime.datetime |
                                                               dict[str, int | float | None]]]:
        if timestamp is None:
            timestamp = datetime.datetime.now()

        self.set_time_for_historical_data_3(timestamp, num_of_data_points)

        req = EchonetProperty(EchonetPropertyCode.historical_cumulative_energy_3)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_historical_cumulative_energy_3,
                                   self.energy_unit,
                                   self.energy_coefficient)

    def set_time_for_historical_data_3(self,
                                       timestamp: datetime.datetime,
                                       num_of_data_points: int = 10,
                                       ) -> None:
        edt = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_3(timestamp,
                                                                               num_of_data_points)
        req = EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_3, edt)
        self._request_to_set([req])

    def get_time_for_historical_data_3(self) -> dict[str, datetime.datetime | None | int]:
        req = EchonetProperty(EchonetPropertyCode.time_for_historical_data_3)
        res = self._request_to_get([req])[0]
        return self._parse_or_wrap(req.epc, res.edt,
                                   EchonetDataParser.parse_time_for_historical_data_3)

    class DayForHistoricalData1(TypedDict, total=False):
        day: int

    class TimeForHistoricalData2(TypedDict, total=False):
        timestamp: datetime.datetime
        num_of_data_points: int

    class TimeForHistoricalData3(TypedDict, total=False):
        timestamp: datetime.datetime
        num_of_data_points: int

    def request_to_set(self,
                       day_for_historical_data_1: DayForHistoricalData1 | None = None,
                       time_for_historical_data_2: TimeForHistoricalData2 | None = None,
                       time_for_historical_data_3: TimeForHistoricalData3 | None = None) -> None:
        properties_with_data = []
        if day_for_historical_data_1 is None and time_for_historical_data_2 is None and time_for_historical_data_3 is None:
            return
        if day_for_historical_data_1 is not None:
            edt = EchonetDataBuilder.build_edata_to_set_day_for_historical_data_1(**day_for_historical_data_1)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.day_for_historical_data_1, edt))
        if time_for_historical_data_2 is not None:
            edt = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_2(**time_for_historical_data_2)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_2, edt))
        if time_for_historical_data_3 is not None:
            edt = EchonetDataBuilder.build_edata_to_set_time_for_historical_data_3(**time_for_historical_data_3)
            properties_with_data.append(EchonetPropertyWithData(EchonetPropertyCode.time_for_historical_data_3, edt))

        self._request_to_set(properties_with_data)

    def request_to_get(self,
                       properties: set[EchonetPropertyCode]) -> dict[EchonetPropertyCode, Any]:
        results = self._request_to_get([EchonetProperty(epc) for epc in properties])
        parsed_results = {}
        for r in results:
            try:
                parser = PARSER_MAP[r.epc]
            except KeyError:
                raise MomongaRuntimeError('No parser found for EPC: %X' % r.epc)

            if parser in ENERGY_PARSERS:
                parsed_results[r.epc] = self._parse_or_wrap(
                    r.epc, r.edt, parser, self.energy_unit, self.energy_coefficient)
            else:
                parsed_results[r.epc] = self._parse_or_wrap(r.epc, r.edt, parser)

        return parsed_results
