"""期货撮合引擎占位。

M2 才实现：
- 保证金不足拒单，且不改持仓与资金
- 开平成交、今昨仓、手续费
- 状态机 pending_new → submitted → partial_filled → filled / canceled / rejected
- 终态不可回退
- 成交写入 fut_trade，持仓只写 fut_position

M1 调用 submit 会直接失败，避免误把订单做成成交。
"""

from __future__ import annotations


class FuturesMatchingEngine:
    def submit(self, draft: dict) -> None:
        raise NotImplementedError(
            "M2: futures matching engine is not implemented; "
            "M1 must not fill orders or mutate fut_position / real_positions"
        )


def live_trading_enabled() -> bool:
    """硬约束：未经批准不发起真实下单。M1 不读取环境开关。"""
    return False
