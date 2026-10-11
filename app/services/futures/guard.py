"""期货账本写入护栏：不得落到股票持仓集合。"""

from __future__ import annotations

from app.services.futures.schema import STOCK_POSITION_COLLECTIONS


def assert_futures_collection(name: str) -> str:
    """M1 写入只允许 fut_*。real_positions / paper_positions 直接拒绝。"""
    if name in STOCK_POSITION_COLLECTIONS:
        raise RuntimeError(
            f"refusing to use stock position collection {name} for futures ledger"
        )
    if not name.startswith("fut_"):
        raise RuntimeError(f"futures ledger collection must be fut_*: {name}")
    return name


async def insert_futures_doc(db, collection: str, doc: dict) -> None:
    assert_futures_collection(collection)
    await db[collection].insert_one(doc)
