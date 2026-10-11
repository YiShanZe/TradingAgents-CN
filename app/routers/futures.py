"""期货只读 API（M1）。

路径前缀在 router_registry 中挂为 /api，本路由前缀 /futures。
响应 data.asset_class 恒为 futures。不写股票 RealPosition。
POST /orders 只做结构预检；POST /sync 不连接外部行情源。
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.database import get_mongo_db
from app.core.response import ok
from app.models.futures import FutOrderDraft
from app.routers.auth_db import get_current_user
from app.services.futures.errors import FuturesDataError
from app.services.futures.order_gate import evaluate_order
from app.services.futures.queries import (
    enabled_codes,
    get_contract,
    get_ledger,
    health,
    list_contracts,
    list_daily,
    list_mapping,
    list_margin_fee,
    list_orders,
    list_positions,
    list_sync_runs,
    list_trades,
    load_whitelist,
)
from app.services.futures.schema import ASSET_CLASS_FUTURES, ERROR_MESSAGES
from app.services.futures.sync import run_readonly_sync_stub
from app.utils.timezone import now_tz

router = APIRouter(prefix="/futures", tags=["futures"])


def _error_body(exc: FuturesDataError) -> dict:
    data = {
        "asset_class": ASSET_CLASS_FUTURES,
        "error_code": exc.code,
        **exc.details,
    }
    body = {
        "success": False,
        "data": data,
        "message": exc.message,
        "code": exc.status_code,
        "timestamp": now_tz().isoformat(),
    }
    # 禁止 NaN/Infinity 被序列化成非标准 JSON。
    json.dumps(body, allow_nan=False)
    return body


def _fail(exc: FuturesDataError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc))


@router.get("/health")
async def futures_health(
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await health(db))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/whitelist")
async def futures_whitelist(
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    rows = await load_whitelist(db)
    return ok({
        "asset_class": ASSET_CLASS_FUTURES,
        "items": rows,
        "count": len(rows),
    })


@router.get("/contracts")
async def futures_contracts(
    exchange: Optional[str] = Query(None),
    fut_code: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await list_contracts(
            db, exchange=exchange, fut_code=fut_code, q=q, limit=limit,
        ))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/contracts/{ts_code}")
async def futures_contract_detail(
    ts_code: str,
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await get_contract(db, ts_code))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/mapping")
async def futures_mapping(
    ts_code: Optional[str] = Query(None),
    trade_date: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await list_mapping(db, ts_code=ts_code, trade_date=trade_date, limit=limit))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/daily")
async def futures_daily(
    ts_code: str = Query(...),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await list_daily(db, ts_code=ts_code, start=start, end=end, limit=limit))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/margin-fee")
async def futures_margin_fee(
    ts_code: str = Query(...),
    trade_date: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await list_margin_fee(db, ts_code=ts_code, trade_date=trade_date, limit=limit))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/positions")
async def futures_positions(
    account_id: str = Query(...),
    session: Optional[str] = Query(None, description="intraday 用最新价；eod 用结算价"),
    limit: int = Query(200, ge=1, le=500),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await list_positions(db, account_id=account_id, session=session, limit=limit))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/ledger")
async def futures_ledger(
    account_id: str = Query(...),
    session: Optional[str] = Query(None),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await get_ledger(db, account_id=account_id, session=session))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/orders")
async def futures_orders(
    account_id: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await list_orders(db, account_id=account_id, limit=limit))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/trades")
async def futures_trades(
    account_id: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await list_trades(db, account_id=account_id, limit=limit))
    except FuturesDataError as exc:
        return _fail(exc)


@router.get("/sync-runs")
async def futures_sync_runs(
    limit: int = Query(20, ge=1, le=100),
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    try:
        return ok(await list_sync_runs(db, limit=limit))
    except FuturesDataError as exc:
        return _fail(exc)


@router.post("/sync")
async def futures_sync_stub(
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    """只读占位：不请求 Tushare，不写行情表。"""
    try:
        await run_readonly_sync_stub(db)
    except FuturesDataError as exc:
        return _fail(exc)
    return _fail(FuturesDataError("FUT_SYNC_STUB", 501))


@router.post("/orders")
async def futures_order_preview(
    draft: FutOrderDraft,
    db=Depends(get_mongo_db),
    user: dict = Depends(get_current_user),
):
    """结构预检。M1 不落库、不撮合、不发送 live。"""
    rows = await load_whitelist(db)
    result = evaluate_order(
        mode=draft.mode,
        ts_code=draft.ts_code,
        contract_type=draft.contract_type,
        order_type=draft.order_type,
        price=draft.price,
        volume=draft.volume,
        approval_id=draft.approval_id,
        direction=draft.direction,
        offset=draft.offset,
        account_id=draft.account_id,
        known_products=enabled_codes(rows),
    )
    data = {
        "asset_class": ASSET_CLASS_FUTURES,
        **result.as_dict(),
    }
    body = {
        "success": False,
        "data": data,
        "message": result.message or ERROR_MESSAGES.get(result.error_code, result.error_code),
        "code": result.http_status,
        "timestamp": now_tz().isoformat(),
    }
    json.dumps(body, allow_nan=False)
    return JSONResponse(status_code=result.http_status, content=body)
