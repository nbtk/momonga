"""
Integration tests for smart meter notification (sync API).

Requires a Wi-SUN USB dongle connected to a live smart meter.

Configure via environment variables:
  MOMONGA_ROUTEB_ID        Route-B ID
  MOMONGA_ROUTEB_PASSWORD  Password
  MOMONGA_DEV_PATH         Serial device path (e.g. /dev/ttyUSB0, COM3)
  MOMONGA_TIMEOUT          Seconds to wait per call (default: 2400)

Run:
  MOMONGA_ROUTEB_ID=... MOMONGA_ROUTEB_PASSWORD=... MOMONGA_DEV_PATH=... \
    python -m unittest tests/test_realdevice_notification.py -v
"""
import os
import unittest

from momonga import Momonga, EchonetServiceCode
from momonga.momonga_echonet_enum import EchonetPropertyCode

_RBID    = os.environ.get('MOMONGA_ROUTEB_ID', '')
_PWD     = os.environ.get('MOMONGA_ROUTEB_PASSWORD', '')
_DEV     = os.environ.get('MOMONGA_DEV_PATH', '')
_TIMEOUT = int(os.environ.get('MOMONGA_TIMEOUT', '2400'))

_SKIP        = not all([_RBID, _PWD, _DEV])
_SKIP_REASON = 'Set MOMONGA_ROUTEB_ID, MOMONGA_ROUTEB_PASSWORD, MOMONGA_DEV_PATH to run hardware integration tests.'


def _assert_notification(tc: unittest.TestCase, notif: dict | None) -> None:
    tc.assertIsNotNone(notif, 'No notification received within %ds.' % _TIMEOUT)
    tc.assertIn('esv', notif)
    tc.assertIn('properties', notif)
    tc.assertIn(notif['esv'], (EchonetServiceCode.inf, EchonetServiceCode.infc))
    for key in notif['properties']:
        tc.assertIsInstance(
            key, (EchonetPropertyCode, int),
            'Property key has unexpected type: %s (%r)' % (type(key).__name__, key))
    for key, val in notif['properties'].items():
        if isinstance(key, EchonetPropertyCode) and val is not None:
            tc.assertNotIsInstance(
                val, bytes,
                'Known EPC %s returned unparsed bytes: %r' % (key.name, val))


@unittest.skipIf(_SKIP, _SKIP_REASON)
class TestNotificationSync(unittest.TestCase):
    """Momonga.get_notification() with a live smart meter."""

    @classmethod
    def setUpClass(cls):
        cls.mo = Momonga(_RBID, _PWD, _DEV)
        cls.mo.open()

    @classmethod
    def tearDownClass(cls):
        cls.mo.close()

    def test_notification_flow(self):
        """One notification covers: structure, ESV, key types, parsed values, and timeout=0 on empty queue."""
        notif = self.mo.get_notification(timeout=_TIMEOUT)
        _assert_notification(self, notif)
        while self.mo.get_notification(timeout=0) is not None:
            pass
        self.assertIsNone(self.mo.get_notification(timeout=0))


if __name__ == '__main__':
    unittest.main()
