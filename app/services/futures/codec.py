"""把 Mongo 文档收成期货 API 字段，去掉股票专属列，并把 NaN 收成 null。"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from app.services.futures.schema import STOCK_ONLY_FIELDS

try:
    from bson import Decimal128, ObjectId
except Exception:  # pragma: no cover - bson 始终随 pymongo 存在
    Decimal128 = ()  # type: ignore
    ObjectId = ()  # type: ignore


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if Decimal128 and isinstance(value, Decimal128):
        return format(value.to_decimal(), "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if ObjectId and isinstance(value, ObjectId):
        return str(value)
    return value


def public_doc(doc: Optional[dict], fields: tuple[str, ...]) -> dict:
    source = doc or {}
    out = {}
    for key in fields:
        if key in STOCK_ONLY_FIELDS:
            continue
        out[key] = jsonable(source.get(key))
    return out


def field_status(doc: dict, keys: tuple[str, ...]) -> dict:
    """源未提供的字段标 source_missing，展示为「—（源未提供）」，而不是 NaN。"""
    status = {}
    for key in keys:
        value = doc.get(key)
        missing = value is None or (isinstance(value, float) and math.isnan(value))
        status[key] = {
            "status": "source_missing" if missing else "ok",
            "display": "—（源未提供）" if missing else "ok",
        }
    return status


def strip_stock_fields(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key not in STOCK_ONLY_FIELDS}
