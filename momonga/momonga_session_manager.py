
import logging
import threading
import queue
import time

from .momonga_exception import *
from .momonga_sk_wrapper import MomongaSkWrapper
from .momonga_sk_wrapper import logger as sk_wrapper_logger


try:
    from typing import Self
except ImportError:
    Self = object


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
        self.xmit_restriction_cnt = 0
        self.xmit_lock = threading.Lock()
        self.rejoin_lock = threading.Lock()

        self.pkt_sbsc_q = queue.Queue()
        self.recv_q = queue.Queue()
        self.xmit_q = queue.Queue()

#    def __enter__(self) -> Self:
    def __enter__(self):
        return self.open()

    def __exit__(self, type, value, traceback) -> None:
        self.close()

#    def open(self) -> Self:
    def open(self):
        logger.info('Opening a Momonga session...')
        try:
            self.skw.open()

            if self.reset_dev is True:
                # to reset the specified wi-sun module.
                self.skw.skreset()

            # to disable echoback.
            #self.sksreg('SFE', '0')

            # to show the rssi of the received packets.
            self.skw.sksreg('SA2', '1')

            # scanning a PAN from here.
            # to set a route b id.
            self.skw.sksetrbid(self.rbid)
            # to set a pasword.
            self.skw.sksetpwd(self.pwd)
            logger.info('The Route-B ID and the password were registered.')
            try:
                logger.info('Scanning PAN channels...')
                scan_res = self.skw.skscan()
                logger.info('A PAN was found.')
            except MomongaSkScanFailure as e:
                logger.error('Gave up to find a PAN. Check the device location and Route-B ID. Then try again.')
                raise MomongaSkScanFailure('Gave up to find a PAN. Check the device location and Route-B ID. Then try again.')
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
            except MomongaSkJoinFailure:
                logger.error('Gave up to establish a PANA session. Check the Route-B ID and password. Then try again.')
                raise MomongaSkJoinFailure('Gave up to establish a PANA session. Check the Route-B ID and password. Then try again.')

            while not self.pkt_sbsc_q.empty():
                self.pkt_sbsc_q.get()
            while not self.recv_q.empty():
                self.recv_q.get()
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
            raise e

    def close(self) -> None:
        logger.info('Closing the Momonga session...')

        if self.rejoin_lock.acquire(timeout=120) is False:
            logger.warning('Failed to acquire "rejoin_lock".')

        if self.session_established is True:
            try:
                self.session_established = False
                logger.info('Terminating the PANA session...')
                self.skw.skterm()
            except Exception as e:
                logger.warning('Failed to terminate the PANA session. %s: %s' % (type(e).__name__, e))
            finally:
                self.rejoin_lock.release()
        else:
            self.rejoin_lock.release()

        if self.receiver_th is not None:
            if self.receiver_th.is_alive():
                self.pkt_sbsc_q.put('__CLOSE__') # to close the receiver thread.
                self.receiver_th.join()
            self.receiver_th = None

        if self.skw.subscribers.get('pkt_sbsc_q') is not None:
            self.skw.subscribers.pop('pkt_sbsc_q')

        self.unrestrict_to_xmit(force=True)

        assert self.xmit_lock.locked() is False, '"xmit_lock" is unexpectedly locked.'
        assert self.rejoin_lock.locked() is False, '"rejoin_lock" is unexpectedly locked.'

        self.skw.close()
        logger.info('The Momonga session is closed.')

    def receiver(self) -> None:
        logger.debug('The packet receiver has been started.')
        try:
            while True:
                res = self.pkt_sbsc_q.get()
                if res == '__CLOSE__':
                    break

                if not (res.startswith('EVENT') or res.startswith('ERXUDP')):
                    # droping the command responses.
                    continue

                if res.startswith('EVENT 29'):
                    logger.debug('The PANA session lifetime has been expired.')
                    self.restrict_to_xmit()
                elif res.startswith('EVENT 24'):
                    logger.warning('Could not rejoin the PAN.')
                    self.rejoin_lock.acquire()
                    if self.session_established is True:
                        self.session_established = False
                        try:
                            self.skw.skjoin(self.smart_meter_addr)
                        except MomongaSkJoinFailure as e:
                            logger.error('%s Close Momonga and open it again.' % e)
                            raise MomongaNeedToReopen('%s Close Momonga and open it again.' % e)
                        finally:
                            self.rejoin_lock.release()
                    else:
                        self.rejoin_lock.release()
                elif res.startswith('EVENT 25'):
                    logger.debug('Successfully rejoined the PAN.')
                    self.session_established = True
                    self.unrestrict_to_xmit()
                elif res.startswith('EVENT 32'):
                    logger.warning('The transmission rate limit has been exceeded.')
                    self.restrict_to_xmit()
                elif res.startswith('EVENT 33'):
                    logger.debug('The transmission rate limit has been released.')
                    self.unrestrict_to_xmit()
                elif res.startswith('EVENT 27'):
                    self.restrict_to_xmit()
                    logger.debug('The PANA session has been closed successfully.')
                elif res.startswith('EVENT 28'): # there was no session to close.
                    self.restrict_to_xmit()
                    logger.warning('There was no PANA session to close.')
                elif res.startswith('EVENT 21') or res.startswith('EVENT 02'):
                    self.recv_q.put(res)
                elif res.startswith('ERXUDP'):
                    self.recv_q.put(res)
        except Exception as e:
            logger.error('An exception was raised from the receiver thread. %s: %s' % (type(e).__name__, e))
            self.receiver_exception = e

        logger.debug('The packet receiver has been stopped.')

    def xmitter(self,
                data: bytes,
               ) -> None:
        xmitted = False
        for _ in range(3):
            logger.debug('Trying to acquire "xmit_lock".')
            for _ in range(30):
                unlocked =  self.xmit_lock.acquire(timeout=120) 
                if unlocked is False: 
                    logger.warning('Could not acquire "xmit_lock".')
                    if self.receiver_exception is not None:
                        logger.error('Got an exception from the receiver thread. %s: %s' % (type(self.receiver_exception).__name__, self.receiver_exception))
                        raise MomongaNeedToReopen('Got an exception from the receiver thread. %s: %s' % (type(self.receiver_exception).__name__, self.receiver_exception))
                else:
                    break

            if unlocked is False: 
                logger.error('Transmission rights could not be acquired. Close Momonga and open it again.')
                raise MomongaNeedToReopen('Transmission rights could not be acquired. Close Momonga and open it again.')
            else:
                logger.debug('Acquired "xmit_lock".')

            assert self.session_established is not False, 'Tried to transmit a packet, but no PANA session was established.'

            try:
                self.skw.sksendto(self.smart_meter_addr, data)
                xmitted = True
                break
            except MomongaSkCommandExecutionFailure as e:
                logger.warning('Failed to transmit a packet: %s' % e)
            except Exception as e:
                logger.warning('An error occurred to transmit a packet. %s: %s' % (type(e).__name__, e))
            finally:
                self.xmit_lock.release()
            time.sleep(3)
        if xmitted is False:
            logger.error('Could not transmit a packet. Close Momonga and open it again.')
            raise MomongaNeedToReopen('Could not transmit a packet. Close Momonga and open it again.')

    def restrict_to_xmit(self) -> None:
        self.xmit_restriction_cnt += 1
        logger.debug('The counter for the restriction was incremented: %d' % (self.xmit_restriction_cnt))

        assert self.xmit_restriction_cnt <= 2, 'The critical section counter for data transmission is inconsistent: Too big than expected.'

        if self.xmit_restriction_cnt == 1:
            logger.debug('Trying to restrict data transmission.')
            self.xmit_lock.acquire()
            logger.debug('Data transmission is being restricted.')

    def unrestrict_to_xmit(self, force=False) -> None:
        if force is True:
            self.xmit_restriction_cnt = 0
            logger.debug('The counter for the restriction was forcibly set to zero.')
        else:
            self.xmit_restriction_cnt -= 1
            logger.debug('The counter for the restriction was decremented: %d' % (self.xmit_restriction_cnt))

        assert self.xmit_restriction_cnt >= 0, 'The critical section counter for data transmit is inconsistent: Too small than expected.'

        if self.xmit_restriction_cnt == 0:
            try:
                self.xmit_lock.release()
            except RuntimeError as e:
                #logger.warning('Could not release "xmit_lock": %s' % e)
                pass
            logger.debug('Data transmission is being unrestricted.')
