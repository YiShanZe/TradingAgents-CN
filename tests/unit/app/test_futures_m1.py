"""M1 期货账本：模型、盈亏约定、白名单种子、只读 API。不连接 Mongo。"""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.futures import (
    FutAccountLedger,
    FutContract,
    FutOrder,
    FutOrderDraft,
    FutPosition,
)
from app.models.portfolio import RealPosition
from app.routers import futures as futures_router
from app.services.futures.contract_meta import (
    looks_like_month_contract,
    product_of_ts_code,
    resolve_multiplier,
)
from app.services.futures.guard import assert_futures_collection
from app.services.futures.order_gate import evaluate_order
from app.services.futures.matching import FuturesMatchingEngine, live_trading_enabled
from app.services.futures.pnl import (
    estimate_margin,
    mark_unrealized_pnl,
    price_pnl,
)
from app.services.futures.schema import (
    ASSET_CLASS_FUTURES,
    FUTURES_COLLECTIONS,
    FUTURES_INDEXES,
    P0_WHITELIST_ROWS,
)
from migrations.v3_3_0 import DESCRIPTION, VERSION, downgrade, upgrade


def _match(doc, query) -> bool:
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$regex" in expected:
                flags = re.I if "i" in expected.get("$options", "") else 0
                text = "" if actual is None else str(actual)
                if re.search(expected["$regex"], text, flags) is None:
                    return False
            continue
        if actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, keys, direction=None):
        if isinstance(keys, str):
            keys = [(keys, direction or 1)]
        for key, way in reversed(list(keys)):
            self.docs.sort(
                key=lambda doc, field=key: (doc.get(field) is None, str(doc.get(field))),
                reverse=way < 0,
            )
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs[:length])


class FakeCollection:
    def __init__(self, name, db):
        self.name = name
        self.db = db
        self.docs = []

    def find(self, query=None, projection=None):
        matched = [dict(doc) for doc in self.docs if _match(doc, query or {})]
        return FakeCursor(matched)

    async def find_one(self, query=None, projection=None):
        docs = await self.find(query).to_list(1)
        return docs[0] if docs else None

    async def insert_one(self, doc):
        stored = dict(doc)
        self.docs.append(stored)
        self.db.inserts.append((self.name, stored))
        return stored

    async def update_one(self, filt, update, upsert=False):
        self.db.updates.append((self.name, filt, update, upsert))
        target = next((doc for doc in self.docs if _match(doc, filt)), None)
        if target is None:
            if not upsert:
                return
            target = {}
            target.update(update.get("$setOnInsert") or {})
            target.update(update.get("$set") or {})
            self.docs.append(target)
            return
        target.update(update.get("$set") or {})

    async def delete_many(self, filt):
        self.docs = [doc for doc in self.docs if not _match(doc, filt)]

    async def create_index(self, keys, **kwargs):
        self.db.indexes.append((self.name, list(keys), kwargs))

    async def drop_index(self, name):
        self.db.dropped.append((self.name, name))


class FakeDB:
    def __init__(self):
        self.cols = {}
        self.inserts = []
        self.updates = []
        self.indexes = []
        self.dropped = []

    def __getitem__(self, name):
        if name not in self.cols:
            self.cols[name] = FakeCollection(name, self)
        return self.cols[name]


def _body(result):
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body.decode())
    return 200, result


def test_real_position_is_not_extended_with_futures_fields():
    fields = set(RealPosition.model_fields)
    forbidden = {
        "direction",
        "offset",
        "margin_occupied",
        "volume_today",
        "mark_price_type",
        "multiplier",
        "fut_code",
        "asset_class",
        "last_settle",
    }
    assert forbidden.isdisjoint(fields)
    assert "code" in fields and "quantity" in fields


def test_futures_models_enforce_ledger_checks():
    with pytest.raises(ValidationError):
        FutAccountLedger(account_id="a", asset_class="equity")
    with pytest.raises(ValidationError):
        FutAccountLedger(account_id="a", asset_class="future")
    ledger = FutAccountLedger(account_id="a")
    assert ledger.asset_class == ASSET_CLASS_FUTURES

    with pytest.raises(ValidationError):
        FutContract(
            ts_code="IF2410.CFX",
            symbol="IF2410",
            fut_code="IF",
            exchange="CFFEX",
            multiplier=Decimal("0"),
            contract_type="month",
        )
    with pytest.raises(ValidationError):
        FutOrder(
            order_id="o1",
            account_id="a",
            mode="paper",
            ts_code="IF2410.CFX",
            direction="long",
            offset="open",
            volume=1,
            filled_volume=2,
            status="submitted",
        )
    with pytest.raises(ValidationError):
        FutOrder(
            order_id="o2",
            account_id="a",
            mode="live",
            ts_code="IF2410.CFX",
            direction="long",
            offset="open",
            volume=1,
            status="rejected",
        )
    with pytest.raises(ValidationError):
        FutPosition(
            account_id="a",
            ts_code="IF2410.CFX",
            direction="long",
            volume=-1,
        )


def test_pnl_intraday_last_and_eod_settle():
    long_last = mark_unrealized_pnl(
        direction="long",
        open_avg_price=4000,
        multiplier=300,
        volume=2,
        session="intraday",
        last_price=4010,
        settle_price=4005,
    )
    assert long_last.mark_price_type == "last"
    assert long_last.unrealized_pnl == Decimal("6000.0000")

    short_last = price_pnl(
        direction="short",
        mark=Decimal("4010"),
        open_avg=Decimal("4000"),
        multiplier=Decimal("300"),
        volume=2,
    )
    assert short_last == Decimal("-6000.0000")

    eod = mark_unrealized_pnl(
        direction="long",
        open_avg_price=4000,
        multiplier=300,
        volume=2,
        session="eod",
        last_price=4010,
        settle_price=4005,
    )
    assert eod.mark_price_type == "settle"
    assert eod.unrealized_pnl == Decimal("3000.0000")

    rb = mark_unrealized_pnl(
        direction="long",
        open_avg_price=3500,
        multiplier=10,
        volume=1,
        session="intraday",
        last_price=3520,
    )
    assert rb.unrealized_pnl == Decimal("200.0000")
    assert estimate_margin(
        price_ref=4000, multiplier=300, lots=2, margin_rate="0.12",
    ) == Decimal("288000.0000")


def test_multiplier_resolution_and_month_code():
    mult, source = resolve_multiplier(None, "10")
    assert mult == Decimal("10") and source == "per_unit"
    mult, source = resolve_multiplier("300", "10")
    assert mult == Decimal("300") and source == "multiplier"
    with pytest.raises(ValueError):
        resolve_multiplier(None, None)

    known = {"IF", "IM", "RB"}
    assert product_of_ts_code("IF2410.CFX", known) == "IF"
    assert product_of_ts_code("IFL.CFX", known) == "IF"
    assert product_of_ts_code("RB.SHF", known) == "RB"
    assert product_of_ts_code("CU2410.SHF", known) == "CU"
    assert looks_like_month_contract("IF2410.CFX")
    assert not looks_like_month_contract("IF.CFX")
    assert not looks_like_month_contract("IFL.CFX")


def test_guard_and_matching_engine_do_not_touch_stock_positions():
    with pytest.raises(RuntimeError):
        assert_futures_collection("real_positions")
    with pytest.raises(RuntimeError):
        assert_futures_collection("paper_positions")
    assert assert_futures_collection("fut_position") == "fut_position"
    assert live_trading_enabled() is False
    with pytest.raises(NotImplementedError):
        FuturesMatchingEngine().submit({"ts_code": "IF2410.CFX"})
    rejected = evaluate_order(
        mode="paper",
        ts_code="IF2410.CFX",
        contract_type="month",
        order_type="limit",
        price=float("nan"),
        volume=1,
        approval_id=None,
        direction="long",
        offset="open",
        account_id="a",
    )
    assert rejected.accepted is False
    assert rejected.persisted is False


def test_schema_covers_ddl_tables_and_p0_seed():
    assert set(FUTURES_COLLECTIONS) == {
        "fut_product_whitelist",
        "fut_contract",
        "fut_mapping",
        "fut_daily",
        "fut_margin_fee",
        "fut_account_ledger",
        "fut_order",
        "fut_trade",
        "fut_position",
        "fut_sync_run",
    }
    assert P0_WHITELIST_ROWS == (
        ("IF", "CFFEX", "P0 定稿：沪深300股指"),
        ("IM", "CFFEX", "P0 定稿：中证1000股指"),
        ("RB", "SHFE", "P0 定稿：螺纹钢"),
    )
    unique = {
        spec["name"]: spec["unique"]
        for spec in FUTURES_INDEXES
    }
    assert unique["uq_fut_contract_ts_code"] is True
    assert unique["uq_fut_mapping"] is True
    assert unique["uq_fut_position"] is True
    assert unique["uq_fut_account_ledger"] is True
    assert VERSION == "3.3.0"
    assert "RealPosition" in DESCRIPTION or "股票" in DESCRIPTION


def test_migration_seeds_whitelist_without_stock_collections():
    db = FakeDB()

    async def _run():
        await upgrade(db)
        await downgrade(db)

    asyncio.run(_run())
    seeded = [call[1]["fut_code"] for call in db.updates]
    assert seeded == ["IF", "IM", "RB"]
    exchanges = {call[1]["fut_code"]: call[2]["$set"]["exchange"] for call in db.updates}
    assert exchanges == {"IF": "CFFEX", "IM": "CFFEX", "RB": "SHFE"}
    assert all(call[3] is True for call in db.updates)
    assert "real_positions" not in db.cols
    assert "paper_positions" not in db.cols
    names = {spec[2]["name"] for spec in db.indexes}
    assert "uq_fut_contract_ts_code" in names
    assert db["fut_product_whitelist"].docs == []
    assert db.dropped


def test_readonly_api_whitelist_contract_quote_and_nan_guard():
    db = FakeDB()
    db["fut_contract"].docs.append({
        "ts_code": "IF2410.CFX",
        "symbol": "IF2410",
        "fut_code": "IF",
        "exchange": "CFFEX",
        "name": "沪深300股指2410",
        "multiplier": "300",
        "contract_type": "month",
        "status": "listed",
        "price_tick": float("nan"),
        "pe": 12.5,
        "total_share": 100,
    })

    async def _run():
        whitelist = await futures_router.futures_whitelist(db=db, user={"id": "u"})
        blocked = await futures_router.futures_contracts(
            exchange=None, fut_code="CU", q=None, limit=100, db=db, user={"id": "u"},
        )
        listed = await futures_router.futures_contracts(
            exchange=None, fut_code="IF", q=None, limit=100, db=db, user={"id": "u"},
        )
        detail = await futures_router.futures_contract_detail(
            "IF2410.CFX", db=db, user={"id": "u"},
        )
        missing = await futures_router.futures_contract_detail(
            "IF9999.CFX", db=db, user={"id": "u"},
        )
        no_quote = await futures_router.futures_daily(
            ts_code="IF2410.CFX", start=None, end=None, limit=20, db=db, user={"id": "u"},
        )
        no_map = await futures_router.futures_mapping(
            ts_code="IF.CFX", trade_date=None, limit=20, db=db, user={"id": "u"},
        )
        return whitelist, blocked, listed, detail, missing, no_quote, no_map

    whitelist, blocked, listed, detail, missing, no_quote, no_map = asyncio.run(_run())
    status, body = _body(whitelist)
    assert status == 200
    codes = {(item["fut_code"], item["exchange"]) for item in body["data"]["items"]}
    assert codes == {("IF", "CFFEX"), ("IM", "CFFEX"), ("RB", "SHFE")}
    assert body["data"]["asset_class"] == "futures"

    status, body = _body(blocked)
    assert status == 403
    assert body["data"]["error_code"] == "FUT_NOT_IN_WHITELIST"
    assert body["message"] == "该品种未纳入 P0 白名单"
    assert "NaN" not in json.dumps(body, allow_nan=False)

    status, body = _body(listed)
    assert status == 200
    assert body["data"]["items"][0]["fut_code"] == "IF"
    assert "pe" not in body["data"]["items"][0]
    assert body["data"]["items"][0]["field_status"]["price_tick"]["display"] == "—（源未提供）"

    status, body = _body(detail)
    blob = json.dumps(body, allow_nan=False)
    assert "NaN" not in blob
    assert "pe" not in body["data"]
    assert "total_share" not in body["data"]
    assert body["data"]["asset_class"] == "futures"

    status, body = _body(missing)
    assert status == 404 and body["data"]["error_code"] == "FUT_CONTRACT_NOT_FOUND"
    status, body = _body(no_quote)
    assert status == 404 and body["data"]["error_code"] == "FUT_NO_QUOTE"
    assert body["message"] == "暂无行情数据"
    status, body = _body(no_map)
    assert status == 404 and body["data"]["error_code"] == "FUT_MAPPING_MISSING"


def test_positions_recompute_pnl_without_writing():
    db = FakeDB()
    db["fut_contract"].docs.append({
        "ts_code": "IF2410.CFX",
        "fut_code": "IF",
        "exchange": "CFFEX",
        "multiplier": "300",
    })
    db["fut_position"].docs.append({
        "account_id": "paper-1",
        "ts_code": "IF2410.CFX",
        "product_code": "IF",
        "direction": "long",
        "volume": 2,
        "open_avg_price": "4000",
        "unrealized_pnl": "0",
        "mark_price": "4010",
        "mark_price_type": "last",
        "last_settle": "4005",
    })
    db["fut_account_ledger"].docs.extend([
        {"account_id": "paper-1", "asset_class": "equity", "balance": "1"},
        {
            "account_id": "paper-1",
            "asset_class": "futures",
            "balance": "1000000",
            "available": "900000",
            "unrealized_pnl": "0",
        },
    ])

    async def _run():
        intraday = await futures_router.futures_positions(
            account_id="paper-1", session="intraday", limit=20, db=db, user={"id": "u"},
        )
        eod = await futures_router.futures_positions(
            account_id="paper-1", session="eod", limit=20, db=db, user={"id": "u"},
        )
        ledger = await futures_router.futures_ledger(
            account_id="paper-1", session="eod", db=db, user={"id": "u"},
        )
        return intraday, eod, ledger

    intraday, eod, ledger = asyncio.run(_run())
    _, intra_body = _body(intraday)
    _, eod_body = _body(eod)
    _, ledger_body = _body(ledger)
    assert intra_body["data"]["items"][0]["pnl"]["computed_unrealized_pnl"] == "6000.0000"
    assert intra_body["data"]["items"][0]["pnl"]["mark_price_type"] == "last"
    assert intra_body["data"]["items"][0]["pnl"]["persisted"] is False
    assert eod_body["data"]["items"][0]["pnl"]["computed_unrealized_pnl"] == "3000.0000"
    assert eod_body["data"]["items"][0]["pnl"]["mark_price_type"] == "settle"
    assert eod_body["data"]["unrealized_pnl_rollup"] == "3000.0000"
    assert ledger_body["data"]["ledger"]["asset_class"] == "futures"
    assert ledger_body["data"]["ledger"]["balance"] == "1000000"
    assert db["fut_position"].docs[0]["unrealized_pnl"] == "0"
    assert db.inserts == []


def test_sync_stub_and_order_gate_do_not_persist_orders(monkeypatch):
    from app.core.config import settings

    db = FakeDB()

    async def _sync(token):
        monkeypatch.setattr(settings, "TUSHARE_TOKEN", token)
        return await futures_router.futures_sync_stub(db=db, user={"id": "u"})

    auth = asyncio.run(_sync(""))
    status, body = _body(auth)
    assert status == 503
    assert body["data"]["error_code"] == "FUT_SRC_AUTH"
    assert body["message"] == "期货数据源未授权，请配置 Token"
    assert db.inserts[-1][0] == "fut_sync_run"
    assert db.inserts[-1][1]["status"] == "failed"

    stub = asyncio.run(_sync("super-secret-token"))
    status, body = _body(stub)
    blob = json.dumps(body, allow_nan=False)
    assert status == 501
    assert body["data"]["error_code"] == "FUT_SYNC_STUB"
    assert "super-secret-token" not in blob
    assert all(name == "fut_sync_run" for name, _ in db.inserts)

    health = asyncio.run(futures_router.futures_health(db=db, user={"id": "u"}))
    _, health_body = _body(health)
    assert health_body["data"]["asset_class"] == "futures"
    assert health_body["data"]["pnl_policy"]["intraday"] == "last"
    assert health_body["data"]["pnl_policy"]["eod"] == "settle"
    assert health_body["data"]["live_trading_enabled"] is False
    assert health_body["data"]["matching_engine"] == "todo_m2"
    assert health_body["data"]["token_configured"] is True
    assert "super-secret-token" not in json.dumps(health_body)

    async def _orders():
        paper = await futures_router.futures_order_preview(
            FutOrderDraft(
                account_id="paper-1",
                mode="paper",
                ts_code="IF2410.CFX",
                direction="long",
                offset="open",
                price=Decimal("4000"),
                volume=1,
            ),
            db=db,
            user={"id": "u"},
        )
        shadow = await futures_router.futures_order_preview(
            FutOrderDraft(
                account_id="paper-1",
                mode="live_shadow",
                ts_code="RB2410.SHF",
                direction="short",
                offset="open",
                order_type="market",
                volume=1,
            ),
            db=db,
            user={"id": "u"},
        )
        live = await futures_router.futures_order_preview(
            FutOrderDraft(
                account_id="paper-1",
                mode="live",
                ts_code="IF2410.CFX",
                direction="long",
                offset="open",
                price=Decimal("4000"),
                volume=1,
                approval_id="apr-1",
            ),
            db=db,
            user={"id": "u"},
        )
        main = await futures_router.futures_order_preview(
            FutOrderDraft(
                account_id="paper-1",
                mode="paper",
                ts_code="IF.CFX",
                direction="long",
                offset="open",
                price=Decimal("4000"),
                volume=1,
            ),
            db=db,
            user={"id": "u"},
        )
        return paper, shadow, live, main

    paper, shadow, live, main = asyncio.run(_orders())
    status, body = _body(paper)
    assert status == 501
    assert body["data"]["persisted"] is False
    assert body["data"]["dispatched"] is False
    assert body["data"]["error_code"] == "FUT_MATCH_TODO"
    status, body = _body(shadow)
    assert status == 501
    assert body["data"]["shadow_payload"]["dispatched"] is False
    assert body["data"]["shadow_payload"]["ts_code"] == "RB2410.SHF"
    status, body = _body(live)
    assert status == 400
    assert body["data"]["error_code"] == "FUT_LIVE_DISABLED"
    assert body["data"]["dispatched"] is False
    status, body = _body(main)
    assert status == 400
    assert body["data"]["error_code"] == "FUT_MAIN_NOT_TRADABLE"
    assert all(name == "fut_sync_run" for name, _ in db.inserts)
    assert "real_positions" not in db.cols
    assert "fut_order" not in db.cols
    assert "fut_position" not in db.cols


def test_sql_ddl_contains_tables_and_seed():
    text = open("scripts/sql/futures_ledger.sql", encoding="utf-8").read()
    for name in FUTURES_COLLECTIONS:
        assert f"CREATE TABLE IF NOT EXISTS {name}" in text
    assert "('IF', 'CFFEX'" in text
    assert "('IM', 'CFFEX'" in text
    assert "('RB', 'SHFE'" in text
    assert "mark_price_type IN ('last', 'settle')" in text
    assert "asset_class = 'futures'" in text
