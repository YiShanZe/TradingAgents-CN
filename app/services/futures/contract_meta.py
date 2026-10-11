"""合约代码与乘数归一。不访问外部行情源。"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.services.futures.pnl import to_decimal


def product_of_ts_code(ts_code: str, known_products: Optional[set[str]] = None) -> str:
    """从 ts_code 取品种字母前缀。

    IF2410.CFX → IF，RB.SHF → RB。
    连续代码 IFL.CFX：若去掉末尾 L 后落在已知品种里，则归到该品种。
    """
    symbol = (ts_code or "").split(".", 1)[0].strip().upper()
    letters: list[str] = []
    for ch in symbol:
        if "A" <= ch <= "Z":
            letters.append(ch)
        else:
            break
    code = "".join(letters)
    if (
        known_products
        and code not in known_products
        and code.endswith("L")
        and code[:-1] in known_products
    ):
        return code[:-1]
    return code


def looks_like_month_contract(ts_code: str) -> bool:
    """月合约代码带数字（IF2410.CFX）。主力 IF.CFX / 连续 IFL.CFX 没有数字。"""
    symbol = (ts_code or "").split(".", 1)[0].strip().upper()
    return any(ch.isdigit() for ch in symbol)


def resolve_multiplier(raw_multiplier, raw_per_unit) -> tuple[Decimal, str]:
    """T2 待确认的入库规则：优先 multiplier，空或非正时用 per_unit。

    返回 (统一乘数, source_multiplier_field)。
    """
    if raw_multiplier is not None and str(raw_multiplier).strip() != "":
        mult = to_decimal(raw_multiplier)
        if mult > 0:
            return mult, "multiplier"
    if raw_per_unit is not None and str(raw_per_unit).strip() != "":
        per_unit = to_decimal(raw_per_unit)
        if per_unit > 0:
            return per_unit, "per_unit"
    raise ValueError("multiplier and per_unit are both missing or non-positive")
