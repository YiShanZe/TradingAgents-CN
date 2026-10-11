"""期货只读查询。不拉外部源，不写持仓。"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.services.futures.codec import field_status, public_doc
from app.services.futures.contract_meta import product_of_ts_code
from app.services.futures.errors import FuturesDataError
from app.services.futures.pnl import MissingMarkError, mark_unrealized_pnl, rollup_unrealized, to_decimal
from app.services.futures.schema import (
    ASSET_CLASS_FUTURES,
    CONTRACT_FIELDS,
    CONTRACT_OPTIONAL_FIELDS,
    DAILY_FIELDS,
    LEDGER_FIELDS,
    MAPPING_FIELDS,
    MARGIN_FEE_FIELDS,
    ORDER_FIELDS,
    PNL_POLICY,
    POSITION_FIELDS,
    SYNC_RUN_FIELDS,
    TRADE_FIELDS,
    WHITELIST_FIELDS,
    builtin_whitelist,
)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_iso_date(value: Optional[str], field: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if not _DATE.match(value):
        raise FuturesDataError(
            "FUT_BAD_REQUEST",
            400,
            {"field": field},
            message=f"{field} 日期格式应为 YYYY-MM-DD",
        )
    return value


def _with_class(payload: dict) -> dict:
    payload["asset_class"] = ASSET_CLASS_FUTURES
    return payload


async def _to_list(coll, query: dict, sort=None, limit: int = 200) -> list[dict]:
    cursor = coll.find(query)
    if sort:
        cursor = cursor.sort(sort)
    if limit:
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=limit or None)
    return list(docs or [])


async def load_whitelist(db) -> list[dict]:
    docs = await _to_list(
        db["fut_product_whitelist"],
        {},
        sort=[("fut_code", 1)],
        limit=100,
    )
    if not docs:
        return [public_doc(row, WHITELIST_FIELDS) for row in builtin_whitelist()]
    return [public_doc(row, WHITELIST_FIELDS) for row in docs]


def enabled_codes(rows: list[dict]) -> set[str]:
    return {
        str(row["fut_code"]).upper()
        for row in rows
        if row.get("enabled", True) and row.get("fut_code")
    }


def _require_product(ts_code: str, codes: set[str]) -> str:
    product = product_of_ts_code(ts_code, codes)
    if product not in codes:
        raise FuturesDataError("FUT_NOT_IN_WHITELIST", 403)
    return product


async def list_contracts(
    db,
    *,
    exchange: Optional[str] = None,
    fut_code: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
) -> dict:
    rows = await load_whitelist(db)
    codes = enabled_codes(rows)
    query: dict[str, Any] = {"fut_code": {"$in": sorted(codes)}}
    if fut_code:
        wanted = fut_code.strip().upper()
        if wanted not in codes:
            raise FuturesDataError("FUT_NOT_IN_WHITELIST", 403, {"fut_code": wanted})
        query["fut_code"] = wanted
    if exchange:
        query["exchange"] = exchange.strip().upper()
    if q:
        pattern = re.escape(q.strip())
        query["$or"] = [
            {"ts_code": {"$regex": pattern, "$options": "i"}},
            {"symbol": {"$regex": pattern, "$options": "i"}},
            {"name": {"$regex": pattern, "$options": "i"}},
        ]
    docs = await _to_list(db["fut_contract"], query, sort=[("ts_code", 1)], limit=limit)
    items = [_contract_item(doc) for doc in docs]
    if not items and (fut_code or q or exchange):
        raise FuturesDataError("FUT_CONTRACT_NOT_FOUND", 404)
    payload = _with_class({
        "items": items,
        "count": len(items),
    })
    if not items:
        payload["empty_reason"] = "白名单内暂无已同步合约"
    return payload


async def get_contract(db, ts_code: str) -> dict:
    code = (ts_code or "").strip().upper()
    if not code:
        raise FuturesDataError("FUT_BAD_REQUEST", 400, message="缺少 ts_code")
    rows = await load_whitelist(db)
    _require_product(code, enabled_codes(rows))
    doc = await db["fut_contract"].find_one({"ts_code": code})
    if not doc:
        raise FuturesDataError("FUT_CONTRACT_NOT_FOUND", 404, {"ts_code": code})
    return _with_class(_contract_item(doc))


def _contract_item(doc: dict) -> dict:
    item = public_doc(doc, CONTRACT_FIELDS)
    item["field_status"] = field_status(item, CONTRACT_OPTIONAL_FIELDS)
    return item


async def list_mapping(
    db,
    *,
    ts_code: Optional[str] = None,
    trade_date: Optional[str] = None,
    limit: int = 100,
) -> dict:
    trade_date = parse_iso_date(trade_date, "trade_date")
    rows = await load_whitelist(db)
    codes = enabled_codes(rows)
    query: dict[str, Any] = {}
    if ts_code:
        code = ts_code.strip().upper()
        _require_product(code, codes)
        query["ts_code"] = code
    if trade_date:
        query["trade_date"] = trade_date
    docs = await _to_list(db["fut_mapping"], query, sort=[("trade_date", -1)], limit=limit)
    if ts_code:
        docs = [
            doc for doc in docs
            if product_of_ts_code(str(doc.get("mapping_ts_code") or ""), codes) in codes
            or product_of_ts_code(str(doc.get("ts_code") or ""), codes) in codes
        ]
    else:
        docs = [
            doc for doc in docs
            if product_of_ts_code(str(doc.get("ts_code") or ""), codes) in codes
        ]
    items = [public_doc(doc, MAPPING_FIELDS) for doc in docs]
    if ts_code and not items:
        raise FuturesDataError("FUT_MAPPING_MISSING", 404, {"ts_code": ts_code.strip().upper()})
    payload = _with_class({"items": items, "count": len(items)})
    if not items:
        payload["empty_reason"] = "暂无主力映射，无法解析月合约"
    return payload


async def list_daily(
    db,
    *,
    ts_code: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 200,
) -> dict:
    code = (ts_code or "").strip().upper()
    start = parse_iso_date(start, "start")
    end = parse_iso_date(end, "end")
    rows = await load_whitelist(db)
    _require_product(code, enabled_codes(rows))
    query: dict[str, Any] = {"ts_code": code}
    if start or end:
        bounds: dict[str, str] = {}
        if start:
            bounds["$gte"] = start
        if end:
            bounds["$lte"] = end
        query["trade_date"] = bounds
    docs = await _to_list(
        db["fut_daily"],
        query,
        sort=[("trade_date", -1)],
        limit=limit,
    )
    if not docs:
        raise FuturesDataError("FUT_NO_QUOTE", 404, {"ts_code": code})
    return _with_class({
        "ts_code": code,
        "items": [public_doc(doc, DAILY_FIELDS) for doc in docs],
        "count": len(docs),
        "volume_unit": "lot",
        "open_interest_unit": "lot",
    })


async def list_margin_fee(
    db,
    *,
    ts_code: str,
    trade_date: Optional[str] = None,
    limit: int = 50,
) -> dict:
    code = (ts_code or "").strip().upper()
    trade_date = parse_iso_date(trade_date, "trade_date")
    rows = await load_whitelist(db)
    _require_product(code, enabled_codes(rows))
    query: dict[str, Any] = {"ts_code": code}
    if trade_date:
        query["trade_date"] = trade_date
    docs = await _to_list(
        db["fut_margin_fee"],
        query,
        sort=[("trade_date", -1)],
        limit=limit,
    )
    payload = _with_class({
        "ts_code": code,
        "items": [public_doc(doc, MARGIN_FEE_FIELDS) for doc in docs],
        "count": len(docs),
    })
    if not docs:
        payload["empty_reason"] = "暂无保证金/手续费快照"
    return payload


async def _multiplier_for(db, ts_code: str):
    doc = await db["fut_contract"].find_one({"ts_code": ts_code})
    if not doc or doc.get("multiplier") is None:
        return None
    try:
        return to_decimal(doc["multiplier"])
    except (TypeError, ValueError, ArithmeticError):
        return None


def _position_pnl_block(doc: dict, multiplier, session: Optional[str]) -> dict:
    block = {
        "policy": PNL_POLICY["policy"],
        "intraday_mark": PNL_POLICY["intraday"],
        "eod_mark": PNL_POLICY["eod"],
        "session": session,
        "stored_unrealized_pnl": public_doc(doc, ("unrealized_pnl",)).get("unrealized_pnl"),
        "computed_unrealized_pnl": None,
        "mark_price": None,
        "mark_price_type": None,
        "persisted": False,
        "field_status": {},
    }
    if session not in ("intraday", "eod"):
        return block
    if multiplier is None:
        block["field_status"]["multiplier"] = {
            "status": "source_missing",
            "display": "—（源未提供）",
        }
        return block
    last_price = doc.get("mark_price") if doc.get("mark_price_type") == "last" else None
    if session == "intraday" and doc.get("mark_price_type") == "last":
        last_price = doc.get("mark_price")
    settle_price = doc.get("last_settle")
    if settle_price is None and doc.get("mark_price_type") == "settle":
        settle_price = doc.get("mark_price")
    try:
        marked = mark_unrealized_pnl(
            direction=doc.get("direction"),
            open_avg_price=doc.get("open_avg_price"),
            multiplier=multiplier,
            volume=int(doc.get("volume") or 0),
            session=session,  # type: ignore[arg-type]
            last_price=last_price,
            settle_price=settle_price,
        )
    except (MissingMarkError, TypeError, ValueError, ArithmeticError) as exc:
        missing_key = "mark_price" if session == "intraday" else "last_settle"
        block["field_status"][missing_key] = {
            "status": "source_missing",
            "display": "—（源未提供）",
        }
        block["message"] = str(exc)
        return block
    view = marked.as_strings()
    block.update(view)
    return block


async def list_positions(
    db,
    *,
    account_id: str,
    session: Optional[str] = None,
    limit: int = 200,
) -> dict:
    account_id = (account_id or "").strip()
    if not account_id:
        raise FuturesDataError("FUT_BAD_REQUEST", 400, message="缺少 account_id")
    if session not in (None, "", "intraday", "eod"):
        raise FuturesDataError("FUT_BAD_REQUEST", 400, message="session 必须是 intraday 或 eod")
    session = session or None
    docs = await _to_list(
        db["fut_position"],
        {"account_id": account_id},
        sort=[("ts_code", 1)],
        limit=limit,
    )
    rows = await load_whitelist(db)
    codes = enabled_codes(rows)
    items = []
    computed = []
    for doc in docs:
        multiplier = await _multiplier_for(db, str(doc.get("ts_code") or ""))
        pnl = _position_pnl_block(doc, multiplier, session)
        product = doc.get("product_code") or product_of_ts_code(str(doc.get("ts_code") or ""), codes)
        items.append({
            "position": public_doc(doc, POSITION_FIELDS),
            "in_whitelist": str(product).upper() in codes if product else False,
            "pnl": pnl,
        })
        if session and pnl.get("computed_unrealized_pnl") is not None:
            computed.append(to_decimal(pnl["computed_unrealized_pnl"]))
        elif session:
            computed.append(None)
    rollup = None
    if session and computed:
        total = rollup_unrealized(computed)
        rollup = None if total is None else format(total, "f")
    payload = _with_class({
        "account_id": account_id,
        "items": items,
        "count": len(items),
        "pnl_policy": PNL_POLICY,
        "unrealized_pnl_rollup": rollup,
        "rollup_persisted": False,
    })
    if not items:
        payload["empty_reason"] = "暂无期货持仓"
    return payload


async def get_ledger(db, *, account_id: str, session: Optional[str] = None) -> dict:
    account_id = (account_id or "").strip()
    if not account_id:
        raise FuturesDataError("FUT_BAD_REQUEST", 400, message="缺少 account_id")
    doc = await db["fut_account_ledger"].find_one({
        "account_id": account_id,
        "asset_class": ASSET_CLASS_FUTURES,
    })
    positions = await list_positions(db, account_id=account_id, session=session)
    payload = _with_class({
        "account_id": account_id,
        "ledger": public_doc(doc, LEDGER_FIELDS) if doc else None,
        "pnl_policy": PNL_POLICY,
        "unrealized_pnl_rollup": positions.get("unrealized_pnl_rollup"),
        "rollup_persisted": False,
    })
    if doc is None:
        payload["empty_reason"] = "尚无期货资金账户"
    return payload


async def list_orders(db, *, account_id: str, limit: int = 100) -> dict:
    account_id = (account_id or "").strip()
    if not account_id:
        raise FuturesDataError("FUT_BAD_REQUEST", 400, message="缺少 account_id")
    docs = await _to_list(
        db["fut_order"],
        {"account_id": account_id},
        sort=[("created_at", -1)],
        limit=limit,
    )
    payload = _with_class({
        "account_id": account_id,
        "items": [public_doc(doc, ORDER_FIELDS) for doc in docs],
        "count": len(docs),
        "live_trading_enabled": False,
    })
    if not docs:
        payload["empty_reason"] = "暂无期货订单"
    return payload


async def list_trades(db, *, account_id: str, limit: int = 100) -> dict:
    account_id = (account_id or "").strip()
    if not account_id:
        raise FuturesDataError("FUT_BAD_REQUEST", 400, message="缺少 account_id")
    docs = await _to_list(
        db["fut_trade"],
        {"account_id": account_id},
        sort=[("trade_time", -1)],
        limit=limit,
    )
    payload = _with_class({
        "account_id": account_id,
        "items": [public_doc(doc, TRADE_FIELDS) for doc in docs],
        "count": len(docs),
        "volume_unit": "lot",
    })
    if not docs:
        payload["empty_reason"] = "暂无期货成交"
    return payload


async def list_sync_runs(db, *, limit: int = 20) -> dict:
    docs = await _to_list(
        db["fut_sync_run"],
        {},
        sort=[("started_at", -1)],
        limit=limit,
    )
    return _with_class({
        "items": [public_doc(doc, SYNC_RUN_FIELDS) for doc in docs],
        "count": len(docs),
    })


async def health(db) -> dict:
    rows = await load_whitelist(db)
    latest = await _to_list(db["fut_sync_run"], {}, sort=[("started_at", -1)], limit=1)
    success = await _to_list(
        db["fut_sync_run"],
        {"status": "success"},
        sort=[("started_at", -1)],
        limit=1,
    )
    from app.services.futures.sync import tushare_token_configured

    return _with_class({
        "source": "tushare",
        "token_configured": tushare_token_configured(),
        "live_trading_enabled": False,
        "matching_engine": "todo_m2",
        "pnl_policy": PNL_POLICY,
        "whitelist": rows,
        "last_sync_run": public_doc(latest[0], SYNC_RUN_FIELDS) if latest else None,
        "last_success_sync": public_doc(success[0], SYNC_RUN_FIELDS) if success else None,
        "note": "M1 不拉取外部行情，也不发起真实下单",
    })
