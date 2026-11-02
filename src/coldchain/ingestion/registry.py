"""Lookup of device serial numbers to database ids, cached in memory."""
import time

import psycopg

from coldchain.config import get_settings

REFRESH_AFTER_MISS_S = 1  # reload at most this often when an unknown serial appears


class DeviceRegistry:
    def __init__(self) -> None:
        self._ids: dict[str, str] = {}
        self._loaded_at = 0.0
        self._url = get_settings().db_url("app").replace("postgresql+psycopg://", "postgresql://")
        self.reload()

    def reload(self) -> None:
        with psycopg.connect(self._url) as conn:
            rows = conn.execute("SELECT serial_no, id::text FROM device WHERE status = 'active'").fetchall()
        self._ids = dict(rows)
        self._loaded_at = time.monotonic()

    def lookup(self, serial: str) -> str | None:
        """Device id for an active device, or None if the device is unknown or inactive."""
        if serial not in self._ids and time.monotonic() - self._loaded_at > REFRESH_AFTER_MISS_S:
            self.reload()  # a device registered since the last load
        return self._ids.get(serial)
