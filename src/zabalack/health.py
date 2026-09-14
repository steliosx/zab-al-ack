"""A process-local, read-only capture probe, separate from Alerta's DB pool.

The synchronous plugin owns one lazy async Psycopg connection and event loop.
The lock covers refresh and every connection operation; no connection or loop is
created before first use in the serving process. Each command is sent separately
so SET LOCAL arms the SELECT statement's server-side timeout through PgBouncer.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import threading
import time
from typing import Any, Callable


class CaptureUnhealthy(RuntimeError):
    """Deliberately redacted failure suitable for the plugin error boundary."""


@dataclass(frozen=True)
class ProbeConfig:
    dsn: str
    database_name: str
    deployment_id: str
    config_revision: str
    schema_version: int = 5
    cache_seconds: float = 5.0
    statement_timeout_ms: int = 500
    acquisition_timeout: float = 0.5
    overall_timeout: float = 1.5

    def __post_init__(self) -> None:
        if not 0 <= self.cache_seconds <= 5:
            raise ValueError("capture probe cache must be between zero and five seconds")
        if not 1 <= self.statement_timeout_ms <= 500:
            raise ValueError("capture SELECT timeout must be at most 500ms")
        if not 0 < self.acquisition_timeout <= 1 or not 0 < self.overall_timeout <= 5:
            raise ValueError("capture acquisition and transport bounds are required")
        if not all((self.dsn, self.database_name, self.deployment_id, self.config_revision)):
            raise ValueError("capture probe target and configuration identity required")


async def _connect(dsn: str) -> Any:
    from psycopg import AsyncConnection
    return await AsyncConnection.connect(dsn, autocommit=True)


class CaptureHealthProbe:
    """No stale fallback, no database mutation, and no implicit role switching."""

    def __init__(self, config: ProbeConfig, *, connector: Callable = _connect,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._connector = connector
        self._clock = clock
        self._pid: int | None = None
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connection: Any = None
        self._healthy_at: float | None = None
        self._result: dict[str, Any] | None = None
        # A lock copied during fork can remain permanently held. Recreate the
        # process-local state in the child without operating on the parent's DB.
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self) -> None:
        self._pid = None
        self._lock = threading.Lock()
        self._loop = None
        self._connection = None
        self._healthy_at = None
        self._result = None

    def invalidate(self) -> None:
        """Call when routing/configuration changes or before migration fencing."""
        self._healthy_at = None
        self._result = None

    def _validate(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise CaptureUnhealthy("capture_health_shape")
        required = {
            "in_recovery": False,
            "database_name": self.config.database_name,
            "deployment_id": self.config.deployment_id,
            "zabalack_schema_version": self.config.schema_version,
        }
        if any(result.get(key) != value for key, value in required.items()):
            raise CaptureUnhealthy("capture_health_identity")
        if not result.get("checked_at"):
            raise CaptureUnhealthy("capture_health_time_missing")
        if result.get("config_revision") != self.config.config_revision:
            raise CaptureUnhealthy("capture_health_config_revision")
        if result.get("capture_disabled") is not False:
            raise CaptureUnhealthy("capture_health_disabled")
        # The SQL function checks the exact relation/function/tgtype/enabled
        # tuple and its expected definitions. A bool supplied by an arbitrary
        # endpoint is not enough: the configured probe identity is a G1 gate.
        if result.get("trigger_ok") is not True or result.get("functions_ok") is not True:
            raise CaptureUnhealthy("capture_health_schema")
        return dict(result)

    async def _discard(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            await asyncio.wait_for(connection.execute("ROLLBACK"), 0.1)
        except BaseException:
            pass
        try:
            await asyncio.wait_for(connection.close(), 0.1)
        except BaseException:
            pass

    async def _refresh(self) -> dict[str, Any]:
        try:
            if self._connection is None or self._connection.closed:
                self.invalidate()
                self._connection = await self._connector(self.config.dsn)
            await self._connection.execute("BEGIN READ ONLY")
            # Validated integer; deliberately a separate client command.
            await self._connection.execute(
                f"SET LOCAL statement_timeout = '{self.config.statement_timeout_ms}ms'")
            cursor = await self._connection.execute("SELECT zabalack.capture_health()")
            row = await cursor.fetchone()
            await self._connection.execute("COMMIT")
            return self._validate(row[0] if row else None)
        except BaseException:
            self.invalidate()
            await self._discard()
            raise

    def check(self, *, force: bool = False) -> dict[str, Any]:
        pid = os.getpid()
        if self._pid != pid:
            self._after_fork()
            self._pid = pid
        if not self._lock.acquire(timeout=self.config.acquisition_timeout):
            self.invalidate()
            raise CaptureUnhealthy("capture_health_acquisition_timeout")
        try:
            now = self._clock()
            if (not force and self._healthy_at is not None and self._result is not None
                    and 0 <= now - self._healthy_at < self.config.cache_seconds):
                return dict(self._result)
            self.invalidate()  # A failed refresh cannot retain an earlier success.
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            try:
                result = self._loop.run_until_complete(
                    asyncio.wait_for(self._refresh(), self.config.overall_timeout))
            except CaptureUnhealthy:
                raise
            except BaseException:
                raise CaptureUnhealthy("capture_health_unavailable") from None
            self._healthy_at = self._clock()
            self._result = result
            return dict(result)
        finally:
            self._lock.release()

    def close(self) -> None:
        """Serving-process teardown; never called by a read-only inventory."""
        if not self._lock.acquire(timeout=self.config.acquisition_timeout):
            self.invalidate()
            raise CaptureUnhealthy("capture_health_acquisition_timeout")
        try:
            self.invalidate()
            if self._loop is not None:
                self._loop.run_until_complete(self._discard())
                self._loop.close()
                self._loop = None
        finally:
            self._lock.release()


_registry_lock = threading.Lock()
_registered_probe: CaptureHealthProbe | None = None


def _registry_after_fork() -> None:
    global _registry_lock, _registered_probe
    # Do not close a duplicated parent's connection from the child. The probe's
    # own fork callback drops references, and the active registry starts fresh.
    _registry_lock = threading.Lock()
    _registered_probe = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_registry_after_fork)


def get_probe(config: ProbeConfig) -> CaptureHealthProbe:
    """At most one probe instance/connection for the process's active config."""
    global _registered_probe
    if not _registry_lock.acquire(timeout=config.acquisition_timeout):
        raise CaptureUnhealthy("capture_health_acquisition_timeout")
    try:
        if _registered_probe is None or _registered_probe.config != config:
            if _registered_probe is not None:
                _registered_probe.close()
            _registered_probe = CaptureHealthProbe(config)
        return _registered_probe
    finally:
        _registry_lock.release()
