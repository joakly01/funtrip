from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json


def normalize_json_value(value: object):
    if isinstance(value, dict):
        return {str(key): normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def safe_json_dumps(
    value: object,
    *,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    separators: tuple[str, str] | None = None,
) -> str:
    normalized = normalize_json_value(value)
    kwargs: dict[str, object] = {
        "ensure_ascii": ensure_ascii,
        "sort_keys": sort_keys,
        "default": str,
    }
    if separators is not None:
        kwargs["separators"] = separators
    return json.dumps(normalized, **kwargs)
