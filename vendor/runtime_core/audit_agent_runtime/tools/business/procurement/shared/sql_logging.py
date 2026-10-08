from __future__ import annotations

import json
import logging
from typing import Any


logger = logging.getLogger("audit_agent_runtime.tools.business.procurement.bid_collusion.tools")


def truncate_log_text(text: str, *, max_length: int = 2000) -> str:
    if len(text) <= max_length:
        return text
    half = max_length // 2
    return f"{text[:half]}...<truncated {len(text) - max_length} chars>...{text[-half:]}"


def format_sql_params_for_log(params: Any, *, max_length: int = 2000) -> str:
    try:
        text = json.dumps(params, ensure_ascii=False, default=str, indent=2)
    except TypeError:
        text = repr(params)
    return truncate_log_text(text, max_length=max_length)


def sql_literal_for_log(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def render_runnable_sql_for_log(sql: str, params: Any) -> str:
    sql_text = str(sql)
    if params in (None, [], ()):
        return sql_text.replace("%%", "%")
    if not isinstance(params, (list, tuple)):
        return sql_text.replace("%%", "%")
    parts = sql_text.split("%s")
    if len(parts) != len(params) + 1:
        return sql_text.replace("%%", "%")
    rendered = parts[0]
    for index, value in enumerate(params):
        rendered += sql_literal_for_log(value)
        rendered += parts[index + 1]
    return rendered.replace("%%", "%")


def log_sql_execution(source: str, sql: str, params: Any = None) -> None:
    sql_text = truncate_log_text(str(sql), max_length=12000)
    runnable_sql = truncate_log_text(render_runnable_sql_for_log(sql, params), max_length=12000)
    logger.info(
        "[procurement.sql] source=%s\nsql:\n%s\nparams:\n%s\nrunnable_sql:\n%s",
        source,
        sql_text,
        format_sql_params_for_log(params, max_length=6000),
        runnable_sql,
    )


def strip_sql_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()
