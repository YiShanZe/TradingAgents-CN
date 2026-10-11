"""本地期货模拟记账（不开实盘、不自动下单）。

手数、保证金和盈亏都在本进程内计算，供 /api/paper 与持仓/复盘展示使用。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

# 开仓 / 平仓方向。buy/sell 沿用股票语义（开多/平多），short/cover 为开空/平空。
OPEN_LONG = "buy"
OPEN_SHORT = "short"
CLOSE_LONG = "sell"
CLOSE_SHORT = "cover"
FUTURES_SIDES = {OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT}
OPEN_SIDES = {OPEN_LONG, OPEN_SHORT}
CLOSE_SIDES = {CLOSE_LONG, CLOSE_SHORT}

DEFAULT_MARGIN_RATE = 0.12
DEFAULT_MULTIPLIER = 10

# 常见股指/国债合约乘数；商品默认 10（吨/手一类）。
_MULTIPLIERS = {
    "IF": 300,
    "IH": 300,
    "IC": 200,
    "IM": 200,
    "T": 10000,
    "TF": 10000,
    "TS": 20000,
    "TL": 10000,
}


def is_futures_code(code: str) -> bool:
    """与前端识别一致：1-2 个字母，可选最多 4 位数字，如 AP、RB、IC、AP601。"""
    raw = str(code or "").strip().upper()
    if "." in raw:
        raw = raw.split(".")[-1]
    return bool(re.match(r"^[A-Z]{1,2}\d{0,4}$", raw)) and not raw[:1].isdigit()


def product_code(code: str) -> str:
    raw = str(code or "").strip().upper()
    if "." in raw:
        raw = raw.split(".")[-1]
    match = re.match(r"^([A-Z]{1,2})", raw)
    return match.group(1) if match else raw


def futures_multiplier(code: str) -> int:
    return _MULTIPLIERS.get(product_code(code), DEFAULT_MULTIPLIER)


def side_label(side: str) -> str:
    return {
        OPEN_LONG: "开多",
        OPEN_SHORT: "开空",
        CLOSE_LONG: "平多",
        CLOSE_SHORT: "平空",
    }.get(side, side)


def is_opening(side: str) -> bool:
    return side in OPEN_SIDES


def is_closing(side: str) -> bool:
    return side in CLOSE_SIDES


def unrealized_pnl(
    last: Optional[float],
    long_qty: int,
    long_avg: float,
    short_qty: int,
    short_avg: float,
    multiplier: int,
) -> Optional[float]:
    """多头 (最新-均价)*手数*乘数；空头 (均价-最新)*手数*乘数。最新价缺失时返回 None。"""
    if last is None:
        return None
    price = float(last)
    mult = int(multiplier or 1)
    long_part = (price - float(long_avg or 0)) * int(long_qty or 0) * mult
    short_part = (float(short_avg or 0) - price) * int(short_qty or 0) * mult
    return round(long_part + short_part, 2)


def notional(price: float, qty: int, multiplier: int) -> float:
    return round(float(price) * int(qty) * int(multiplier or 1), 2)


def margin_required(price: float, qty: int, multiplier: int, rate: float = DEFAULT_MARGIN_RATE) -> float:
    return round(notional(price, qty, multiplier) * float(rate), 2)


def tq_order_gate() -> Dict[str, Any]:
    """真实下单闸门。本版本不实现实盘/自动下单，enabled 恒为 False。"""
    user = (os.getenv("TQ_USER") or os.getenv("TQSDK_USER") or "").strip()
    password = (os.getenv("TQ_PASSWORD") or os.getenv("TQSDK_PASSWORD") or "").strip()
    return {
        "enabled": False,
        "env_var": "TQ_REAL_ORDER_ENABLED",
        "env_raw": "0",
        "creds_loaded": bool(user and password),
    }


def tq_account_unavailable(reason: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": reason,
        "source": "local",
        "balance": None,
        "available": None,
        "margin": None,
        "frozen_margin": None,
        "float_profit": None,
        "close_profit": None,
        "risk_ratio": None,
    }


def enrich_position_row(position: Dict[str, Any], last: Optional[float]) -> Dict[str, Any]:
    """把持仓文档整理成前端行。空头单独持仓时不再把数量/均价显示成 0。"""
    market = position.get("market") or "CN"
    code = position.get("code")
    long_qty = int(position.get("quantity") or 0)
    short_qty = int(position.get("short_qty") or 0)
    long_avg = float(position.get("avg_cost") or 0)
    short_avg = float(position.get("short_avg") or 0)
    multiplier = int(position.get("multiplier") or (futures_multiplier(code) if market == "FUT" else 1))
    margin_long = float(position.get("margin_long") or 0)
    margin_short = float(position.get("margin_short") or 0)
    margin_used = float(position.get("margin_used") or (margin_long + margin_short))

    if market == "FUT":
        pnl = unrealized_pnl(last, long_qty, long_avg, short_qty, short_avg, multiplier)
        equity_value = round(margin_used + (pnl or 0.0), 2)
        market_value = round(float(last or 0) * (long_qty + short_qty) * multiplier, 2)
    else:
        pnl = None if last is None else round((float(last) - long_avg) * long_qty, 2)
        equity_value = round(float(last or 0) * long_qty, 2)
        market_value = equity_value

    if market == "FUT" and long_qty > 0 and short_qty > 0:
        position_side = "both"
    elif market == "FUT" and short_qty > 0 and long_qty <= 0:
        position_side = "short"
    else:
        position_side = "long"

    return {
        "code": code,
        "name": position.get("name") or "",
        "market": market,
        "currency": position.get("currency") or "CNY",
        "quantity": long_qty,
        "available_qty": position.get("available_qty", long_qty),
        "avg_cost": long_avg,
        "short_qty": short_qty,
        "short_avg": short_avg,
        "position_side": position_side,
        "multiplier": multiplier,
        "margin_used": round(margin_used, 2),
        "last_price": last,
        "market_value": market_value,
        "equity_value": equity_value,
        "unrealized_pnl": pnl,
    }


def count_trade_sides(side: Optional[str]) -> Dict[str, int]:
    """复盘统计：开多/开空计入开仓，平多/平空计入平仓。空头不再被当成卖出后丢掉。"""
    counts = {
        "buy_count": 0,
        "sell_count": 0,
        "short_count": 0,
        "cover_count": 0,
        "open_count": 0,
        "close_count": 0,
    }
    if side == OPEN_LONG:
        counts["buy_count"] = 1
        counts["open_count"] = 1
    elif side == OPEN_SHORT:
        counts["short_count"] = 1
        counts["open_count"] = 1
    elif side == CLOSE_SHORT:
        counts["cover_count"] = 1
        counts["close_count"] = 1
    elif side in CLOSE_SIDES or side == CLOSE_LONG:
        counts["sell_count"] = 1
        counts["close_count"] = 1
    return counts
