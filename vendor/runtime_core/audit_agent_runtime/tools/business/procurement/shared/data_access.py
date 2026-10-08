from __future__ import annotations

from typing import Any, Callable

from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.persistence.db import build_engine
from audit_agent_runtime.tools.business.procurement.shared.sql_logging import log_sql_execution


_DEFAULT_SETTINGS: Settings | None = None


def default_connection_factory():
    global _DEFAULT_SETTINGS
    if _DEFAULT_SETTINGS is None:
        _DEFAULT_SETTINGS = Settings()
    engine = build_engine(_DEFAULT_SETTINGS)
    return engine.raw_connection()


def fetch_rows(
    connection_factory: Callable[[], Any],
    sql: str,
    params: list[Any],
) -> list[dict[str, Any]]:
    conn = connection_factory()
    try:
        cur = conn.cursor()
        try:
            log_sql_execution("_fetch_rows", sql, params)
            cur.execute(sql, params)
            records = cur.fetchall()
            columns = [item[0] for item in cur.description]
        finally:
            cur.close()
    finally:
        conn.close()
    return as_rows(columns, records)


def fetch_one_row(
    connection_factory: Callable[[], Any],
    sql: str,
    params: list[Any],
) -> dict[str, Any] | None:
    rows = fetch_rows(connection_factory, sql, params)
    if not rows:
        return None
    return rows[0]


def as_rows(columns, records):
    return [dict(zip(columns, record)) for record in records]
