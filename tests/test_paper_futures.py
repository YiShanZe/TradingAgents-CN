"""期货模拟记账与复盘方向统计。不连接行情，也不发起实盘下单。"""
from app.services.paper_futures import (
    CLOSE_SHORT,
    OPEN_SHORT,
    count_trade_sides,
    enrich_position_row,
    futures_multiplier,
    is_futures_code,
    margin_required,
    tq_order_gate,
    unrealized_pnl,
)


def test_futures_code_detection():
    assert is_futures_code("AP")
    assert is_futures_code("RB2610")
    assert is_futures_code("SHFE.rb2610")
    assert not is_futures_code("600519")
    assert not is_futures_code("AAPL")


def test_short_pnl_uses_short_qty_not_long_qty():
    # 空 2 手，均价 5000，最新 4900，乘数 10 → 盈利 2000
    pnl = unrealized_pnl(4900, 0, 0, 2, 5000, 10)
    assert pnl == 2000
    # 价格上涨则空头亏损
    assert unrealized_pnl(5100, 0, 0, 2, 5000, 10) == -2000
    # 零盈亏
    assert unrealized_pnl(5000, 0, 0, 2, 5000, 10) == 0


def test_enrich_short_position_keeps_qty_and_avg():
    row = enrich_position_row(
        {
            "code": "AP601",
            "name": "苹果",
            "market": "FUT",
            "currency": "CNY",
            "quantity": 0,
            "avg_cost": 0,
            "short_qty": 2,
            "short_avg": 5000,
            "multiplier": 10,
            "margin_used": 1200,
        },
        4900,
    )
    assert row["quantity"] == 0
    assert row["short_qty"] == 2
    assert row["short_avg"] == 5000
    assert row["position_side"] == "short"
    assert row["unrealized_pnl"] == 2000
    assert row["avg_cost"] == 0


def test_index_multiplier_and_margin():
    assert futures_multiplier("IF2412") == 300
    assert futures_multiplier("AP") == 10
    assert margin_required(5000, 1, 10, 0.12) == 6000


def test_review_counts_open_short_as_open_not_sell():
    opened = count_trade_sides(OPEN_SHORT)
    closed = count_trade_sides(CLOSE_SHORT)
    assert opened["open_count"] == 1
    assert opened["buy_count"] == 0
    assert opened["sell_count"] == 0
    assert closed["close_count"] == 1
    assert closed["sell_count"] == 0


def test_tq_gate_never_enables_live_orders():
    gate = tq_order_gate()
    assert gate["enabled"] is False
    assert gate["env_raw"] == "0"


def test_frontend_tq_client_uses_imported_api_client():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "frontend/src/api/paper.ts").read_text(encoding="utf-8")
    assert "async getTqAccount()" in source
    assert "async getTqPositions()" in source
    assert "async getTqOrderStatus()" in source
    assert "request.get" not in source
    assert source.count("ApiClient.get") >= 3
