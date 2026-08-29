import builtins
import logging
import threading
import queue
import serial
import time

from collections.abc import Callable
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any, Literal, Self

from .momonga_exception import (MomongaError,
                                MomongaIOError,
                                MomongaNeedToReopen,
                                MomongaRuntimeError,
                                MomongaSkCommandBusy,
                                MomongaSkCommandCancelled,
                                MomongaSkCommandDeadlineExceeded,
                                MomongaSkCommandFailedToExecute,
                                MomongaSkCommandInvalidArgument,
                                MomongaSkCommandInvalidSyntax,
                                MomongaSkCommandSerialInputError,
                                MomongaSkCommandUnknownError,
                                MomongaSkCommandUnsupported,
                                MomongaSkJoinFailure,
                                MomongaSkResponseNotExpected,
                                MomongaSkScanFailure,
                                MomongaTimeoutError,
                                MomongaValueError)
from .momonga_device import (BP35A1Strategy,
                             BP35C2Strategy,
                             DeviceStrategy,
                             DeviceType)
from .momonga_response import (SkVerResponse,
                               SkAppVerResponse,
                               SkInfoResponse,
                               SkScanResponse,
                               SkLl64Response)
from .momonga_echonet_enum import ECHONET_LITE_PORT

logger = logging.getLogger(__name__)


class _PublisherStopped:
    def __repr__(self) -> str:
        return 'PUBLISHER_STOPPED'


PUBLISHER_STOPPED = _PublisherStopped()


class _CommandsCancelled:
    def __repr__(self) -> str:
        return '_COMMANDS_CANCELLED'


_COMMANDS_CANCELLED = _CommandsCancelled()

# BP35A1 returns this value for the SKINFO side field (not a real side index)
_BP35A1_SIDE_SENTINEL = 0xFFFE

_SK_COMMAND_LIMIT = 300

# how often a caller that can be told to stop gets to look
_STOP_CHECK_POLL = 0.5

_PUBLISHER_JOIN_LIMIT = 5

_BUF_CLEAR_LIMIT = 10

# SKSCAN's window doubles with each step of DURATION, so widening it without
# a stop turns a handful of retries into hours. Three steps is what the
# estimates below were written for; past that a retry repeats the widest
# window rather than doubling it again.
_SCAN_FIRST_DURATION = 6
_SCAN_WIDEST_DURATION = 8

_SECRET_COMMANDS = ('SKSETPWD', 'SKSETRBID')



def _port(what: str) -> AbstractContextManager[None]:
    """Turn a failure of the serial device itself into a MomongaError.

    pyserial raises SerialException, and the OS raises FileNotFoundError or
    PermissionError, for a port that has gone or was never there. None of
    those is something a caller of this library was told to expect, so they
    become MomongaIOError - which is an OSError too, so code already catching
    that keeps working, and the recovery loop in _request_with_recovery still
    sees it.
    """
    class _Wrap:
        def __enter__(self) -> None:
            return None

        def __exit__(self,
                     exc_type: type[BaseException] | None,
                     exc: BaseException | None,
                     tb: TracebackType | None) -> Literal[False]:
            if exc is None or isinstance(exc, MomongaError):
                return False
            if isinstance(exc, OSError):
                raise MomongaIOError('%s: %s: %s'
                                     % (what, type(exc).__name__, exc)) from exc
            return False
    return _Wrap()


def _mask_secrets(text: str) -> str:
    for name in _SECRET_COMMANDS:
        index = text.find(name)
        if index >= 0:
            return text[:index + len(name)] + ' ****'
    return text


class MomongaSkWrapper:
    def __init__(self,
                 dev: str,
                 baudrate: int = 115200,
                 ) -> None:
        self.dev = dev
        self.baudrate = baudrate

        # the following value will be set a pyserial object.
        self._ser: serial.Serial | None = None
        self._publisher_th_breaker = False
        self._publisher_th: threading.Thread | None = None
        self.publisher_exception: Exception | None = None
        self.subscribers: dict[str, queue.Queue[Any]] = {'cmd_exec_q': queue.Queue()}
        self.device_strategy: DeviceStrategy = BP35C2Strategy()
        self._cmd_lock = threading.Lock()
        self._cancelled = False

    @property
    def device_type(self) -> DeviceType:
        return self.device_strategy.device_type

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self,
                 type: builtins.type[BaseException] | None,
                 value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()

    @property
    def _port_or_raise(self) -> serial.Serial:
        if self._ser is None:
            raise MomongaRuntimeError('The Wi-SUN module is not open.')
        return self._ser

    def open(self) -> Self:
        with _port('could not open %s' % self.dev):
            self._ser = serial.Serial(self.dev, self.baudrate, timeout=_SK_COMMAND_LIMIT)

        try:
            try:
                # to drop garbage data in the buffer.
                self._clear_buf()

                # to check udp payloads returned from the wi-sun module are in ascii format.
                if self._exec_ropt() != 1:
                    logger.warning("Executing the 'WOPT 01\\r' command to make the Wi-SUN module return UDP payloads "
                                    "in ASCII format. Note: the WOPT command can only be executed a limited number of times. "
                                    "This configuration is saved in the Wi-SUN module, so this log message should "
                                    "no longer appear.")
                    self._exec_wopt(1)  # to make the wi-sun module return udp payloads in ascii format.
            except MomongaSkCommandUnsupported:
                logger.info('The ROPT command is unsupported on this hardware. Assuming ASCII output mode.')

            for q in list(self.subscribers.values()):
                while not q.empty():
                    q.get()

            self._publisher_th_breaker = False  # set True when you want to stop the publisher.
            self.publisher_exception = None
            self._cancelled = False
            self._publisher_th = threading.Thread(target=self.received_packet_publisher, daemon=True)
            self._publisher_th.start()

            self.detect_device()
        except Exception:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self._publisher_th is not None:
            self._publisher_th_breaker = True
            self._publisher_th.join(timeout=_PUBLISHER_JOIN_LIMIT)
            if self._publisher_th.is_alive():
                logger.warning('The received packet publisher is still running. It is stuck'
                               ' reading the serial port and will stop when that read returns.')
            self._publisher_th = None
        if self._ser is not None and not self._ser.closed:
            with _port('could not close %s' % self.dev):
                self._ser.close()

    def _clear_buf(self) -> None:  # do not call this after open().
        with _port('could not clear the buffer'):
            self._port_or_raise.write(b'\r\n')
            self._port_or_raise.flush()
            timeout = self._port_or_raise.timeout
            self._port_or_raise.timeout = 2  # will wait the specified seconds.
            deadline = time.monotonic() + _BUF_CLEAR_LIMIT
            while self._port_or_raise.read():
                # this loop clears garbage data if it exists.
                if time.monotonic() >= deadline:
                    logger.warning('Gave up clearing the buffer. The Wi-SUN module keeps sending data.')
                    break
            # to undo the timeout.
            self._port_or_raise.timeout = timeout

    def _exec_ropt(self) -> int:  # do not call this after open().
        with _port('could not send ROPT'):
            self._port_or_raise.write(b'ROPT\r')
            self._port_or_raise.flush()
        res = b''
        ok = b'OK '
        fail = b'FAIL'
        deadline = time.monotonic() + _SK_COMMAND_LIMIT
        while True:
            with _port('could not read from %s' % self.dev):
                b = self._port_or_raise.read()
            if not b or time.monotonic() >= deadline:
                raise MomongaTimeoutError('The ROPT command timed out.')
            res += b
            if ok in res and res.endswith(b'\r'):
                break
            elif fail in res and res.endswith(b'\r'):
                decoded = res.decode(errors='replace')
                for line in decoded.splitlines():
                    if line.startswith('FAIL'):
                        self._raise_fail_response('ROPT', line)
                raise MomongaSkCommandUnknownError('Unexpected ROPT response: %s' % decoded)
        return int(res[res.index(ok) + len(ok):-1].decode())

    def _exec_wopt(self,
                   opt: int,
                   ) -> None:  # do not call this after open().
        supported_opts = (0,  # binary mode
                          1,  # hex ascii mode
                          )
        if opt not in supported_opts:
            raise MomongaValueError('The WOPT command does not support the given option: %02d' % opt)

        with _port('could not send WOPT'):
            self._port_or_raise.write(('WOPT %02d\r' % opt).encode())
            self._port_or_raise.flush()
        res = b''
        deadline = time.monotonic() + _SK_COMMAND_LIMIT
        while True:
            with _port('could not read from %s' % self.dev):
                b = self._port_or_raise.read()
            if not b or time.monotonic() >= deadline:
                raise MomongaTimeoutError('The WOPT command timed out.')
            res += b
            if b'OK\r' in res:
                break
        return

    def _readline(self,
                  timeout: int | None = None,
                  ) -> str:
        with _port('could not read from %s' % self.dev):
            org_timeout = self._port_or_raise.timeout
            self._port_or_raise.timeout = timeout
            data_bytes = self._port_or_raise.readline()
            self._port_or_raise.timeout = org_timeout
        if data_bytes != b'':
            logger.debug('<<< %s', _mask_secrets(str(data_bytes)))
        line = data_bytes.decode(errors='replace').split('\r\n')[0]
        return line

    def received_packet_publisher(self) -> None:
        logger.debug('A received packet publisher has been started.')
        my_ser = self._ser  # open() swaps this in, which is how a stale publisher knows
        try:
            while True:
                if self._publisher_th_breaker or self._ser is not my_ser:
                    break
                line = self._readline(timeout=1)
                if line == '':
                    continue
                for q in list(self.subscribers.values()):
                    q.put(line)  # will dispatch the line to each subscriber
        except Exception as e:
            logger.error('An exception was raised from the publisher thread. %s: %s', type(e).__name__, e)
            if self._ser is my_ser:
                self.publisher_exception = e
                for q in list(self.subscribers.values()):
                    q.put(PUBLISHER_STOPPED)
            else:
                logger.debug('The exception came from a publisher that no longer owns the port. Ignoring.')

        logger.debug('The received packet publisher has been stopped.')

    def _writeline(self,
                   line: str,
                   payload: bytes | None = None,
                   ) -> None:
        if payload is not None:
            data_bytes = (line + ' ').encode() + payload
        else:
            data_bytes = (line + '\r\n').encode()
        with _port('could not write to %s' % self.dev):
            self._port_or_raise.write(data_bytes)
            logger.debug('>>> %s', _mask_secrets(str(data_bytes)))
            self._port_or_raise.flush()

    def cancel_commands(self) -> None:
        running = self._cmd_lock.locked()
        self._cancelled = True
        self.subscribers['cmd_exec_q'].put(_COMMANDS_CANCELLED)
        if running:
            logger.warning('SK command execution has been cancelled.')
        else:
            logger.debug('SK command execution has been cancelled. Nothing was running.')

    def exec_command(self,
                     command: list[str],
                     wait_until: str | list[str] = 'OK',
                     timeout: int | float | None = _SK_COMMAND_LIMIT,
                     payload: bytes | None = None,
                     lock_timeout: int | float = -1,
                     deadline: float | None = None,
                     should_stop: Callable[[], bool] | None = None,
                     ) -> list[str]:
        masked = _mask_secrets(' '.join(command))
        if deadline is not None:
            lock_timeout = deadline - time.monotonic()
            if lock_timeout <= 0:
                raise MomongaSkCommandDeadlineExceeded(
                    'Ran out of time before running: %s' % masked)
        if not self._acquire_cmd_lock(lock_timeout, should_stop):
            raise MomongaSkCommandBusy('Another SK command is still running: %s'
                                       % masked)
        try:
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise MomongaSkCommandDeadlineExceeded(
                        'Ran out of time before running: %s' % masked)
                timeout = left if timeout is None else min(timeout, left)
            return self._exec_command_locked(command, wait_until, timeout,
                                             payload, should_stop)
        finally:
            self._cmd_lock.release()

    def _acquire_cmd_lock(self,
                          lock_timeout: int | float,
                          should_stop: Callable[[], bool] | None,
                          ) -> bool:
        """Wait for the command lock, in slices if the caller can be told to
        stop. cancel_commands() cannot reach this wait at all: a thread that
        never acquires the lock never looks at the queue the cancellation
        arrives on."""
        if should_stop is None:
            return self._cmd_lock.acquire(timeout=lock_timeout)

        give_up_at = None if lock_timeout < 0 else time.monotonic() + lock_timeout
        while True:
            if should_stop():
                raise MomongaSkCommandCancelled('Stopped waiting for the command lock.')

            wait = _STOP_CHECK_POLL
            if give_up_at is not None:
                left = give_up_at - time.monotonic()
                if left <= 0:
                    return False
                wait = min(wait, left)

            if self._cmd_lock.acquire(timeout=wait):
                return True

    def _exec_command_locked(self,
                             command: list[str],
                             wait_until: str | list[str],
                             timeout: int | float | None,
                             payload: bytes | None,
                             should_stop: Callable[[], bool] | None,
                             ) -> list[str]:
        line = ' '.join(command)

        expected = [wait_until] if isinstance(wait_until, str) else wait_until

        subscriber_q = self.subscribers['cmd_exec_q']
        while not subscriber_q.empty():
            subscriber_q.get()

        self._raise_if_publisher_died()
        self._raise_if_cancelled()

        self._writeline(line, payload)

        deadline = None if timeout is None else time.monotonic() + timeout

        res: list[str] = []
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                r = self._next_response_line(subscriber_q, remaining, should_stop)
                if isinstance(r, _PublisherStopped):
                    self._raise_if_publisher_died()
                    raise MomongaNeedToReopen('The packet publisher has stopped.'
                                              ' Close Momonga and open it again.')
                if isinstance(r, _CommandsCancelled):
                    raise MomongaSkCommandCancelled('The command was cancelled: %s'
                                                    % (_mask_secrets(line)))
            except queue.Empty:
                self._raise_if_publisher_died()
                raise MomongaNeedToReopen('The module did not respond to a command.'
                                          ' Close Momonga and open it again: %s' % (_mask_secrets(line))) from None

            if r.startswith('ERXUDP'):
                continue

            if r[:4] == 'FAIL':
                self._raise_fail_response(_mask_secrets(line), r)
            else:
                res.append(r)
                matched = False
                for w in expected:
                    if r.startswith(w):
                        matched = True
                        break
                if matched:
                    break
        return res

    def _next_response_line(self,
                            subscriber_q: queue.Queue[Any],
                            remaining: int | float | None,
                            should_stop: Callable[[], bool] | None,
                            ) -> str | _PublisherStopped | _CommandsCancelled:
        """One line from the module, in slices if the caller can be told to
        stop. Unlike the lock, cancel_commands() does reach this wait - but
        close() only sends it once it has finished waiting for the lock this
        command holds, which is the wait it would have cut short."""
        if should_stop is None:
            line: str | _PublisherStopped | _CommandsCancelled = subscriber_q.get(timeout=remaining)
            return line

        give_up_at = None if remaining is None else time.monotonic() + remaining
        while True:
            if should_stop():
                raise MomongaSkCommandCancelled('Stopped waiting for the module.')

            wait = _STOP_CHECK_POLL
            if give_up_at is not None:
                left = give_up_at - time.monotonic()
                if left <= 0:
                    raise queue.Empty
                wait = min(wait, left)

            try:
                sliced: str | _PublisherStopped | _CommandsCancelled = subscriber_q.get(timeout=wait)
            except queue.Empty:
                continue
            return sliced

    def _raise_if_publisher_died(self) -> None:
        if self.publisher_exception is not None:
            raise MomongaNeedToReopen('The packet publisher has stopped.'
                                      ' Close Momonga and open it again. %s: %s'
                                      % (type(self.publisher_exception).__name__,
                                         self.publisher_exception)) from self.publisher_exception

    def _raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise MomongaSkCommandCancelled('SK command execution has been cancelled.'
                                            ' Close Momonga and open it again.')

    def _raise_fail_response(self, command: str, r: str) -> None:
        try:
            error_code = int(r[7:10])
        except ValueError:
            raise MomongaSkCommandUnknownError(
                'Unreadable failure response "%s": %s' % (r, command)) from None

        if 1 <= error_code <= 3:
            raise MomongaSkCommandUnknownError('Unknown error code %s: %s' % (error_code, command))
        elif error_code == 4:
            raise MomongaSkCommandUnsupported('Unsupported command: %s' % (command))
        elif error_code == 5:
            raise MomongaSkCommandInvalidArgument('Invalid argument: %s' % (command))
        elif error_code == 6:
            raise MomongaSkCommandInvalidSyntax('Invalid syntax: %s' % (command))
        elif 7 <= error_code <= 8:
            raise MomongaSkCommandUnknownError('Unknown error code %s: %s' % (error_code, command))
        elif error_code == 9:
            raise MomongaSkCommandSerialInputError('Serial input error: %s' % (command))
        elif error_code == 10:
            raise MomongaSkCommandFailedToExecute(
                'The specified command was accepted but failed to execute: %s' % (command))
        else:
            raise MomongaSkCommandUnknownError('Unknown error code %s: %s' % (error_code, command))

    def skver(self) -> SkVerResponse:
        res = self.exec_command(['SKVER'])
        return SkVerResponse(res)

    def skappver(self) -> SkAppVerResponse:
        res = self.exec_command(['SKAPPVER'])
        return SkAppVerResponse(res)

    def skreset(self) -> None:
        self.exec_command(['SKRESET'])

    def skinfo(self) -> SkInfoResponse:
        res = self.exec_command(['SKINFO'])
        return SkInfoResponse(res)

    def sksreg(self,
               reg: str,
               val: str | int | bytes,
               ) -> None:
        if isinstance(val, int):
            text = '%X' % val
        elif isinstance(val, bytes):
            text = val.hex().upper()
        else:
            text = val
        self.exec_command(['SKSREG', reg, text])

    def sksetrbid(self,
                  rbid: str,
                  ) -> None:
        self.exec_command(['SKSETRBID', rbid])

    def sksetpwd(self,
                 pwd: str,
                 ) -> None:
        self.exec_command(['SKSETPWD', '%X' % len(pwd), pwd])

    def skscan(self,
               retry: int = 3,
               ) -> SkScanResponse:
        duration = _SCAN_FIRST_DURATION
        unreadable = None
        for _ in range(retry):
            logger.debug('Trying to scan a PAN... Duration: %d', duration)
            res = self.exec_command(self.device_strategy.skscan_command(duration), 'EVENT 22')
            # estimated execution time: 0.0096s*(2^(DURATION=6)+1)*28 = 17.5s
            # estimated execution time: 0.0096s*(2^(DURATION=7)+1)*28 = 34.7s
            # estimated execution time: 0.0096s*(2^(DURATION=8)+1)*28 = 69.1s
            if 'EPANDESC' in res:
                try:
                    return SkScanResponse(res, self.device_strategy.decode_scan_side)
                except MomongaSkResponseNotExpected as err:
                    unreadable = err
                    logger.warning('A PAN was announced but its description '
                                   'could not be read. Scanning again. %s', err)
            duration = min(duration + 1, _SCAN_WIDEST_DURATION)

        if unreadable is not None:
            raise MomongaSkScanFailure(
                'Found a PAN but never read a complete description of it. %s'
                % unreadable)
        raise MomongaSkScanFailure('Could not find the specified PAN.')

    def skll64(self,
               mac_addr: bytes,
               ) -> SkLl64Response:
        res = self.exec_command(['SKLL64', mac_addr.hex().upper()], 'FE80:')
        return SkLl64Response(res)

    def skjoin(self,
               ip6_addr: str,
               retry: int = 3,
               deadline: float | None = None,
               should_stop: Callable[[], bool] | None = None,
               ) -> None:
        for _ in range(retry):
            if should_stop is not None and should_stop():
                raise MomongaSkCommandCancelled('Stopped trying to establish a PANA session.')
            logger.debug('Trying to establish a PANA session...')
            res = self.exec_command(['SKJOIN', ip6_addr], ['EVENT 24', 'EVENT 25'],
                                    deadline=deadline, should_stop=should_stop)
            # estimated execution time: 2s + 4s + 8s + 8s + 8s + 8s + 8s = 38s ~ 40s
            if res[-1].startswith('EVENT 25'):
                logger.debug('A PANA session has been established.')
                return
        raise MomongaSkJoinFailure('Could not establish a PANA session.')

    def skterm(self,
               lock_timeout: int | float = -1,
               deadline: float | None = None,
               ) -> None:
        logger.debug('Trying to terminate the session...')
        res = self.exec_command(['SKTERM'], ['EVENT 27', 'EVENT 28'],
                                lock_timeout=lock_timeout, deadline=deadline)
        if res[-1].startswith('EVENT 28'):
            logger.warning('There was no session to terminate.')

    def sksendto(self,
                 ip6_addr: str,
                 data: bytes,
                 handle: int = 1,
                 port: int = ECHONET_LITE_PORT,
                 sec: int = 2,
                 side: int = 0,
                 timeout: int | float | None = _SK_COMMAND_LIMIT,
                 lock_timeout: int | float = -1,
                 deadline: float | None = None,
                 ) -> None:
        self.exec_command(
            self.device_strategy.sksendto_args(handle, ip6_addr, port, sec, side, len(data)),
            payload=data,
            timeout=timeout,
            lock_timeout=lock_timeout,
            deadline=deadline,
        )

    def detect_device(self) -> None:
        logger.debug('Trying to detect the device...')
        dev_info = self.skinfo()
        if dev_info.side == _BP35A1_SIDE_SENTINEL:
            logger.debug('Device type is BP35A1.')
            self.device_strategy = BP35A1Strategy()
        elif dev_info.side < 2:
            logger.debug('Device type is BP35C2.')
            self.device_strategy = BP35C2Strategy()
        else:
            logger.warning('Device type is UNKNOWN. Assuming BP35C2.')
            self.device_strategy = BP35C2Strategy()
