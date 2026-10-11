"""
v3.3.0 migration - 国内期货账本 M1（fut_*）

对齐 scripts/sql/futures_ledger.sql：
1. 为 fut_* 集合创建与 DDL 一致的唯一约束和查询索引
2. 写入 P0 白名单种子：IF@CFFEX、IM@CFFEX、RB@SHFE

不修改 real_positions / paper_positions，不写入股票 RealPosition。
幂等：索引已存在则跳过；白名单按 fut_code upsert，不覆盖 created_at。

应用：
    python -m migrations.cli run --version 3.3.0
API 进程启动时也会执行未完成迁移。

PostgreSQL 镜像（可选）：
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/sql/futures_ledger.sql
"""

import logging
from datetime import datetime, timezone

from app.services.futures.schema import FUTURES_INDEXES, whitelist_seed_docs

logger = logging.getLogger("migrations.v3_3_0")

VERSION = "3.3.0"
DESCRIPTION = "期货账本 M1：fut_* 索引与白名单种子 IF/IM/RB（不改股票持仓）"


async def _safe_create_index(collection, keys, **kwargs):
    try:
        await collection.create_index(keys, **kwargs)
    except Exception as exc:
        err_code = getattr(exc, "code", None)
        if err_code in (85, 86):
            logger.info(
                "v3.3.0 索引已存在，跳过: %s.%s",
                collection.name,
                kwargs.get("name", keys),
            )
            return
        # 假库或已同名索引时，重复创建也不应让迁移失败。
        message = str(exc).lower()
        if "already exists" in message or "indexoptionsconflict" in message:
            logger.info("v3.3.0 索引已存在，跳过: %s", kwargs.get("name", keys))
            return
        raise


async def upgrade(db):
    for spec in FUTURES_INDEXES:
        collection = db[spec["collection"]]
        await _safe_create_index(
            collection,
            spec["keys"],
            name=spec["name"],
            unique=spec["unique"],
            background=True,
        )
        logger.info("v3.3.0 索引 %s.%s", spec["collection"], spec["name"])

    now = datetime.now(timezone.utc).isoformat()
    for doc in whitelist_seed_docs(now):
        await db["fut_product_whitelist"].update_one(
            {"fut_code": doc["fut_code"]},
            {
                "$set": {
                    "exchange": doc["exchange"],
                    "enabled": doc["enabled"],
                    "note": doc["note"],
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "fut_code": doc["fut_code"],
                    "created_at": now,
                },
            },
            upsert=True,
        )
    logger.info("v3.3.0 白名单已写入 IF@CFFEX IM@CFFEX RB@SHFE")


async def downgrade(db):
    """去掉本迁移创建的索引，并删除这三条种子。不删除其他 fut_* 文档。"""
    for spec in FUTURES_INDEXES:
        collection = db[spec["collection"]]
        try:
            await collection.drop_index(spec["name"])
        except Exception as exc:
            logger.info("v3.3.0 删除索引跳过 %s: %s", spec["name"], exc)
    await db["fut_product_whitelist"].delete_many({
        "fut_code": {"$in": ["IF", "IM", "RB"]},
    })
