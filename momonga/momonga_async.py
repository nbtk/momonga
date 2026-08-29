import asyncio
import collections
import datetime
import functools
import logging
import math
import time

from concurrent.futures import ThreadPoolExecutor
from collections.abc import AsyncGenerator, Callable, Iterable
from types import TracebackType
from typing import Any, Self, TypeVar

from .momonga import Momonga
from .momonga_echonet_enum import EchonetPropertyCode
from .momonga_exception import MomongaRuntimeError

logger = logging.getLogger(__name__)

_Result = TypeVar('_Result')

_NOTIFICATION_POLL = 1

_DEFAULT_MAX_WORKERS = 4

# one to run the call, one so a call nobody is waiting for any more cannot keep
# the next one from starting
_RESERVED_WORKERS = 2

# notifications taken off the meter by reads that were cancelled, held for
# whoever reads next; past this the oldest is dropped with a warning
_ORPHAN_LIMIT = 32


class AsyncMomonga:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                 reopen_delays: Iterable[float] | Callable[[], Iterable[float]] | None = None,
                 max_workers: int = _DEFAULT_MAX_WORKERS,
                 scan_retries: int = 3,
                 join_retries: int = 3,
                 ) -> None:
        self._sync = Momonga(rbid, pwd, dev, baudrate, reset_dev, reopen_delays,
                             scan_retries, join_retries)
        self._orphaned: collections.deque[tuple[dict[str, Any], Any]] = collections.deque()
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix='momonga')
        self._notif_executor = ThreadPoolExecutor(max_workers=_RESERVED_WORKERS,
                                                  thread_name_prefix='momonga-notif')
        self._life_executor = ThreadPoolExecutor(max_workers=_RESERVED_WORKERS,
                                                 thread_name_prefix='momonga-life')

    def _run(self,
             fn: Callable[..., _Result],
             *args: Any,
             executor: ThreadPoolExecutor | None = None,
             ) -> asyncio.Future[_Result]:
        loop = asyncio.get_running_loop()
        try:
            return loop.run_in_executor(executor or self._executor,
                                        functools.partial(fn, *args))
        except RuntimeError as e:
            raise MomongaRuntimeError('Momonga is not open.') from e

    @property
    def xmit_retries(self) -> int:
        return self._sync.xmit_retries

    @xmit_retries.setter
    def xmit_retries(self, value: int) -> None:
        self._sync.xmit_retries = value

    @property
    def recv_timeout(self) -> int | float:
        return self._sync.recv_timeout

    @recv_timeout.setter
    def recv_timeout(self, value: int | float) -> None:
        self._sync.recv_timeout = value

    @property
    def xmit_timeout(self) -> int | float:
        return self._sync.xmit_timeout

    @xmit_timeout.setter
    def xmit_timeout(self, value: int | float) -> None:
        self._sync.xmit_timeout = value

    @property
    def internal_xmit_interval(self) -> int | float:
        return self._sync.internal_xmit_interval

    @internal_xmit_interval.setter
    def internal_xmit_interval(self, value: int | float) -> None:
        self._sync.internal_xmit_interval = value

    @property
    def is_open(self) -> bool:
        return self._sync.is_open

    @property
    def energy_unit(self) -> int | float:
        return self._sync.energy_unit

    @property
    def energy_coefficient(self) -> int:
        return self._sync.energy_coefficient

    @property
    def lqi(self) -> int | None:
        return self._sync.lqi

    @property
    def rssi(self) -> float | None:
        return self._sync.rssi

    async def __aenter__(self) -> Self:
        await self._run(self._sync.open, executor=self._life_executor)
        return self

    async def __aexit__(self,
                        exc_type: type[BaseException] | None,
                        exc_val: BaseException | None,
                        exc_tb: TracebackType | None) -> None:
        try:
            try:
                await self._run(self._sync.close, executor=self._life_executor)
            except Exception:
                if exc_val is None:
                    raise
                logger.warning('Failed to close the session while %s was propagating. '
                               'Keeping the original exception.',
                               exc_val.__class__.__name__, exc_info=True)
        finally:
            self._executor.shutdown(wait=False)
            self._notif_executor.shutdown(wait=False)
            self._life_executor.shutdown(wait=False)

    async def open(self) -> None:
        await self._run(self._sync.open, executor=self._life_executor)

    async def close(self) -> None:
        await self._run(self._sync.close, executor=self._life_executor)

    async def reopen(self) -> None:
        await self._run(self._sync.reopen, executor=self._life_executor)

    async def get_notification(self,
                               timeout: int | float | None = None,
                               ) -> dict[str, Any] | None:
        while self._orphaned:
            held, session = self._orphaned.popleft()
            if self._sync.is_open and self._sync.session_manager is session:
                return held

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            poll: int | float = _NOTIFICATION_POLL
            if deadline is not None:
                poll = min(poll, max(0.0, deadline - time.monotonic()))
            # the poll slice is this loop's business; the reply may use what the
            # caller actually allowed, which is everything when they set no timeout
            reply_budget = (math.inf if deadline is None
                            else max(0.0, deadline - time.monotonic()))
            reading: asyncio.Future[dict[str, Any] | None] = self._run(
                self._sync._read_notification,  # pyright: ignore[reportPrivateUsage]
                poll, reply_budget,
                executor=self._notif_executor)
            try:
                notif = await asyncio.shield(reading)
            except asyncio.CancelledError:
                reading.add_done_callback(self._keep_what_was_read)
                raise
            if notif is not None:
                return notif
            if deadline is not None and time.monotonic() >= deadline:
                return None

    def _keep_what_was_read(self, reading: asyncio.Future[Any]) -> None:
        if reading.cancelled() or reading.exception() is not None:
            return
        notif = reading.result()
        if notif is None:
            return

        if len(self._orphaned) >= _ORPHAN_LIMIT:
            self._orphaned.popleft()
            logger.warning('Dropping the oldest of %d notifications held for reads'
                           ' that were cancelled. Nobody is reading them.',
                           _ORPHAN_LIMIT)
        self._orphaned.append((notif, self._sync.session_manager))

    async def notifications(self,
                            timeout: int | float = 60,
                            ) -> AsyncGenerator[dict[str, Any], None]:
        while True:
            notif = await self.get_notification(timeout=timeout)
            if notif is not None:
                yield notif

    async def get_operation_status(self) -> bool | None:
        return await self._run(self._sync.get_operation_status)

    async def get_installation_location(self) -> str:
        return await self._run(self._sync.get_installation_location)

    async def get_standard_version(self) -> str:
        return await self._run(self._sync.get_standard_version)

    async def get_fault_status(self) -> bool | None:
        return await self._run(self._sync.get_fault_status)

    async def get_manufacturer_code(self) -> bytes:
        return await self._run(self._sync.get_manufacturer_code)

    async def get_serial_number(self) -> str:
        return await self._run(self._sync.get_serial_number)

    async def get_current_time_setting(self) -> datetime.time:
        return await self._run(self._sync.get_current_time_setting)

    async def get_current_date_setting(self) -> datetime.date:
        return await self._run(self._sync.get_current_date_setting)

    async def get_properties_for_status_notification(self) -> set[EchonetPropertyCode | int]:
        return await self._run(self._sync.get_properties_for_status_notification)

    async def get_properties_to_set_values(self) -> set[EchonetPropertyCode | int]:
        return await self._run(self._sync.get_properties_to_set_values)

    async def get_properties_to_get_values(self) -> set[EchonetPropertyCode | int]:
        return await self._run(self._sync.get_properties_to_get_values)

    async def get_route_b_id(self) -> dict[str, bytes]:
        return await self._run(self._sync.get_route_b_id)

    async def get_one_minute_measured_cumulative_energy(
            self,
    ) -> dict[str, datetime.datetime | dict[str, int | float | None]]:
        return await self._run(self._sync.get_one_minute_measured_cumulative_energy)

    async def get_coefficient_for_cumulative_energy(self) -> int:
        return await self._run(self._sync.get_coefficient_for_cumulative_energy)

    async def get_number_of_effective_digits_for_cumulative_energy(self) -> int:
        return await self._run(self._sync.get_number_of_effective_digits_for_cumulative_energy)

    async def get_measured_cumulative_energy(self,
                                             reverse: bool = False,
                                             ) -> int | float | None:
        return await self._run(self._sync.get_measured_cumulative_energy, reverse)

    async def get_unit_for_cumulative_energy(self) -> int | float:
        return await self._run(self._sync.get_unit_for_cumulative_energy)

    async def get_historical_cumulative_energy_1(
            self,
            day: int = 0,
            reverse: bool = False,
    ) -> list[dict[str, datetime.datetime | int | float | None]]:
        return await self._run(self._sync.get_historical_cumulative_energy_1, day, reverse)

    async def set_day_for_historical_data_1(self, day: int = 0) -> None:
        await self._run(self._sync.set_day_for_historical_data_1, day)

    async def get_day_for_historical_data_1(self) -> int:
        return await self._run(self._sync.get_day_for_historical_data_1)

    async def get_instantaneous_power(self) -> int | None:
        return await self._run(self._sync.get_instantaneous_power)

    async def get_instantaneous_current(self) -> dict[str, float | None]:
        return await self._run(self._sync.get_instantaneous_current)

    async def get_cumulative_energy_measured_at_fixed_time(
            self,
            reverse: bool = False,
    ) -> dict[str, datetime.datetime | int | float | None]:
        return await self._run(self._sync.get_cumulative_energy_measured_at_fixed_time, reverse)

    async def get_historical_cumulative_energy_2(
            self,
            timestamp: datetime.datetime | None = None,
            num_of_data_points: int = 12,
    ) -> list[dict[str, datetime.datetime | dict[str, int | float | None]]]:
        return await self._run(
            self._sync.get_historical_cumulative_energy_2, timestamp, num_of_data_points)

    async def set_time_for_historical_data_2(self,
                                             timestamp: datetime.datetime,
                                             num_of_data_points: int = 12,
                                             ) -> None:
        await self._run(self._sync.set_time_for_historical_data_2, timestamp, num_of_data_points)

    async def get_time_for_historical_data_2(self) -> dict[str, datetime.datetime | None | int]:
        return await self._run(self._sync.get_time_for_historical_data_2)

    async def get_historical_cumulative_energy_3(
            self,
            timestamp: datetime.datetime | None = None,
            num_of_data_points: int = 10,
    ) -> list[dict[str, datetime.datetime | dict[str, int | float | None]]]:
        return await self._run(
            self._sync.get_historical_cumulative_energy_3, timestamp, num_of_data_points)

    async def set_time_for_historical_data_3(self,
                                             timestamp: datetime.datetime,
                                             num_of_data_points: int = 10,
                                             ) -> None:
        await self._run(self._sync.set_time_for_historical_data_3, timestamp, num_of_data_points)

    async def get_time_for_historical_data_3(self) -> dict[str, datetime.datetime | None | int]:
        return await self._run(self._sync.get_time_for_historical_data_3)

    async def request_to_set(self,
                             day_for_historical_data_1: Momonga.DayForHistoricalData1 | None = None,
                             time_for_historical_data_2: Momonga.TimeForHistoricalData2 | None = None,
                             time_for_historical_data_3: Momonga.TimeForHistoricalData3 | None = None,
                             ) -> None:
        await self._run(self._sync.request_to_set,
                                day_for_historical_data_1,
                                time_for_historical_data_2,
                                time_for_historical_data_3)

    async def request_to_get(self,
                             properties: set[EchonetPropertyCode],
                             ) -> dict[EchonetPropertyCode | int, Any]:
        return await self._run(self._sync.request_to_get, properties)
