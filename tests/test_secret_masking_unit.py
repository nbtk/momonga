"""
Unit tests for masking Route-B credentials in logs and exception messages.

Run:
  python -m unittest tests/test_secret_masking_unit.py -v
"""
import logging
import queue
import unittest
from unittest.mock import MagicMock, patch

from momonga.momonga_exception import (
    MomongaNeedToReopen,
    MomongaSkCommandInvalidArgument,
)
from momonga.momonga_sk_wrapper import MomongaSkWrapper
from tests._timebox import TimeBoxedTestCase

RBID = '00112233445566778899AABBCCDDEEFF'
PWD = 'mySecretPass'
WRITELINE = '_writeline'


def _make_skw():
    skw = MomongaSkWrapper('/dev/ttyUSB0', 115200)
    skw._ser = MagicMock()
    return skw


class TestOutboundLog(TimeBoxedTestCase):

    def _write(self, skw, *command):
        # exec_command() drains the queue first, so answer from inside the write
        skw._ser.write.side_effect = lambda data: skw.subscribers['cmd_exec_q'].put('OK')
        with self.assertLogs('momonga.momonga_sk_wrapper', level=logging.DEBUG) as captured:
            skw.exec_command(list(command), timeout=5)
        return '\n'.join(captured.output)

    def test_password_is_not_logged(self):
        log = self._write(_make_skw(), 'SKSETPWD', '%X' % len(PWD), PWD)
        self.assertNotIn(PWD, log)
        self.assertIn('SKSETPWD ****', log)

    def test_route_b_id_is_not_logged(self):
        log = self._write(_make_skw(), 'SKSETRBID', RBID)
        self.assertNotIn(RBID, log)
        self.assertIn('SKSETRBID ****', log)

    def test_ordinary_commands_are_logged_unchanged(self):
        log = self._write(_make_skw(), 'SKSREG', 'S2', '21')
        self.assertIn("b'SKSREG S2 21\\r\\n'", log)


class TestInboundLog(TimeBoxedTestCase):

    def test_echoed_password_is_not_logged(self):
        # open() does not disable echoback
        skw = _make_skw()
        skw._ser.readline.return_value = ('SKSETPWD %X %s\r\n' % (len(PWD), PWD)).encode()

        with self.assertLogs('momonga.momonga_sk_wrapper', level=logging.DEBUG) as captured:
            skw._readline(timeout=1)

        log = '\n'.join(captured.output)
        self.assertNotIn(PWD, log)
        self.assertIn('SKSETPWD ****', log)


class TestExceptionMessages(TimeBoxedTestCase):

    def test_password_is_not_in_a_fail_response(self):
        skw = _make_skw()

        def fake_writeline(line, payload=None):
            skw.subscribers['cmd_exec_q'].put('FAIL ER05')

        with patch.object(skw, WRITELINE, fake_writeline):
            with self.assertRaises(MomongaSkCommandInvalidArgument) as ctx:
                skw.exec_command(['SKSETPWD', '%X' % len(PWD), PWD])

        self.assertNotIn(PWD, str(ctx.exception))

    def test_password_is_not_in_a_timeout_message(self):
        skw = _make_skw()

        with patch.object(skw, WRITELINE, lambda line, payload=None: None):
            with self.assertRaises(MomongaNeedToReopen) as ctx:
                skw.exec_command(['SKSETPWD', '%X' % len(PWD), PWD], timeout=0.05)

        self.assertNotIn(PWD, str(ctx.exception))

    def test_route_b_id_is_not_in_a_fail_response(self):
        skw = _make_skw()

        def fake_writeline(line, payload=None):
            skw.subscribers['cmd_exec_q'].put('FAIL ER05')

        with patch.object(skw, WRITELINE, fake_writeline):
            with self.assertRaises(MomongaSkCommandInvalidArgument) as ctx:
                skw.exec_command(['SKSETRBID', RBID])

        self.assertNotIn(RBID, str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
