"""
Integration tests requiring actual hardware (Wi-SUN dongle + smart meter).

Configure via environment variables:
  MOMONGA_RBID     Route-B ID
  MOMONGA_PWD      Password
  MOMONGA_DEV      Serial device  (e.g. /dev/ttyUSB0, /dev/tty.usbserial-..., COM3)
  MOMONGA_TIMEOUT  Seconds to wait per notification call (default: 2400)

Run:
  MOMONGA_RBID=... MOMONGA_PWD=... MOMONGA_DEV=... python -m unittest tests/test_realdevice.py -v
"""
import asyncio
import os
import unittest

from momonga import AsyncMomonga, Momonga, EchonetServiceCode
from momonga.momonga_echonet_enum import EchonetPropertyCode

_RBID    = os.environ.get('MOMONGA_RBID', '')
_PWD     = os.environ.get('MOMONGA_PWD', '')
_DEV     = os.environ.get('MOMONGA_DEV', '')
_TIMEOUT = int(os.environ.get('MOMONGA_TIMEOUT', '2400'))

_SKIP        = not all([_RBID, _PWD, _DEV])
_SKIP_REASON = 'Set MOMONGA_RBID, MOMONGA_PWD, MOMONGA_DEV to run hardware integration tests.'


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


# ---------------------------------------------------------------------------
# Sync notification tests
# ---------------------------------------------------------------------------

@unittest.skipIf(_SKIP, _SKIP_REASON)
class TestNotificationSync(unittest.TestCase):
    """Tests for Momonga.get_notification() with a live smart meter.

    One PANA session is shared across all tests in this class.
    Each test consumes one notification from the queue in sequence.
    """

    @classmethod
    def setUpClass(cls):
        cls.mo = Momonga(_RBID, _PWD, _DEV)
        cls.mo.open()

    @classmethod
    def tearDownClass(cls):
        cls.mo.close()

    def test_notification_structure_and_types(self):
        """Notification must have correct top-level keys, valid ESV, and parsed property values."""
        notif = self.mo.get_notification(timeout=_TIMEOUT)
        _assert_notification(self, notif)

    def test_second_notification_also_valid(self):
        """A second call also returns a well-formed notification."""
        notif = self.mo.get_notification(timeout=_TIMEOUT)
        _assert_notification(self, notif)

    def test_timeout_zero_returns_none_on_empty_queue(self):
        """After draining the queue, timeout=0 must return None immediately."""
        while self.mo.get_notification(timeout=0) is not None:
            pass
        self.assertIsNone(self.mo.get_notification(timeout=0))


# ---------------------------------------------------------------------------
# Async notification tests
# ---------------------------------------------------------------------------

@unittest.skipIf(_SKIP, _SKIP_REASON)
class TestNotificationAsync(unittest.IsolatedAsyncioTestCase):
    """Tests for AsyncMomonga notification methods with a live smart meter.

    One PANA session is shared across all tests via asyncio.run() in setUpClass.
    """

    @classmethod
    def setUpClass(cls):
        async def _open():
            mo = AsyncMomonga(_RBID, _PWD, _DEV)
            await mo.open()
            return mo
        cls.mo = asyncio.run(_open())

    @classmethod
    def tearDownClass(cls):
        async def _close():
            await cls.mo.close()
        asyncio.run(_close())

    async def test_is_open_after_open(self):
        self.assertTrue(self.mo._sync.is_open)

    async def test_get_notification_returns_valid_dict(self):
        """get_notification() must return a well-formed notification dict."""
        notif = await self.mo.get_notification(timeout=_TIMEOUT)
        _assert_notification(self, notif)

    async def test_second_notification_also_valid(self):
        notif = await self.mo.get_notification(timeout=_TIMEOUT)
        _assert_notification(self, notif)

    async def test_notifications_generator_yields_valid_dicts(self):
        """notifications() must yield consecutive well-formed dicts."""
        collected = []
        async for notif in self.mo.notifications(timeout=_TIMEOUT):
            _assert_notification(self, notif)
            collected.append(notif)
            if len(collected) >= 2:
                break
        self.assertEqual(len(collected), 2)

    async def test_timeout_zero_returns_none_on_empty_queue(self):
        while await self.mo.get_notification(timeout=0) is not None:
            pass
        self.assertIsNone(await self.mo.get_notification(timeout=0))


if __name__ == '__main__':
    unittest.main()
