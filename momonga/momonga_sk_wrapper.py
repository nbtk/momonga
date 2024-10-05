import logging
import threading
import queue
import serial

from .momonga_exception import (MomongaError,
                                MomongaTimeoutError,
                                MomongaSkCommandUnknownError,
                                MomongaSkCommandUnsupported,
                                MomongaSkCommandInvalidArgument,
                                MomongaSkCommandInvalidSyntax,
                                MomongaSkCommandSerialInputError,
                                MomongaSkCommandFailedToExecute,
                                MomongaSkScanFailure,
                                MomongaSkJoinFailure)
from .momonga_response import (SkVerResponse,
                               SkAppVerResponse,
                               SkInfoResponse,
                               SkScanResponse,
                               SkLl64Response)

try:
    from typing import Self
except ImportError:
    Self = object

logger = logging.getLogger(__name__)


class MomongaSkWrapper:
    def __init__(self,
                 dev: str,
                 baudrate: int = 115200,
                 ) -> None:
        self.dev = dev
        self.baudrate = baudrate

        # the following value will be set a pyserial object.
        self.ser = None
        self.publisher_th = None
        self.subscribers = {'cmd_exec_q': queue.Queue()}

    #def __enter__(self) -> Self:
    def __enter__(self):
        return self.open()

    def __exit__(self, type, value, traceback) -> None:
        self.close()

    #def open(self) -> Self:
    def open(self):
        self.ser = serial.Serial(self.dev, self.baudrate)

        # to drop garbage data in the buffer.
        self.__clear_buf()

        # to check to be returned the udp payloads in ascii.
        if self.__exec_ropt() != 1:
            logger.warning("Executing 'WOPT 01\\r' command to make the Wi-SUN module return the UDP payloads "
                           "in ASCII format. Note: WOPT command can only be executed a limited number of times. "
                           "This configuration is saved in the Wi-SUN module, so this log message should "
                           "no longer appear.")
            self.__exec_wopt(1)  # to make the wi-sun module return the UDP payloads in ASCII format.

        for q in self.subscribers.values():
            while not q.empty():
                q.get()

        self.publisher_th_breaker = False  # set True when you want to stop the publisher.
        self.publisher_th = threading.Thread(target=self.received_packet_publisher, daemon=True)
        self.publisher_th.start()

    def close(self) -> None:
        if self.publisher_th is not None:
            self.publisher_th_breaker = True
            self.publisher_th.join()
            self.publisher_th = None
        if self.ser is not None and not self.ser.closed:
            self.ser.close()

    def __clear_buf(self) -> None:  # do not call this after open().
        self.ser.write(b'\r\n')
        self.ser.flush()
        timeout = self.ser.timeout
        self.ser.timeout = 2  # will wait the specified seconds.
        while self.ser.read():
            # this loop clears garbage data if it exists.
            pass
        # to undo the timeout.
        self.ser.timeout = timeout

    def __exec_ropt(self) -> int:  # do not call this after open().
        self.ser.write(b'ROPT\r')
        self.ser.flush()
        res = b''
        ok = b'OK '
        while True:
            res += self.ser.read()
            if ok in res and res.endswith(b'\r'):
                break
        return int(res[res.index(ok) + len(ok):-1].decode())

    def __exec_wopt(self,
                    opt: int,
                    ) -> None:  # do not call this after open().
        supported_opts = (0,  # binary mode
                          1,  # hex ascii mode
                          )
        if opt not in supported_opts:
            raise MomongaError('WOPT command dose not support the given option: %03d' % opt)

        self.ser.write(('WOPT %02d\r' % opt).encode())
        self.ser.flush()
        res = b''
        while True:
            res += self.ser.read()
            if b'OK\r' in res:
                break
        return

    def __readline(self,
                   timeout: int | None = None,
                   ) -> str:
        org_timeout = self.ser.timeout
        self.ser.timeout = timeout
        data_bytes = self.ser.readline()
        self.ser.timeout = org_timeout
        if data_bytes != b'':
            logger.debug('<<< %s' % data_bytes)
        line = data_bytes.decode().split('\r\n')[0]
        return line

    def received_packet_publisher(self) -> None:
        logger.debug('A received packet publisher has been started.')
        while True:
            if self.publisher_th_breaker is True:
                break
            line = self.__readline(timeout=1)
            if line == '':
                continue
            for q in self.subscribers.values():
                q.put(line)  # will dispatch the line to each subscriber
        logger.debug('The received packet publisher has been stopped.')

    def __writeline(self,
                    line: str,
                    payload: bytes | None = None,
                    ) -> None:
        if payload is not None:
            data_bytes = (line + ' ').encode() + payload
        else:
            data_bytes = (line + '\r\n').encode()
        self.ser.write(data_bytes)
        logger.debug('>>> %s' % data_bytes)
        self.ser.flush()

    def exec_command(self,
                     command: list[str],
                     wait_until: str | list[str] = 'OK',
                     timeout: int | None = None,
                     payload: bytes | None = None,
                     ) -> list[str]:
        command = ' '.join(command)

        if type(wait_until) is str:
            wait_until = [wait_until]

        subscriber_q = self.subscribers['cmd_exec_q']
        while not subscriber_q.empty():
            subscriber_q.get()

        self.__writeline(command, payload)

        res = []
        while True:
            r = subscriber_q.get(timeout=timeout)
            if r.startswith('ERXUDP'):
                continue

            if r == '':
                raise MomongaTimeoutError('The command timed out: %s' % (command))
            elif r[:4] == 'FAIL':
                error_code = int(r[7:10])
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
                res.append(r)
                matched = False
                for w in wait_until:
                    if r.startswith(w):
                        matched = True
                        break
                if matched is True:
                    break
        return res

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
        if type(val) is int:
            val = '%X' % val
        elif type(val) is bytes:
            val = val.hex().upper()
        self.exec_command(['SKSREG', reg, val])

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
        duration = 6
        for _ in range(retry):
            logger.debug('Trying to scan a PAN... Duration: %d' % duration)
            res = self.exec_command(['SKSCAN', '2', 'FFFFFFFF', str(duration), '0'], 'EVENT 22')
            # estimated execution time: 0.0096s*(2^(DURATION=6)+1)*28 = 17.5s
            # estimated execution time: 0.0096s*(2^(DURATION=7)+1)*28 = 34.7s
            # estimated execution time: 0.0096s*(2^(DURATION=8)+1)*28 = 69.1s
            if 'EPANDESC' in res:
                return SkScanResponse(res)
            duration += 1
        raise MomongaSkScanFailure('Could not find the specified PAN.')

    def skll64(self,
               mac_addr: bytes,
               ) -> SkLl64Response:
        res = self.exec_command(['SKLL64', mac_addr.hex().upper()], 'FE80:')
        return SkLl64Response(res)

    def skjoin(self,
               ip6_addr: str,
               retry: int = 3,
               ) -> None:
        for _ in range(retry):
            logger.debug('Trying to establish a PANA session...')
            res = self.exec_command(['SKJOIN', ip6_addr], ['EVENT 24', 'EVENT 25'])
            # extimated execution time: 2s + 4s + 8s + 8s + 8s + 8s + 8s = 38s ~ 40s
            if res[-1].startswith('EVENT 25'):
                logger.debug('A PANA Session has been established.')
                return
        raise MomongaSkJoinFailure('Could not establish a PANA session.')

    def skterm(self) -> None:
        logger.debug('Trying to terminate the session...')
        res = self.exec_command(['SKTERM'], ['EVENT 27', 'EVENT 28'])
        if res[-1].startswith('EVENT 28'):
            logger.warning('There was no session to terminate.')

    def sksendto(self,
                 ip6_addr: str,
                 data: bytes,
                 handle: int = 1,
                 port: int = 0x0E1A,
                 sec: int = 2,
                 side: int = 0,
                 ) -> None:
        self.exec_command(['SKSENDTO', str(handle), ip6_addr, '%04X' % port,
                           str(sec), str(side), '%04X' % len(data)],
                          payload=data)
