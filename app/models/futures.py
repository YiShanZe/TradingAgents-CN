"""期货账本 Pydantic 模型，字段对齐 scripts/sql/futures_ledger.sql。

这些模型只描述 fut_* 集合。不要把字段并进
app.models.portfolio.RealPosition（real_positions，股票持仓）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.timezone import now_tz

ContractType = Literal["month", "main", "continuous"]
ContractStatus = Literal["listed", "delisted", "suspended"]
MapKind = Literal["main", "continuous"]
FutDirection = Literal["long", "short"]
FutOffset = Literal["open", "close", "close_today", "close_yesterday"]
FutOrderType = Literal["limit", "market"]
FutOrderStatus = Literal[
    "pending_new",
    "submitted",
    "partial_filled",
    "filled",
    "canceled",
    "rejected",
]
FutMode = Literal["paper", "live_shadow", "live"]
MarkPriceType = Literal["last", "settle"]
SyncStatus = Literal["running", "success", "failed"]


def _new_id() -> str:
    return str(uuid4())


class FutDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    def to_doc(self) -> dict:
        """JSON 安全文档：日期为 ISO 字符串，Decimal 为十进制字符串。"""
        return self.model_dump(mode="json")


class FutProductWhitelist(FutDocument):
    """P0 品种白名单。同步只拉 enabled=true。"""

    fut_code: str = Field(..., min_length=1, max_length=16)
    exchange: str = Field(..., min_length=1, max_length=16)
    enabled: bool = True
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=now_tz)
    updated_at: datetime = Field(default_factory=now_tz)

    @field_validator("fut_code", "exchange")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class FutContract(FutDocument):
    """期货合约元数据。与 A 股标的表分离。"""

    contract_id: str = Field(default_factory=_new_id)
    ts_code: str
    symbol: str
    fut_code: str
    exchange: str
    name: Optional[str] = None
    delivery_month: Optional[str] = None
    list_date: Optional[str] = None
    delist_date: Optional[str] = None
    last_delivery_date: Optional[str] = None
    multiplier: Decimal
    source_multiplier_field: Optional[str] = None
    raw_multiplier: Optional[Decimal] = None
    raw_per_unit: Optional[Decimal] = None
    trade_unit: Optional[str] = None
    quote_unit: Optional[str] = None
    price_tick: Optional[Decimal] = None
    trade_time_desc: Optional[str] = None
    contract_type: ContractType
    is_main: Optional[bool] = None
    currency: str = "CNY"
    status: ContractStatus = "listed"
    source: str = "tushare"
    created_at: datetime = Field(default_factory=now_tz)
    updated_at: datetime = Field(default_factory=now_tz)

    @field_validator("multiplier")
    @classmethod
    def _positive_multiplier(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("multiplier must be > 0")
        return value

    @field_validator("fut_code", "exchange", "ts_code", "symbol")
    @classmethod
    def _strip_upper(cls, value: str) -> str:
        return value.strip().upper()


class FutMapping(FutDocument):
    """主力/连续代码按日映射到可交易月合约。持仓必须挂 mapping_ts_code。"""

    map_id: str = Field(default_factory=_new_id)
    ts_code: str
    trade_date: str
    mapping_ts_code: str
    map_kind: MapKind
    source: str = "tushare"
    rule_version: Optional[str] = None
    created_at: datetime = Field(default_factory=now_tz)


class FutDaily(FutDocument):
    """期货日线。volume / open_interest 单位为手。amount 入库为元。"""

    ts_code: str
    trade_date: str
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    settle: Optional[Decimal] = None
    pre_close: Optional[Decimal] = None
    pre_settle: Optional[Decimal] = None
    change_close: Optional[Decimal] = None
    change_settle: Optional[Decimal] = None
    volume: Optional[int] = None
    amount: Optional[Decimal] = None
    amount_unit_raw: Optional[str] = None
    open_interest: Optional[int] = None
    oi_chg: Optional[int] = None
    up_limit: Optional[Decimal] = None
    down_limit: Optional[Decimal] = None
    source: str = "tushare"
    synced_at: datetime = Field(default_factory=now_tz)


class FutMarginFee(FutDocument):
    """合约×日的保证金率与手续费快照。"""

    ts_code: str
    trade_date: str
    multiplier: Decimal
    price_tick: Optional[Decimal] = None
    long_margin_rate: Optional[Decimal] = None
    short_margin_rate: Optional[Decimal] = None
    long_hedge_margin_rate: Optional[Decimal] = None
    short_hedge_margin_rate: Optional[Decimal] = None
    m_ratio_min: Optional[Decimal] = None
    open_fee_rate: Optional[Decimal] = None
    open_fee_per_lot: Optional[Decimal] = None
    close_fee_rate: Optional[Decimal] = None
    close_fee_per_lot: Optional[Decimal] = None
    close_today_fee_rate: Optional[Decimal] = None
    close_today_fee_per_lot: Optional[Decimal] = None
    delivery_fee: Optional[Decimal] = None
    source: str = "tushare"
    synced_at: datetime = Field(default_factory=now_tz)

    @field_validator("multiplier")
    @classmethod
    def _positive_multiplier(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("multiplier must be > 0")
        return value


class FutAccountLedger(FutDocument):
    """期货资金视图。asset_class 固定 futures，与 A 股资金隔离。

    unrealized_pnl：盘中可按最新价汇总；日终用 settle 重算落地（M2 落库）。
    """

    ledger_id: str = Field(default_factory=_new_id)
    account_id: str
    asset_class: Literal["futures"] = "futures"
    balance: Decimal = Decimal("0")
    available: Decimal = Decimal("0")
    margin_used: Decimal = Decimal("0")
    frozen_margin: Decimal = Decimal("0")
    frozen_fee: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    currency: str = "CNY"
    updated_at: datetime = Field(default_factory=now_tz)


class FutOrder(FutDocument):
    """期货订单。volume 单位为手。mode=live 必须带 approval_id，且 M1 不发送。"""

    order_id: str
    account_id: str
    mode: FutMode
    ts_code: str
    direction: FutDirection
    offset: FutOffset
    order_type: FutOrderType = "limit"
    price: Optional[Decimal] = None
    volume: int
    filled_volume: int = 0
    status: FutOrderStatus
    frozen_margin: Decimal = Decimal("0")
    frozen_fee: Decimal = Decimal("0")
    reject_reason: Optional[str] = None
    broker_order_id: Optional[str] = None
    approval_id: Optional[str] = None
    created_at: datetime = Field(default_factory=now_tz)
    updated_at: datetime = Field(default_factory=now_tz)

    @model_validator(mode="after")
    def _check_volume_and_live(self):
        if self.volume <= 0:
            raise ValueError("volume must be > 0")
        if self.filled_volume < 0 or self.filled_volume > self.volume:
            raise ValueError("filled_volume must be between 0 and volume")
        if self.mode == "live" and not self.approval_id:
            raise ValueError("mode=live requires approval_id")
        return self


class FutTrade(FutDocument):
    """成交明细。realized_pnl 仅平仓有值；开仓为 0。单位为手。"""

    trade_id: str
    order_id: str
    account_id: str
    ts_code: str
    direction: FutDirection
    offset: FutOffset
    price: Decimal
    volume: int
    fee: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    mode: FutMode
    trade_time: datetime = Field(default_factory=now_tz)

    @field_validator("volume")
    @classmethod
    def _positive_volume(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("volume must be > 0")
        return value


class FutPosition(FutDocument):
    """期货持仓。独立于股票 real_positions。单位为手。

    unrealized_pnl / mark_price_type：
    - 盘中 last = 最新价
    - 日终 settle = 结算价
    """

    position_id: str = Field(default_factory=_new_id)
    account_id: str
    ts_code: str
    product_code: Optional[str] = None
    direction: FutDirection
    volume: int = 0
    volume_today: int = 0
    volume_yesterday: int = 0
    open_avg_price: Optional[Decimal] = None
    position_avg_price: Optional[Decimal] = None
    margin_occupied: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl_cum: Decimal = Decimal("0")
    mark_price: Optional[Decimal] = None
    mark_price_type: Optional[MarkPriceType] = None
    last_settle: Optional[Decimal] = None
    updated_at: datetime = Field(default_factory=now_tz)

    @model_validator(mode="after")
    def _check_volumes(self):
        if self.volume < 0 or self.volume_today < 0 or self.volume_yesterday < 0:
            raise ValueError("position volumes must be >= 0")
        return self


class FutSyncRun(FutDocument):
    """行情/合约同步运行记录。M1 同步入口只写失败/占位，不拉外部源。"""

    run_id: str = Field(default_factory=_new_id)
    job_name: str
    status: SyncStatus
    error_code: Optional[str] = None
    message: Optional[str] = None
    started_at: datetime = Field(default_factory=now_tz)
    finished_at: Optional[datetime] = None


class FutOrderDraft(FutDocument):
    """M1 下单预检请求。不会被撮合，也不会写入 fut_order。"""

    account_id: str = Field(..., min_length=1)
    mode: FutMode
    ts_code: str = Field(..., min_length=1)
    direction: FutDirection
    offset: FutOffset
    order_type: FutOrderType = "limit"
    price: Optional[Decimal] = None
    volume: int = Field(..., gt=0)
    approval_id: Optional[str] = None
    contract_type: Optional[ContractType] = None

    @field_validator("ts_code")
    @classmethod
    def _upper_code(cls, value: str) -> str:
        return value.strip().upper()
