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
                 overall_timeout_seconds: float = 8.0,
                 operation_budgets: dict[str, float] | None = None) -> None:
        self._dsn = dsn
        self.connect_timeout = connect_timeout
        self.statement_timeout_ms = statement_timeout_ms
        self.overall_timeout_seconds = overall_timeout_seconds
        # Statement budgets in seconds for the few worker operations whose cost
        # is set by the installed table rather than by an index — the retirement
        # sweep's scan of public.alerts. Every other call keeps the short default.
        self.operation_budgets = {name: float(seconds) for name, seconds in (operation_budgets or {}).items()}

    def budget_for(self, function: str, operation: str) -> tuple[int, float]:
        """(statement_timeout in ms, overall deadline in seconds) for one call."""
        budget = self.operation_budgets.get(operation) if function == "worker" else None
        if budget is None:
            return int(self.statement_timeout_ms), self.overall_timeout_seconds
        return int(budget * 1000), budget + self.connect_timeout + 1

    async def _call(self, function: str, operation: str, payload: dict[str, Any]) -> Any:
        import psycopg
        from psycopg.types.json import Jsonb
        # Function is chosen internally, never interpolated from an operator argument.
        statement = {"worker": "SELECT zabalack.worker_call(%s, %s)",
                     "operator": "SELECT zabalack.operator_call(%s, %s)"}[function]
        statement_ms, overall = self.budget_for(function, operation)
        conn = None
        try:
            async with asyncio.timeout(overall):
                conn = await psycopg.AsyncConnection.connect(
                    self._dsn, connect_timeout=self.connect_timeout, autocommit=True)
                async with conn.transaction():
                    await conn.execute("SET LOCAL statement_timeout = %s" % statement_ms)
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
