"""期货浮动盈亏。

约定（指挥官已定）：
- 盘中 session=intraday：mark_price_type=last，用最新价
- 日终 session=eod：mark_price_type=settle，用结算价

多头：(mark - open_avg) × multiplier × volume
空头：(open_avg - mark) × multiplier × volume

手数为「手」。本模块只计算，不写库，也不改股票持仓。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

PNL_QUANT = Decimal("0.0001")
MarkType = Literal["last", "settle"]
Session = Literal["intraday", "eod"]


class MissingMarkError(ValueError):
    """计价价格缺失。调用方应返回 null + field_status，不要填 NaN。"""


def to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise TypeError("numeric value required")
    return Decimal(str(value))


def quantize_pnl(value: Decimal) -> Decimal:
    return value.quantize(PNL_QUANT, rounding=ROUND_HALF_UP)


def price_pnl(
    *,
    direction: str,
    mark: Decimal,
    open_avg: Decimal,
    multiplier: Decimal,
    volume: int,
) -> Decimal:
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    if volume < 0:
        raise ValueError("volume must be >= 0")
    if multiplier <= 0:
        raise ValueError("multiplier must be > 0")
    if volume == 0:
        return quantize_pnl(Decimal("0"))
    if direction == "long":
        diff = mark - open_avg
    else:
        diff = open_avg - mark
    return quantize_pnl(diff * multiplier * Decimal(volume))


@dataclass(frozen=True)
class MarkResult:
    mark_price: Decimal
    mark_price_type: MarkType
    unrealized_pnl: Decimal
    session: Session

    def as_strings(self) -> dict:
        return {
            "mark_price": format(self.mark_price, "f"),
            "mark_price_type": self.mark_price_type,
            "computed_unrealized_pnl": format(self.unrealized_pnl, "f"),
            "session": self.session,
            "persisted": False,
        }


def mark_unrealized_pnl(
    *,
    direction: str,
    open_avg_price,
    multiplier,
    volume: int,
    session: Session,
    last_price=None,
    settle_price=None,
) -> MarkResult:
    """按盘中/日终选择计价价格并计算浮动盈亏。不落库。"""
    if session == "intraday":
        if last_price is None:
            raise MissingMarkError("intraday mark requires last price")
        mark = to_decimal(last_price)
        kind: MarkType = "last"
    elif session == "eod":
        if settle_price is None:
            raise MissingMarkError("eod mark requires settle price")
        mark = to_decimal(settle_price)
        kind = "settle"
    else:
        raise ValueError("session must be intraday or eod")
    pnl = price_pnl(
        direction=direction,
        mark=mark,
        open_avg=to_decimal(open_avg_price),
        multiplier=to_decimal(multiplier),
        volume=int(volume),
    )
    return MarkResult(
        mark_price=mark,
        mark_price_type=kind,
        unrealized_pnl=pnl,
        session=session,
    )


def rollup_unrealized(pnls: list[Optional[Decimal]]) -> Optional[Decimal]:
    """账户级浮动盈亏合计。任一缺失则返回 None，避免把缺价当成 0。"""
    if any(p is None for p in pnls):
        return None
    total = Decimal("0")
    for pnl in pnls:
        total += pnl or Decimal("0")
    return quantize_pnl(total)


def estimate_margin(*, price_ref, multiplier, lots, margin_rate) -> Decimal:
    """保证金占用近似：price_ref × multiplier × lots × margin_rate。

    M2 撮合冻结保证金时使用。M1 仅提供公式，不改可用资金。
    """
    margin = (
        to_decimal(price_ref)
        * to_decimal(multiplier)
        * to_decimal(lots)
        * to_decimal(margin_rate)
    )
    if margin < 0:
        raise ValueError("margin inputs must be non-negative")
    return quantize_pnl(margin)
