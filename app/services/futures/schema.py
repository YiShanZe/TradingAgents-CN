"""期货账本集合、索引与 P0 白名单（对齐 scripts/sql/futures_ledger.sql）。

运行时库是 MongoDB。下列名称与 PostgreSQL DDL 中的 fut_* 表一一对应。
股票持仓集合 real_positions / paper_positions 不在此清单中。
"""

from __future__ import annotations

ASSET_CLASS_FUTURES = "futures"

# 盘中用最新价，日终用结算价。指挥官约定，见 DDL fut_position / fut_account_ledger。
PNL_POLICY = {
    "intraday": "last",
    "eod": "settle",
    "policy": "intraday_last_eod_settle",
}

# T1 待确认：广期所后缀不在此枚举中强制。ts_code 保持自由字符串。
GFEX_SUFFIX_NOTE = "unconfirmed: Tushare .GFE vs some AKShare docs .GFEX"

FUTURES_COLLECTIONS = (
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
)

# 禁止期货写入的股票持仓集合。
STOCK_POSITION_COLLECTIONS = frozenset({"real_positions", "paper_positions"})

# (fut_code, exchange, note) — P0 定稿 IF@CFFEX / IM@CFFEX / RB@SHFE
P0_WHITELIST_ROWS = (
    ("IF", "CFFEX", "P0 定稿：沪深300股指"),
    ("IM", "CFFEX", "P0 定稿：中证1000股指"),
    ("RB", "SHFE", "P0 定稿：螺纹钢"),
)

ERROR_MESSAGES = {
    "FUT_SRC_AUTH": "期货数据源未授权，请配置 Token",
    "FUT_SRC_PERM": "当前积分无法拉取期货接口",
    "FUT_SRC_DOWN": "行情源暂时不可用，请稍后重试",
    "FUT_NOT_IN_WHITELIST": "该品种未纳入 P0 白名单",
    "FUT_CONTRACT_NOT_FOUND": "未找到合约",
    "FUT_NO_QUOTE": "暂无行情数据",
    "FUT_MAPPING_MISSING": "暂无主力映射，无法解析月合约",
    "FUT_SYNC_STUB": "M1 只读占位：未连接外部行情源，同步与撮合留待 M2",
    "FUT_MATCH_TODO": "M2 撮合引擎尚未实现，订单未落库、未发送",
    "FUT_LIVE_DISABLED": "未经批准不发起真实下单；live 默认关闭",
    "FUT_MAIN_NOT_TRADABLE": "持仓与下单必须使用月合约，不能直接使用主力/连续代码",
    "FUT_BAD_REQUEST": "请求参数无效",
}

# Mongo 访问路径。键与 DDL 索引/唯一约束对齐；started_at 为健康检查附加索引。
FUTURES_INDEXES = (
    {
        "collection": "fut_product_whitelist",
        "keys": [("fut_code", 1)],
        "unique": True,
        "name": "uq_fut_product_whitelist_fut_code",
    },
    {
        "collection": "fut_contract",
        "keys": [("ts_code", 1)],
        "unique": True,
        "name": "uq_fut_contract_ts_code",
    },
    {
        "collection": "fut_contract",
        "keys": [("fut_code", 1), ("exchange", 1)],
        "unique": False,
        "name": "idx_fut_contract_fut_exch",
    },
    {
        "collection": "fut_contract",
        "keys": [("contract_type", 1), ("status", 1)],
        "unique": False,
        "name": "idx_fut_contract_type_status",
    },
    {
        "collection": "fut_contract",
        "keys": [("fut_code", 1), ("delivery_month", 1)],
        "unique": False,
        "name": "idx_fut_contract_delivery",
    },
    {
        "collection": "fut_mapping",
        "keys": [("ts_code", 1), ("trade_date", 1), ("map_kind", 1)],
        "unique": True,
        "name": "uq_fut_mapping",
    },
    {
        "collection": "fut_mapping",
        "keys": [("trade_date", 1)],
        "unique": False,
        "name": "idx_fut_mapping_date",
    },
    {
        "collection": "fut_mapping",
        "keys": [("mapping_ts_code", 1), ("trade_date", 1)],
        "unique": False,
        "name": "idx_fut_mapping_month",
    },
    {
        "collection": "fut_daily",
        "keys": [("ts_code", 1), ("trade_date", 1)],
        "unique": True,
        "name": "uq_fut_daily_ts_date",
    },
    {
        "collection": "fut_daily",
        "keys": [("trade_date", 1)],
        "unique": False,
        "name": "idx_fut_daily_date",
    },
    {
        "collection": "fut_margin_fee",
        "keys": [("ts_code", 1), ("trade_date", 1)],
        "unique": True,
        "name": "uq_fut_margin_fee_ts_date",
    },
    {
        "collection": "fut_margin_fee",
        "keys": [("trade_date", 1)],
        "unique": False,
        "name": "idx_fut_margin_fee_date",
    },
    {
        "collection": "fut_account_ledger",
        "keys": [("account_id", 1), ("asset_class", 1)],
        "unique": True,
        "name": "uq_fut_account_ledger",
    },
    {
        "collection": "fut_order",
        "keys": [("order_id", 1)],
        "unique": True,
        "name": "uq_fut_order_id",
    },
    {
        "collection": "fut_order",
        "keys": [("account_id", 1), ("created_at", -1)],
        "unique": False,
        "name": "idx_fut_order_account_time",
    },
    {
        "collection": "fut_order",
        "keys": [("status", 1)],
        "unique": False,
        "name": "idx_fut_order_status",
    },
    {
        "collection": "fut_order",
        "keys": [("ts_code", 1), ("created_at", -1)],
        "unique": False,
        "name": "idx_fut_order_ts_code",
    },
    {
        "collection": "fut_trade",
        "keys": [("trade_id", 1)],
        "unique": True,
        "name": "uq_fut_trade_id",
    },
    {
        "collection": "fut_trade",
        "keys": [("order_id", 1)],
        "unique": False,
        "name": "idx_fut_trade_order",
    },
    {
        "collection": "fut_trade",
        "keys": [("account_id", 1), ("trade_time", -1)],
        "unique": False,
        "name": "idx_fut_trade_account_time",
    },
    {
        "collection": "fut_position",
        "keys": [("account_id", 1), ("ts_code", 1), ("direction", 1)],
        "unique": True,
        "name": "uq_fut_position",
    },
    {
        "collection": "fut_position",
        "keys": [("account_id", 1)],
        "unique": False,
        "name": "idx_fut_position_account",
    },
    {
        "collection": "fut_position",
        "keys": [("product_code", 1)],
        "unique": False,
        "name": "idx_fut_position_product",
    },
    {
        "collection": "fut_sync_run",
        "keys": [("started_at", -1)],
        "unique": False,
        "name": "idx_fut_sync_run_started",
    },
)

CONTRACT_FIELDS = (
    "contract_id",
    "ts_code",
    "symbol",
    "fut_code",
    "exchange",
    "name",
    "delivery_month",
    "list_date",
    "delist_date",
    "last_delivery_date",
    "multiplier",
    "source_multiplier_field",
    "raw_multiplier",
    "raw_per_unit",
    "trade_unit",
    "quote_unit",
    "price_tick",
    "trade_time_desc",
    "contract_type",
    "is_main",
    "currency",
    "status",
    "source",
    "created_at",
    "updated_at",
)

# 源可能缺失、需要 field_status 的合约字段。禁止用 NaN 占位。
CONTRACT_OPTIONAL_FIELDS = (
    "name",
    "delivery_month",
    "list_date",
    "delist_date",
    "last_delivery_date",
    "trade_unit",
    "quote_unit",
    "price_tick",
    "trade_time_desc",
    "is_main",
)

MAPPING_FIELDS = (
    "map_id",
    "ts_code",
    "trade_date",
    "mapping_ts_code",
    "map_kind",
    "source",
    "rule_version",
    "created_at",
)

DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "settle",
    "pre_close",
    "pre_settle",
    "change_close",
    "change_settle",
    "volume",
    "amount",
    "amount_unit_raw",
    "open_interest",
    "oi_chg",
    "up_limit",
    "down_limit",
    "source",
    "synced_at",
)

MARGIN_FEE_FIELDS = (
    "ts_code",
    "trade_date",
    "multiplier",
    "price_tick",
    "long_margin_rate",
    "short_margin_rate",
    "long_hedge_margin_rate",
    "short_hedge_margin_rate",
    "m_ratio_min",
    "open_fee_rate",
    "open_fee_per_lot",
    "close_fee_rate",
    "close_fee_per_lot",
    "close_today_fee_rate",
    "close_today_fee_per_lot",
    "delivery_fee",
    "source",
    "synced_at",
)

LEDGER_FIELDS = (
    "ledger_id",
    "account_id",
    "asset_class",
    "balance",
    "available",
    "margin_used",
    "frozen_margin",
    "frozen_fee",
    "realized_pnl",
    "unrealized_pnl",
    "currency",
    "updated_at",
)

ORDER_FIELDS = (
    "order_id",
    "account_id",
    "mode",
    "ts_code",
    "direction",
    "offset",
    "order_type",
    "price",
    "volume",
    "filled_volume",
    "status",
    "frozen_margin",
    "frozen_fee",
    "reject_reason",
    "broker_order_id",
    "approval_id",
    "created_at",
    "updated_at",
)

TRADE_FIELDS = (
    "trade_id",
    "order_id",
    "account_id",
    "ts_code",
    "direction",
    "offset",
    "price",
    "volume",
    "fee",
    "realized_pnl",
    "mode",
    "trade_time",
)

POSITION_FIELDS = (
    "position_id",
    "account_id",
    "ts_code",
    "product_code",
    "direction",
    "volume",
    "volume_today",
    "volume_yesterday",
    "open_avg_price",
    "position_avg_price",
    "margin_occupied",
    "unrealized_pnl",
    "realized_pnl_cum",
    "mark_price",
    "mark_price_type",
    "last_settle",
    "updated_at",
)

SYNC_RUN_FIELDS = (
    "run_id",
    "job_name",
    "status",
    "error_code",
    "message",
    "started_at",
    "finished_at",
)

WHITELIST_FIELDS = (
    "fut_code",
    "exchange",
    "enabled",
    "note",
    "created_at",
    "updated_at",
)

# 股票详情里常见、期货响应里必须去掉的字段。
STOCK_ONLY_FIELDS = frozenset({
    "pe",
    "pe_ttm",
    "pb",
    "pb_mrq",
    "total_share",
    "float_share",
    "total_mv",
    "circ_mv",
    "roe",
    "eps",
})


def whitelist_seed_docs(now_iso: str) -> list[dict]:
    """幂等种子。updated_at 由迁移覆盖；created_at 仅插入时写入。"""
    docs = []
    for fut_code, exchange, note in P0_WHITELIST_ROWS:
        docs.append({
            "fut_code": fut_code,
            "exchange": exchange,
            "enabled": True,
            "note": note,
            "created_at": now_iso,
            "updated_at": now_iso,
        })
    return docs


def builtin_whitelist() -> list[dict]:
    """库中尚无种子时的只读回退。时间戳留空，表示尚未落库。"""
    docs = []
    for fut_code, exchange, note in P0_WHITELIST_ROWS:
        docs.append({
            "fut_code": fut_code,
            "exchange": exchange,
            "enabled": True,
            "note": note,
            "created_at": None,
            "updated_at": None,
        })
    return docs
