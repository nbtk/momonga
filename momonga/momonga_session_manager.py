import logging
import threading
import queue
import time

from collections.abc import Callable
from typing import Self

from .momonga_exception import (MomongaSkScanFailure,
                                MomongaSkJoinFailure,
                                MomongaNeedToReopen,
                                MomongaSkCommandExecutionFailure,
)
from .momonga_response import SkEventNum, SkParsedEvent, SkParsedRxUdp, parse_sk_line
from .momonga_sk_wrapper import MomongaSkWrapper
from .momonga_sk_wrapper import logger as sk_wrapper_logger

logger = logging.getLogger(__name__)


class MomongaSessionManager:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                ) -> None:
        self.dev = dev
        self.baudrate = baudrate
        self.rbid = rbid
        self.pwd = pwd
        self.reset_dev = reset_dev

        # the following value will be set a pyserial object.
        self.skw = MomongaSkWrapper(dev, baudrate)

        # the following values will be set by open() with skscan().
        self.smart_meter_mac = None
        self.smart_meter_addr = None
        self.channel = None
        self.pan_id = None

        # the following values will be set by open() with skjoin().
        self.session_established = False
        self.receiver_th = None
        self.receiver_exception = None
        self.gate_lock = threading.Lock()
        self.session_available = True
        self.rate_ok = True
        self.xmit_allowed = threading.Event()
        self.xmit_allowed.set()
        self.rejoin_lock = threading.Lock()

        self.on_meter_frame: Callable[[SkParsedRxUdp], None] | None = None

        self.pkt_sbsc_q = queue.Queue()
        self.recv_q = queue.Queue()
        self.notif_q = queue.Queue()
        self.xmit_q = queue.Queue()

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, type, value, traceback) -> None:
        self.close()

    def open(self) -> Self:
        logger.info('Opening a Momonga session...')
        try:
            self.skw.open()

            if self.reset_dev is True:
                # to reset the specified wi-sun module.
                self.skw.skreset()

            # to disable echoback.
            # self.sksreg('SFE', '0')

            # to show the rssi of the received packets.
            self.skw.sksreg('SA2', '1')

            # scanning a PAN from here.
            # to set a route b id.
            self.skw.sksetrbid(self.rbid)
            # to set a password.
            self.skw.sksetpwd(self.pwd)
            logger.info('The Route-B ID and the password were registered.')
            try:
                logger.info('Scanning PAN channels...')
                scan_res = self.skw.skscan()
                logger.info('A PAN was found.')
            except MomongaSkScanFailure as e:
                logger.error('Gave up to find a PAN. Check the device location and Route-B ID. Then try again.')
                raise MomongaSkScanFailure('Gave up to find a PAN. Check the device location and Route-B ID. Then try again.') from e
            self.smart_meter_mac = scan_res.mac_addr
            self.channel = scan_res.channel
            self.pan_id = scan_res.pan_id
            # converting mac addr to ip6 addr.
            self.smart_meter_addr = self.skw.skll64(scan_res.mac_addr).ip6_addr

            # joining a PAN from here.
            logger.info('Joining the PAN...')
            # to set a channel.
            self.skw.sksreg('S2', self.channel)
            # to set a pan id.
            self.skw.sksreg('S3', self.pan_id)
            # to establish a pana session.
            try:
                self.skw.skjoin(self.smart_meter_addr)
                self.session_established = True
                logger.info('A PANA session has been established.')
            except MomongaSkJoinFailure as e:
                logger.error('Gave up to establish a PANA session. Check the Route-B ID and password. Then try again.')
                raise MomongaSkJoinFailure('Gave up to establish a PANA session. Check the Route-B ID and password. Then try again.') from e

            while not self.pkt_sbsc_q.empty():
                self.pkt_sbsc_q.get()
            while not self.recv_q.empty():
                self.recv_q.get()
            while not self.notif_q.empty():
                self.notif_q.get()
            while not self.xmit_q.empty():
                self.xmit_q.get()

            self.receiver_th = threading.Thread(target=self.receiver, daemon=True)
            self.skw.subscribers.update({'pkt_sbsc_q': self.pkt_sbsc_q})
            self.receiver_th.start()

            logger.info('A Momonga session is open.')
            return self
        except Exception as e:
            logger.error('Could not open a Momonga session. %s: %s' % (type(e).__name__, e))
            self.close()
            raise

    def close(self) -> None:
        logger.info('Closing the Momonga session...')

        rejoin_lock_acquired = self.rejoin_lock.acquire(timeout=120)
        if not rejoin_lock_acquired:
            logger.warning('Failed to acquire "rejoin_lock".')

        if self.session_established:
            try:
                self.session_established = False
                logger.info('Terminating the PANA session...')
                self.skw.skterm()
            except Exception as e:
                logger.warning('Failed to terminate the PANA session. %s: %s' % (type(e).__name__, e))
            finally:
                if rejoin_lock_acquired:
                    self.rejoin_lock.release()
        else:
            if rejoin_lock_acquired:
                self.rejoin_lock.release()

        if self.receiver_th is not None:
            if self.receiver_th.is_alive():
                self.pkt_sbsc_q.put('__CLOSE__')  # to close the receiver thread.
                self.receiver_th.join()
            self.receiver_th = None

        if self.skw.subscribers.get('pkt_sbsc_q') is not None:
            self.skw.subscribers.pop('pkt_sbsc_q')

        self.force_open_gates()

        if self.rejoin_lock.locked():
            logger.error('"rejoin_lock" is unexpectedly locked.')

        self.skw.close()
        logger.info('The Momonga session is closed.')

    def receiver(self) -> None:
        logger.debug('A packet receiver has been started.')
        try:
            while True:
                raw = self.pkt_sbsc_q.get()
                if raw == '__CLOSE__':
                    break

                parsed = parse_sk_line(raw, self.skw.device_strategy)

                if isinstance(parsed, SkParsedEvent):
                    num = parsed.num
                    if num == SkEventNum.session_lifetime:
                        logger.debug('The PANA session lifetime has been expired.')
                        self.close_session_gate()
                    elif num == SkEventNum.rejoin_failed:
                        logger.warning('Could not rejoin the PAN.')
                        self.rejoin_lock.acquire()
                        if self.session_established:
                            self.session_established = False
                            try:
                                self.skw.skjoin(self.smart_meter_addr)
                            except MomongaSkJoinFailure as e:
                                logger.error('%s Close Momonga and open it again.' % (e))
                                raise MomongaNeedToReopen('%s Close Momonga and open it again.' % (e))
                            finally:
                                self.rejoin_lock.release()
                        else:
                            self.rejoin_lock.release()
                    elif num == SkEventNum.rejoined:
                        logger.debug('Successfully rejoined the PAN.')
                        self.session_established = True
                        self.open_session_gate()
                    elif num == SkEventNum.rate_limit_exceeded:
                        logger.warning('The transmission rate limit has been exceeded.')
                        self.close_rate_gate()
                    elif num == SkEventNum.rate_limit_released:
                        logger.debug('The transmission rate limit has been released.')
                        self.open_rate_gate()
                    elif num == SkEventNum.session_closed:
                        self.close_session_gate()
                        logger.debug('The PANA session has been closed successfully.')
                    elif num == SkEventNum.no_session:
                        self.close_session_gate()
                        logger.warning('There was no PANA session to close.')
                    elif num in (SkEventNum.tx_done, SkEventNum.neighbor_discovery):
                        if not self.is_restricted_to_xmit():
                            self.recv_q.put(parsed)

                elif isinstance(parsed, SkParsedRxUdp):
                    if parsed.src_addr == self.smart_meter_addr and self.on_meter_frame is not None:
                        # A slow callback delays all subsequent EVENT processing (e.g. EVENT 32/33).
                        try:
                            self.on_meter_frame(parsed)
                        except Exception as e:
                            logger.error('on_meter_frame raised an exception. %s: %s' % (type(e).__name__, e))

        except Exception as e:
            logger.error('An exception was raised from the receiver thread. %s: %s' % (type(e).__name__, e))
            self.receiver_exception = e

        logger.debug('The packet receiver has been stopped.')

    # Design note: the transmission gate is an optimization, not a correctness guarantee.
    # There is an intentional check-then-act race window between xmit_allowed.wait() and
    # sksendto(): the gate may close (e.g. EVENT 29 arrives) after the check but before
    # the send.  Plugging this window with a send-hold lock is not feasible — PANA session
    # state and rate limiting live in the SK module firmware and cannot be controlled
    # atomically from Python.  Correctness is instead guaranteed by EVENT 21 result
    # handling and the retry loop in __request_locked(): a failed or timed-out send is
    # simply retried.  The gate's value is reducing unnecessary sends during known-bad
    # states, not providing atomicity.
    def xmitter(self,
                data: bytes,
               ) -> None:
        retry_to_xmit = 3
        retry_to_wait_xmit_allowed = 60
        xmitted = False
        for _ in range(retry_to_xmit):
            logger.debug('Waiting for transmission gate to open.')
            allowed = False
            for r in range(retry_to_wait_xmit_allowed):
                allowed = self.xmit_allowed.wait(timeout=60)
                if not allowed:
                    logger.warning('Transmission gate is still closed. (%d/%d)' % (r + 1, retry_to_wait_xmit_allowed))
                    if self.receiver_exception is not None:
                        logger.error('Got an exception from the receiver thread. %s: %s' % (type(self.receiver_exception).__name__, self.receiver_exception))
                        raise MomongaNeedToReopen('Got an exception from the receiver thread. %s: %s' % (type(self.receiver_exception).__name__, self.receiver_exception))
                else:
                    break

            if not allowed:
                logger.error('Transmission rights could not be acquired. Close Momonga and open it again.')
                raise MomongaNeedToReopen('Transmission rights could not be acquired. Close Momonga and open it again.')
            else:
                logger.debug('Transmission gate is open.')

            try:
                if not self.session_established:
                    logger.error('Tried to transmit a packet, but no PANA session was established.')
                    raise MomongaNeedToReopen('No PANA session established. Close Momonga and open it again.')
                self.skw.sksendto(self.smart_meter_addr, data)
                xmitted = True
                break
            except MomongaSkCommandExecutionFailure as e:
                logger.warning('Failed to transmit a packet: %s' % (e))
            except MomongaNeedToReopen:
                raise
            except Exception as e:
                logger.warning('An error occurred to transmit a packet. %s: %s' % (type(e).__name__, e))
            time.sleep(3)
        if not xmitted:
            logger.error('Could not transmit a packet. Close Momonga and open it again.')
            raise MomongaNeedToReopen('Could not transmit a packet. Close Momonga and open it again.')

    def close_session_gate(self) -> None:
        with self.gate_lock:
            self.session_available = False
            self.xmit_allowed.clear()
        logger.debug('Session gate closed.')

    def open_session_gate(self) -> None:
        with self.gate_lock:
            self.session_available = True
            if self.rate_ok:
                self.xmit_allowed.set()
                logger.debug('Both gates open; transmission allowed.')
            else:
                logger.debug('Session gate opened but rate gate still closed.')

    def close_rate_gate(self) -> None:
        with self.gate_lock:
            self.rate_ok = False
            self.xmit_allowed.clear()
        logger.debug('Rate gate closed.')

    def open_rate_gate(self) -> None:
        with self.gate_lock:
            self.rate_ok = True
            if self.session_available:
                self.xmit_allowed.set()
                logger.debug('Both gates open; transmission allowed.')
            else:
                logger.debug('Rate gate opened but session gate still closed.')

    def force_open_gates(self) -> None:
        with self.gate_lock:
            self.session_available = True
            self.rate_ok = True
            self.xmit_allowed.set()
        logger.debug('All gates forcibly opened.')

    def is_restricted_to_xmit(self) -> bool:
        return not self.xmit_allowed.is_set()
