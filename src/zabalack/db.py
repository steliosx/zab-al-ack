"""Bounded PostgreSQL client for the restricted, versioned SQL function facade.

Every call is its own transaction. This is safe with PgBouncer transaction pools;
no connection-level state, advisory session locks, LISTEN, or DDL is used here.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, Protocol


class Database(Protocol):
    async def call(self, operation: str, payload: dict[str, Any] | None = None) -> Any: ...
    async def operator(self, operation: str, payload: dict[str, Any]) -> Any: ...


class Postgres:
    def __init__(self, dsn: str, *, connect_timeout: int = 3, statement_timeout_ms: int = 3000,
                 overall_timeout_seconds: float = 8.0) -> None:
        self._dsn = dsn
        self.connect_timeout = connect_timeout
        self.statement_timeout_ms = statement_timeout_ms
        self.overall_timeout_seconds = overall_timeout_seconds

    async def _call(self, function: str, operation: str, payload: dict[str, Any]) -> Any:
        import psycopg
        from psycopg.types.json import Jsonb
        # Function is chosen internally, never interpolated from an operator argument.
        statement = {"worker": "SELECT zabalack.worker_call(%s, %s)",
                     "operator": "SELECT zabalack.operator_call(%s, %s)"}[function]
        conn = None
        try:
            async with asyncio.timeout(self.overall_timeout_seconds):
                conn = await psycopg.AsyncConnection.connect(
                    self._dsn, connect_timeout=self.connect_timeout, autocommit=True)
                async with conn.transaction():
                    await conn.execute("SET LOCAL statement_timeout = %s" % int(self.statement_timeout_ms))
                    cursor = await conn.execute(statement, (operation, Jsonb(payload)))
                    row = await cursor.fetchone()
                    return row[0] if row else None
        finally:
            if conn is not None:
                with suppress(Exception):
                    await asyncio.wait_for(conn.close(), timeout=0.5)

    async def call(self, operation: str, payload: dict[str, Any] | None = None) -> Any:
        return await self._call("worker", operation, payload or {})

    async def operator(self, operation: str, payload: dict[str, Any]) -> Any:
        return await self._call("operator", operation, payload)
