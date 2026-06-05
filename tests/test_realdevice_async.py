"""
Integration tests for AsyncMomonga with a live smart meter.

Tests cover:
  - Async notification retrieval (get_notification, notifications generator)
  - Concurrent multi-context value retrieval via asyncio.gather()

Requires a Wi-SUN USB dongle connected to a live smart meter.

Configure via environment variables:
  MOMONGA_ROUTEB_ID        Route-B ID
  MOMONGA_ROUTEB_PASSWORD  Password
  MOMONGA_DEV_PATH         Serial device path (e.g. /dev/ttyUSB0, COM3)
  MOMONGA_TIMEOUT          Seconds to wait per call (default: 2400)

Run:
  MOMONGA_ROUTEB_ID=... MOMONGA_ROUTEB_PASSWORD=... MOMONGA_DEV_PATH=... \
    python -m unittest tests.test_realdevice_async -v
"""
import asyncio
import os
import unittest

from momonga import AsyncMomonga, EchonetServiceCode
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


# ---------------------------------------------------------------------------
# Async notification tests
# ---------------------------------------------------------------------------

@unittest.skipIf(_SKIP, _SKIP_REASON)
class TestAsyncNotification(unittest.IsolatedAsyncioTestCase):
    """AsyncMomonga notification methods with a live smart meter.

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


# ---------------------------------------------------------------------------
# Multi-context concurrent value retrieval tests
# ---------------------------------------------------------------------------

@unittest.skipIf(_SKIP, _SKIP_REASON)
class TestAsyncConcurrentRetrieval(unittest.IsolatedAsyncioTestCase):
    """Verify AsyncMomonga handles concurrent coroutine calls via asyncio.gather().

    Tests that multiple coroutines running in the same event loop can each
    independently retrieve values from the smart meter without interfering.
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

    async def test_gather_power_and_current(self):
        """Concurrent retrieval of instantaneous power and current both return valid values."""
        power, current = await asyncio.gather(
            self.mo.get_instantaneous_power(),
            self.mo.get_instantaneous_current(),
        )
        self.assertIsInstance(power, (int, float))
        self.assertIsInstance(current, dict)
        self.assertIn('r phase current', current)
        self.assertIn('t phase current', current)

    async def test_gather_same_property_from_two_coroutines(self):
        """Two concurrent calls to the same method both return valid independent results."""
        p1, p2 = await asyncio.gather(
            self.mo.get_instantaneous_power(),
            self.mo.get_instantaneous_power(),
        )
        self.assertIsInstance(p1, (int, float))
        self.assertIsInstance(p2, (int, float))

    async def test_gather_three_different_properties(self):
        """Three different property types retrieved concurrently all return valid values."""
        power, energy, current = await asyncio.gather(
            self.mo.get_instantaneous_power(),
            self.mo.get_measured_cumulative_energy(),
            self.mo.get_instantaneous_current(),
        )
        self.assertIsInstance(power, (int, float))
        self.assertIsInstance(energy, (int, float))
        self.assertIsInstance(current, dict)


if __name__ == '__main__':
    unittest.main()
