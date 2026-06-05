import asyncio
import datetime
from collections.abc import AsyncGenerator, Iterable
from typing import Any, Self

from .momonga import Momonga
from .momonga_echonet_enum import EchonetPropertyCode


class AsyncMomonga:
    def __init__(self,
                 rbid: str,
                 pwd: str,
                 dev: str,
                 baudrate: int = 115200,
                 reset_dev: bool = True,
                 reopen_delays: Iterable[float] | None = None,
                 ) -> None:
        self._sync = Momonga(rbid, pwd, dev, baudrate, reset_dev, reopen_delays)

    async def __aenter__(self) -> Self:
        await asyncio.to_thread(self._sync.open)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await asyncio.to_thread(self._sync.close)

    async def open(self) -> None:
        await asyncio.to_thread(self._sync.open)

    async def close(self) -> None:
        await asyncio.to_thread(self._sync.close)

    async def reopen(self) -> None:
        await asyncio.to_thread(self._sync.reopen)

    async def get_notification(self,
                               timeout: int | float | None = None,
                               ) -> dict | None:
        return await asyncio.to_thread(self._sync.get_notification, timeout)

    async def notifications(self,
                            timeout: int | float = 60,
                            ) -> AsyncGenerator[dict, None]:
        while True:
            notif = await self.get_notification(timeout=timeout)
            if notif is not None:
                yield notif

    async def get_operation_status(self) -> bool | None:
        return await asyncio.to_thread(self._sync.get_operation_status)

    async def get_installation_location(self) -> str:
        return await asyncio.to_thread(self._sync.get_installation_location)

    async def get_standard_version(self) -> str:
        return await asyncio.to_thread(self._sync.get_standard_version)

    async def get_fault_status(self) -> bool | None:
        return await asyncio.to_thread(self._sync.get_fault_status)

    async def get_manufacturer_code(self) -> bytes:
        return await asyncio.to_thread(self._sync.get_manufacturer_code)

    async def get_serial_number(self) -> str:
        return await asyncio.to_thread(self._sync.get_serial_number)

    async def get_current_time_setting(self) -> datetime.time:
        return await asyncio.to_thread(self._sync.get_current_time_setting)

    async def get_current_date_setting(self) -> datetime.date:
        return await asyncio.to_thread(self._sync.get_current_date_setting)

    async def get_properties_for_status_notification(self) -> set[EchonetPropertyCode | int]:
        return await asyncio.to_thread(self._sync.get_properties_for_status_notification)

    async def get_properties_to_set_values(self) -> set[EchonetPropertyCode | int]:
        return await asyncio.to_thread(self._sync.get_properties_to_set_values)

    async def get_properties_to_get_values(self) -> set[EchonetPropertyCode | int]:
        return await asyncio.to_thread(self._sync.get_properties_to_get_values)

    async def get_route_b_id(self) -> dict[str, bytes]:
        return await asyncio.to_thread(self._sync.get_route_b_id)

    async def get_one_minute_measured_cumulative_energy(
            self,
    ) -> dict[str, datetime.datetime | dict[str, int | float | None]]:
        return await asyncio.to_thread(self._sync.get_one_minute_measured_cumulative_energy)

    async def get_coefficient_for_cumulative_energy(self) -> int:
        return await asyncio.to_thread(self._sync.get_coefficient_for_cumulative_energy)

    async def get_number_of_effective_digits_for_cumulative_energy(self) -> int:
        return await asyncio.to_thread(self._sync.get_number_of_effective_digits_for_cumulative_energy)

    async def get_measured_cumulative_energy(self,
                                             reverse: bool = False,
                                             ) -> int | float:
        return await asyncio.to_thread(self._sync.get_measured_cumulative_energy, reverse)

    async def get_unit_for_cumulative_energy(self) -> int | float:
        return await asyncio.to_thread(self._sync.get_unit_for_cumulative_energy)

    async def get_historical_cumulative_energy_1(
            self,
            day: int = 0,
            reverse: bool = False,
    ) -> list[dict[str, datetime.datetime | dict[str, int | float | None]]]:
        return await asyncio.to_thread(self._sync.get_historical_cumulative_energy_1, day, reverse)

    async def set_day_for_historical_data_1(self, day: int = 0) -> None:
        await asyncio.to_thread(self._sync.set_day_for_historical_data_1, day)

    async def get_day_for_historical_data_1(self) -> int:
        return await asyncio.to_thread(self._sync.get_day_for_historical_data_1)

    async def get_instantaneous_power(self) -> float:
        return await asyncio.to_thread(self._sync.get_instantaneous_power)

    async def get_instantaneous_current(self) -> dict[str, float]:
        return await asyncio.to_thread(self._sync.get_instantaneous_current)

    async def get_cumulative_energy_measured_at_fixed_time(
            self,
            reverse: bool = False,
    ) -> dict[str, datetime.datetime | int | float]:
        return await asyncio.to_thread(self._sync.get_cumulative_energy_measured_at_fixed_time, reverse)

    async def get_historical_cumulative_energy_2(
            self,
            timestamp: datetime.datetime | None = None,
            num_of_data_points: int = 12,
    ) -> list[dict[str, datetime.datetime | dict[str, int | float | None]]]:
        return await asyncio.to_thread(
            self._sync.get_historical_cumulative_energy_2, timestamp, num_of_data_points)

    async def set_time_for_historical_data_2(self,
                                             timestamp: datetime.datetime,
                                             num_of_data_points: int = 12,
                                             ) -> None:
        await asyncio.to_thread(self._sync.set_time_for_historical_data_2, timestamp, num_of_data_points)

    async def get_time_for_historical_data_2(self) -> dict[str, datetime.datetime | None | int]:
        return await asyncio.to_thread(self._sync.get_time_for_historical_data_2)

    async def get_historical_cumulative_energy_3(
            self,
            timestamp: datetime.datetime | None = None,
            num_of_data_points: int = 10,
    ) -> list[dict[str, datetime.datetime | dict[str, int | float | None]]]:
        return await asyncio.to_thread(
            self._sync.get_historical_cumulative_energy_3, timestamp, num_of_data_points)

    async def set_time_for_historical_data_3(self,
                                             timestamp: datetime.datetime,
                                             num_of_data_points: int = 10,
                                             ) -> None:
        await asyncio.to_thread(self._sync.set_time_for_historical_data_3, timestamp, num_of_data_points)

    async def get_time_for_historical_data_3(self) -> dict[str, datetime.datetime | None | int]:
        return await asyncio.to_thread(self._sync.get_time_for_historical_data_3)

    async def request_to_set(self,
                             day_for_historical_data_1: Momonga.DayForHistoricalData1 | None = None,
                             time_for_historical_data_2: Momonga.TimeForHistoricalData2 | None = None,
                             time_for_historical_data_3: Momonga.TimeForHistoricalData3 | None = None,
                             ) -> None:
        await asyncio.to_thread(self._sync.request_to_set,
                                day_for_historical_data_1,
                                time_for_historical_data_2,
                                time_for_historical_data_3)

    async def request_to_get(self,
                             properties: set[EchonetPropertyCode],
                             ) -> dict[EchonetPropertyCode, Any]:
        return await asyncio.to_thread(self._sync.request_to_get, properties)
