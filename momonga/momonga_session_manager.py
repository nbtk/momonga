import logging
import threading
import queue
import time

from collections.abc import Callable
from typing import Self

from .momonga_exception import (MomongaNeedToReopen,
                                       MomongaSkCommandCancelled,
                                       MomongaSkCommandExecutionFailure,
                                       MomongaSkJoinFailure,
                                       MomongaSkScanFailure,
                                       MomongaValueError,
                                       MomongaXmitTimeout)
from .momonga_response import SkEventNum, SkParsedEvent, SkParsedRxUdp, parse_sk_line
from .momonga_sk_wrapper import MomongaSkWrapper, PUBLISHER_STOPPED

logger = logging.getLogger(__name__)


class _SessionEnded:
    def __repr__(self) -> str:
        return 'SESSION_ENDED'


SESSION_ENDED = _SessionEnded()


class _StopReceiver:
    def __repr__(self) -> str:
        return '_STOP_RECEIVER'


_STOP_RECEIVER = _StopReceiver()

_REJOIN_LOCK_LIMIT = 120

# the module's own estimate for one SKJOIN is 40 s, so this is that with room
# to spare, and the budget is this per attempt the caller asked for. close()
# no longer waits a rejoin out - it says it is closing and the rejoin gives up
# - so the budget only has to be long enough to succeed.
_REJOIN_ATTEMPT_LIMIT = 60

_SKTERM_LIMIT = 30

_RECEIVER_JOIN_LIMIT = 30


def _capped_wait(deadline: float | None, cap: int | float) -> int | float:
    if deadline is None:
        return cap
    return min(cap, max(0.0, deadline - time.monotonic()))


def _deadline_passed(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


class MomongaSessionManager:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                 scan_retries: int = 3,
                 join_retries: int = 3,
                ) -> None:
        self.dev = dev
        self.baudrate = baudrate
        self._rbid = rbid
        self._pwd = pwd
        self._reset_dev = reset_dev
        # Zero means never attempting to connect and reporting that the
        # connection failed, which reads as a meter that is not there.
        for name, value in (('scan_retries', scan_retries),
                            ('join_retries', join_retries)):
            if value < 1:
                raise MomongaValueError('%s must be 1 or more, not %s' % (name, value))
        self._scan_retries = scan_retries
        self._join_retries = join_retries

        # the following value will be set a pyserial object.
        self.skw = MomongaSkWrapper(dev, baudrate)

        # the following values will be set by open() with skscan().
        self._smart_meter_mac = None
        self.smart_meter_addr = None
        self.channel = None
        self.pan_id = None

        # the following values will be set by open() with skjoin().
        self.session_established = False
        self._receiver_th = None
        self.receiver_exception = None
        self._gate_lock = threading.Lock()
        self._session_available = True
        self._rate_ok = True
        self._xmit_allowed = threading.Event()
        self._xmit_allowed.set()
        self._rejoin_lock = threading.Lock()
        self._closing = False

        self.on_meter_frame: Callable[[SkParsedRxUdp], None] | None = None

        self._pkt_sbsc_q = queue.Queue()
        self.recv_q = queue.Queue()
        self.notif_q = queue.Queue()

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, type, value, traceback) -> None:
        self.close()

    def open(self) -> Self:
        logger.info('Opening a Momonga session...')
        self._closing = False
        try:
            self.skw.open()

            if self._reset_dev is True:
                # to reset the specified wi-sun module.
                self.skw.skreset()

            # to show the rssi of the received packets.
            self.skw.sksreg('SA2', '1')

            # scanning a PAN from here.
            # to set a route b id.
            self.skw.sksetrbid(self._rbid)
            # to set a password.
            self.skw.sksetpwd(self._pwd)
            logger.info('The Route-B ID and the password were registered.')
            try:
                logger.info('Scanning PAN channels...')
                scan_res = self.skw.skscan(retry=self._scan_retries)
                logger.info('A PAN was found.')
            except MomongaSkScanFailure as e:
                logger.error('Gave up to find a PAN. Check the device location and Route-B ID. Then try again.')
                raise MomongaSkScanFailure('Gave up to find a PAN. Check the device location and Route-B ID. Then try again.') from e
            self._smart_meter_mac = scan_res.mac_addr
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
                self.skw.skjoin(self.smart_meter_addr, retry=self._join_retries)
                self.session_established = True
                logger.info('A PANA session has been established.')
            except MomongaSkJoinFailure as e:
                logger.error('Gave up to establish a PANA session. Check the Route-B ID and password. Then try again.')
                raise MomongaSkJoinFailure('Gave up to establish a PANA session. Check the Route-B ID and password. Then try again.') from e

            self._pkt_sbsc_q = queue.Queue()
            while not self.recv_q.empty():
                self.recv_q.get()
            while not self.notif_q.empty():
                self.notif_q.get()

            self.receiver_exception = None
            self._receiver_th = threading.Thread(target=self._receiver, daemon=True)
            self.skw.subscribers.update({'pkt_sbsc_q': self._pkt_sbsc_q})
            self._receiver_th.start()

            logger.info('A Momonga session is open.')
            return self
        except Exception as e:
            logger.error('Could not open a Momonga session. %s: %s' % (type(e).__name__, e))
            self.close()
            raise

    def close(self) -> None:
        logger.info('Closing the Momonga session...')
        self._closing = True

        rejoin_lock_acquired = self._rejoin_lock.acquire(timeout=_REJOIN_LOCK_LIMIT)
        if not rejoin_lock_acquired:
            logger.warning('Failed to acquire "_rejoin_lock".')

        if self.session_established:
            try:
                self.session_established = False
                logger.info('Terminating the PANA session...')
                self.skw.skterm(deadline=time.monotonic() + _SKTERM_LIMIT)
            except Exception as e:
                logger.warning('Failed to terminate the PANA session. %s: %s' % (type(e).__name__, e))
            finally:
                if rejoin_lock_acquired:
                    self._rejoin_lock.release()
        else:
            if rejoin_lock_acquired:
                self._rejoin_lock.release()

        self.skw.cancel_commands()

        if self._receiver_th is not None:
            if self._receiver_th.is_alive():
                self._pkt_sbsc_q.put(_STOP_RECEIVER)  # to close the receiver thread.
                self._receiver_th.join(timeout=_RECEIVER_JOIN_LIMIT)
                if self._receiver_th.is_alive():
                    logger.warning('The packet receiver is still running. It is inside'
                                   ' on_meter_frame and will stop when that returns.')
            self._receiver_th = None

        if self.skw.subscribers.get('pkt_sbsc_q') is not None:
            self.skw.subscribers.pop('pkt_sbsc_q')

        self._force_open_gates()
        self.notif_q.put(SESSION_ENDED)

        if rejoin_lock_acquired and self._rejoin_lock.locked():
            logger.error('"_rejoin_lock" is unexpectedly locked.')

        self.skw.close()
        logger.info('The Momonga session is closed.')

    def _receiver(self) -> None:
        logger.debug('A packet receiver has been started.')
        pkt_sbsc_q = self._pkt_sbsc_q
        try:
            while True:
                raw = pkt_sbsc_q.get()
                if raw is _STOP_RECEIVER:
                    break
                if raw is PUBLISHER_STOPPED:
                    raise MomongaNeedToReopen('The packet publisher has stopped.'
                                              ' Close Momonga and open it again.')

                parsed = parse_sk_line(raw, self.skw.device_strategy)

                if isinstance(parsed, SkParsedEvent):
                    num = parsed.num
                    if num == SkEventNum.session_lifetime:
                        logger.debug('The PANA session lifetime has been expired.')
                        self._close_session_gate()
                    elif num == SkEventNum.rejoin_failed:
                        logger.warning('Could not rejoin the PAN.')
                        if not self._rejoin_lock.acquire(timeout=_REJOIN_LOCK_LIMIT):
                            logger.warning('Gave up rejoining the PAN. The session is being closed.')
                            continue
                        try:
                            if self.session_established:
                                self.session_established = False
                                try:
                                    self.skw.skjoin(
                                        self.smart_meter_addr,
                                        retry=self._join_retries,
                                        deadline=time.monotonic()
                                        + self._join_retries * _REJOIN_ATTEMPT_LIMIT,
                                        should_stop=lambda: self._closing)
                                except MomongaSkCommandCancelled:
                                    logger.debug('The session is being closed;'
                                                 ' giving up the rejoin.')
                                except MomongaSkJoinFailure as e:
                                    logger.error('%s Close Momonga and open it again.' % (e))
                                    raise MomongaNeedToReopen('%s Close Momonga and open it again.' % (e))
                        finally:
                            self._rejoin_lock.release()
                    elif num == SkEventNum.rejoined:
                        logger.debug('Successfully rejoined the PAN.')
                        if not self._rejoin_lock.acquire(timeout=_REJOIN_LOCK_LIMIT):
                            logger.warning('Could not acquire "_rejoin_lock"'
                                           ' to mark the session as established.')
                            continue
                        try:
                            if self._closing:
                                logger.debug('The session is being closed;'
                                             ' ignoring the rejoined event.')
                                continue
                            self.session_established = True
                        finally:
                            self._rejoin_lock.release()
                        self._open_session_gate()
                    elif num == SkEventNum.rate_limit_exceeded:
                        logger.warning('The transmission rate limit has been exceeded.')
                        self._close_rate_gate()
                    elif num == SkEventNum.rate_limit_released:
                        logger.debug('The transmission rate limit has been released.')
                        self._open_rate_gate()
                    elif num == SkEventNum.session_closed:
                        self._close_session_gate()
                        logger.debug('The PANA session has been closed successfully.')
                    elif num == SkEventNum.no_session:
                        self._close_session_gate()
                        logger.warning('There was no PANA session to close.')
                    elif num in (SkEventNum.tx_done, SkEventNum.neighbor_discovery):
                        if not self._is_restricted_to_xmit():
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
            self.notif_q.put(SESSION_ENDED)

        logger.debug('The packet receiver has been stopped.')

    def raise_if_receiver_died(self) -> None:
        if self.receiver_exception is not None:
            logger.error('Got an exception from the receiver thread. %s: %s'
                         % (type(self.receiver_exception).__name__, self.receiver_exception))
            raise MomongaNeedToReopen('Got an exception from the receiver thread. %s: %s'
                                      % (type(self.receiver_exception).__name__,
                                         self.receiver_exception)) from self.receiver_exception

    # Design note: the transmission gate is an optimization, not a correctness guarantee.
    # There is an intentional check-then-act race window between _xmit_allowed.wait() and
    # sksendto(): the gate may close (e.g. EVENT 29 arrives) after the check but before
    # the send.  Plugging this window with a send-hold lock is not feasible — PANA session
    # state and rate limiting live in the SK module firmware and cannot be controlled
    # atomically from Python.  Correctness is instead guaranteed by EVENT 21 result
    # handling and the retry loop in _request_locked(): a failed or timed-out send is
    # simply retried.  The gate's value is reducing unnecessary sends during known-bad
    # states, not providing atomicity.
    def xmitter(self,
                data: bytes,
                timeout: int | float | None = None,
               ) -> None:
        xmit_retry_limit      = 3
        gate_wait_retry_limit = 60
        gate_wait_timeout     = 60
        xmit_retry_interval   = 3
        xmitted = False
        deadline = None if timeout is None else time.monotonic() + timeout
        self.raise_if_receiver_died()
        for _ in range(xmit_retry_limit):
            logger.debug('Waiting for transmission gate to open.')
            allowed = False
            for r in range(gate_wait_retry_limit):
                allowed = self._xmit_allowed.wait(timeout=_capped_wait(deadline, gate_wait_timeout))
                if allowed:
                    break
                self.raise_if_receiver_died()
                if _deadline_passed(deadline):
                    logger.debug('The transmission gate did not open within the given time.')
                    raise MomongaXmitTimeout('The transmission gate did not open within %s seconds.' % (timeout))
                logger.warning('Transmission gate is still closed. (%d/%d)' % (r + 1, gate_wait_retry_limit))

            if not allowed:
                logger.error('Transmission rights could not be acquired. Close Momonga and open it again.')
                raise MomongaNeedToReopen('Transmission rights could not be acquired. Close Momonga and open it again.')
            else:
                logger.debug('Transmission gate is open.')

            try:
                if not self.session_established:
                    logger.error('Tried to transmit a packet, but no PANA session was established.')
                    raise MomongaNeedToReopen('No PANA session established. Close Momonga and open it again.')
                if deadline is None:
                    self.skw.sksendto(self.smart_meter_addr, data)
                else:
                    self.skw.sksendto(self.smart_meter_addr, data, deadline=deadline)
                xmitted = True
                break
            except MomongaSkCommandExecutionFailure as e:
                logger.warning('Failed to transmit a packet: %s' % (e))
            except MomongaSkCommandCancelled:
                raise
            except MomongaNeedToReopen:
                if _deadline_passed(deadline):
                    logger.debug('Could not transmit a packet within the given time.')
                    raise MomongaXmitTimeout('Could not transmit a packet within %s seconds.' % (timeout))
                raise
            except Exception as e:
                logger.warning('An error occurred to transmit a packet. %s: %s' % (type(e).__name__, e))
            if _deadline_passed(deadline):
                logger.debug('Could not transmit a packet within the given time.')
                raise MomongaXmitTimeout('Could not transmit a packet within %s seconds.' % (timeout))
            time.sleep(_capped_wait(deadline, xmit_retry_interval))
        if not xmitted:
            logger.error('Could not transmit a packet. Close Momonga and open it again.')
            raise MomongaNeedToReopen('Could not transmit a packet. Close Momonga and open it again.')

    def _close_session_gate(self) -> None:
        with self._gate_lock:
            self._session_available = False
            self._xmit_allowed.clear()
        logger.debug('Session gate closed.')

    def _open_session_gate(self) -> None:
        with self._gate_lock:
            self._session_available = True
            if self._rate_ok:
                self._xmit_allowed.set()
                logger.debug('Both gates open; transmission allowed.')
            else:
                logger.debug('Session gate opened but rate gate still closed.')

    def _close_rate_gate(self) -> None:
        with self._gate_lock:
            self._rate_ok = False
            self._xmit_allowed.clear()
        logger.debug('Rate gate closed.')

    def _open_rate_gate(self) -> None:
        with self._gate_lock:
            self._rate_ok = True
            if self._session_available:
                self._xmit_allowed.set()
                logger.debug('Both gates open; transmission allowed.')
            else:
                logger.debug('Rate gate opened but session gate still closed.')

    def _force_open_gates(self) -> None:
        with self._gate_lock:
            self._session_available = True
            self._rate_ok = True
            self._xmit_allowed.set()
        logger.debug('All gates forcibly opened.')

    def _is_restricted_to_xmit(self) -> bool:
        return not self._xmit_allowed.is_set()
