"""下单前结构校验。

不撮合、不落 fut_order、不写 fut_position，更不写 real_positions。
live 一律拒绝。paper / live_shadow 只返回「留待 M2」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app.services.futures.contract_meta import looks_like_month_contract, product_of_ts_code
from app.services.futures.matching import live_trading_enabled
from app.services.futures.schema import ERROR_MESSAGES


@dataclass
class GateResult:
    accepted: bool
    error_code: str
    message: str
    http_status: int
    persisted: bool = False
    dispatched: bool = False
    shadow_payload: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "error_code": self.error_code,
            "message": self.message,
            "persisted": self.persisted,
            "dispatched": self.dispatched,
            "shadow_payload": self.shadow_payload,
            "live_trading_enabled": live_trading_enabled(),
            "matching_engine": "todo_m2",
        }


def evaluate_order(
    *,
    mode: str,
    ts_code: str,
    contract_type: Optional[str],
    order_type: str,
    price,
    volume: int,
    approval_id: Optional[str],
    direction: str,
    offset: str,
    account_id: str,
    known_products: Optional[set[str]] = None,
) -> GateResult:
    if volume <= 0:
        return _reject("FUT_BAD_REQUEST", "手数必须为正整数")
    if order_type == "limit" and price is None:
        return _reject("FUT_BAD_REQUEST", "限价单必须提供价格")
    if price is not None:
        try:
            number = float(price)
        except (TypeError, ValueError):
            return _reject("FUT_BAD_REQUEST", "价格无效")
        if not math.isfinite(number) or number <= 0:
            return _reject("FUT_BAD_REQUEST", "价格必须为正数")

    month = contract_type == "month" or (
        contract_type is None and looks_like_month_contract(ts_code)
    )
    if contract_type in ("main", "continuous") or not month:
        return _reject("FUT_MAIN_NOT_TRADABLE", ERROR_MESSAGES["FUT_MAIN_NOT_TRADABLE"])

    if known_products is not None:
        product = product_of_ts_code(ts_code, known_products)
        if product not in known_products:
            return _reject("FUT_NOT_IN_WHITELIST", ERROR_MESSAGES["FUT_NOT_IN_WHITELIST"])

    if mode == "live":
        # live 在 M1 永远关闭。缺审批与已带审批都拒绝，且不发送。
        extra = ""
        if not approval_id:
            extra = "（缺少 approval_id）"
        return _reject(
            "FUT_LIVE_DISABLED",
            ERROR_MESSAGES["FUT_LIVE_DISABLED"] + extra,
        )
    if mode not in ("paper", "live_shadow"):
        return _reject("FUT_BAD_REQUEST", "mode 必须是 paper、live_shadow 或 live")

    payload = {
        "account_id": account_id,
        "mode": mode,
        "ts_code": ts_code,
        "direction": direction,
        "offset": offset,
        "order_type": order_type,
        "price": None if price is None else str(price),
        "volume": volume,
        "approval_id": approval_id,
        "dispatched": False,
    }
    shadow = payload if mode == "live_shadow" else None
    return GateResult(
        accepted=True,
        error_code="FUT_MATCH_TODO",
        message=ERROR_MESSAGES["FUT_MATCH_TODO"],
        http_status=501,
        persisted=False,
        dispatched=False,
        shadow_payload=shadow,
    )


def _reject(code: str, message: str, status: int = 400) -> GateResult:
    return GateResult(
        accepted=False,
        error_code=code,
        message=message,
        http_status=status,
        persisted=False,
        dispatched=False,
    )
