-- =============================================================================
-- MongoDB 镜像（本仓库运行时库）：migrations/v3_3_0.py
-- 应用启动或 `python -m migrations.cli run --version 3.3.0` 会创建同名 fut_* 集合索引并写入白名单。
-- 股票 real_positions / RealPosition 不在本脚本范围内。
--
-- TradingAgents-CN 国内期货账本 / 行情 DDL
-- 目标库：PostgreSQL 13+（推荐；PG12 需 CREATE EXTENSION pgcrypto 以使用 gen_random_uuid）
-- 日期：2026-10-11（Asia/Shanghai）
-- 权威字段方案：/workspace/futures-ledger-schema.md
-- =============================================================================
--
-- 【MySQL 8 差异摘要】
-- 1) 无原生 ENUM 与 PG 用法不同：可用 VARCHAR + CHECK，或 MySQL ENUM。
-- 2) COMMENT ON 改为列定义尾部 COMMENT '...'；表 COMMENT 用 ALTER TABLE ... COMMENT。
-- 3) UUID：可用 CHAR(36) 或 BINARY(16)；或依赖应用侧生成字符串主键。
-- 4) TIMESTAMPTZ → DATETIME(3) 或 TIMESTAMP（注意会话时区）；建议应用统一存 UTC+业务时区标注。
-- 5) NUMERIC 与 DECIMAL 等价；BOOLEAN → TINYINT(1)。
-- 6) 部分唯一索引含 NULL 语义在 MySQL 与 PG 略有差异，注意 contract 可选字段。
--
-- 【待确认 — 广期所后缀】
-- Tushare 文档交易所表：GFEX 后缀为 .GFE；
-- AKShare 部分文档写 .GFEX。
-- 本 DDL 不强制后缀枚举；ts_code 自由字符串。接入层须统一规范化（建议对齐 Tushare：.GFE）。
--
-- 【待确认 — multiplier vs per_unit】
-- Tushare fut_basic：multiplier 文档写「只适用于国债/指数期货」；商品多为 per_unit（每手交易单位）。
-- 入库规则（建议，待产品/数据确认）：
--   统一写入 fut_contract.multiplier；
--   若 multiplier 为空则用 per_unit；
--   另存 raw_multiplier / raw_per_unit 便于审计（本表用 source_multiplier_field 标注来源）。
--
-- 【浮动盈亏约定 — 指挥官已定】
-- 盘中：可用最新价（mark=last）计算浮动盈亏；
-- 日终：用结算价 settle 计算/落地浮动盈亏。
-- 见 fut_position / fut_account_ledger 相关 COMMENT。
--
-- 【硬约束】未经批准不发起真实下单；mode=live 须审批门禁。
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 可选：独立 schema
-- CREATE SCHEMA IF NOT EXISTS futures;
-- SET search_path TO futures, public;
-- ---------------------------------------------------------------------------

-- ========== 0) 白名单（P0 小集）==========
CREATE TABLE IF NOT EXISTS fut_product_whitelist (
    fut_code        VARCHAR(16) PRIMARY KEY,
    exchange        VARCHAR(16) NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fut_product_whitelist IS 'P0 品种白名单（已定稿 IF/IM/RB）；同步任务仅拉取 enabled=true 的品种';
COMMENT ON COLUMN fut_product_whitelist.fut_code IS '品种代码，如 IF/IM/RB';
COMMENT ON COLUMN fut_product_whitelist.exchange IS 'SHFE/DCE/CZCE/CFFEX/INE/GFEX 等';

-- ========== 1) 合约元数据 ==========
CREATE TABLE IF NOT EXISTS fut_contract (
    contract_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_code                  VARCHAR(32) NOT NULL,
    symbol                   VARCHAR(32) NOT NULL,
    fut_code                 VARCHAR(16) NOT NULL,
    exchange                 VARCHAR(16) NOT NULL,
    name                     VARCHAR(64),
    delivery_month           CHAR(6),
    list_date                DATE,
    delist_date              DATE,
    last_delivery_date       DATE,
    -- 统一乘数（见文件头待确认：Tushare multiplier vs per_unit）
    multiplier               NUMERIC(20, 8) NOT NULL,
    source_multiplier_field  VARCHAR(32),
    raw_multiplier           NUMERIC(20, 8),
    raw_per_unit             NUMERIC(20, 8),
    trade_unit               VARCHAR(32),
    quote_unit               VARCHAR(64),
    price_tick               NUMERIC(20, 8),
    trade_time_desc          TEXT,
    contract_type            VARCHAR(16) NOT NULL,
    is_main                  BOOLEAN,
    currency                 VARCHAR(8) DEFAULT 'CNY',
    status                   VARCHAR(16) NOT NULL DEFAULT 'listed',
    source                   VARCHAR(32) NOT NULL DEFAULT 'tushare',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fut_contract_ts_code UNIQUE (ts_code),
    CONSTRAINT chk_fut_contract_type CHECK (contract_type IN ('month', 'main', 'continuous')),
    CONSTRAINT chk_fut_contract_status CHECK (status IN ('listed', 'delisted', 'suspended')),
    CONSTRAINT chk_fut_contract_multiplier_positive CHECK (multiplier > 0)
);
COMMENT ON TABLE fut_contract IS '期货合约元数据；与 A 股标的表分离';
COMMENT ON COLUMN fut_contract.ts_code IS '统一合约代码，建议对齐 Tushare，如 CU2410.SHF / IF.CFX；广期所后缀 .GFE vs .GFEX 待确认统一';
COMMENT ON COLUMN fut_contract.multiplier IS '合约乘数（统一列）。入库：优先 fut_basic.multiplier，空则用 per_unit。待确认。';
COMMENT ON COLUMN fut_contract.source_multiplier_field IS '实际采用的源字段名：multiplier 或 per_unit';
COMMENT ON COLUMN fut_contract.price_tick IS '最小变动价位；Tushare 常仅有文字说明，可自 AKShare futures_rule/fees_info 补全';
COMMENT ON COLUMN fut_contract.contract_type IS 'month=月合约 main=主力代码 continuous=连续代码';

CREATE INDEX IF NOT EXISTS idx_fut_contract_fut_exch
    ON fut_contract (fut_code, exchange);
CREATE INDEX IF NOT EXISTS idx_fut_contract_type_status
    ON fut_contract (contract_type, status);
CREATE INDEX IF NOT EXISTS idx_fut_contract_delivery
    ON fut_contract (fut_code, delivery_month);

-- ========== 2) 主力/连续映射 ==========
CREATE TABLE IF NOT EXISTS fut_mapping (
    map_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_code           VARCHAR(32) NOT NULL,
    trade_date        DATE NOT NULL,
    mapping_ts_code   VARCHAR(32) NOT NULL,
    map_kind          VARCHAR(16) NOT NULL,
    source            VARCHAR(32) NOT NULL DEFAULT 'tushare',
    rule_version      VARCHAR(32),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fut_mapping UNIQUE (ts_code, trade_date, map_kind),
    CONSTRAINT chk_fut_mapping_kind CHECK (map_kind IN ('main', 'continuous'))
);
COMMENT ON TABLE fut_mapping IS '主力或连续代码按日映射到可交易月合约；对齐 Tushare fut_mapping';
COMMENT ON COLUMN fut_mapping.ts_code IS '主力/连续代码，如 IF.CFX / IFL.CFX';
COMMENT ON COLUMN fut_mapping.mapping_ts_code IS '当日实际月合约，如 IF2410.CFX；账本持仓必须挂此代码而非主连';

CREATE INDEX IF NOT EXISTS idx_fut_mapping_date
    ON fut_mapping (trade_date);
CREATE INDEX IF NOT EXISTS idx_fut_mapping_month
    ON fut_mapping (mapping_ts_code, trade_date);

-- ========== 3) 日线行情 ==========
CREATE TABLE IF NOT EXISTS fut_daily (
    ts_code          VARCHAR(32) NOT NULL,
    trade_date       DATE NOT NULL,
    open             NUMERIC(20, 8),
    high             NUMERIC(20, 8),
    low              NUMERIC(20, 8),
    close            NUMERIC(20, 8),
    settle           NUMERIC(20, 8),
    pre_close        NUMERIC(20, 8),
    pre_settle       NUMERIC(20, 8),
    change_close     NUMERIC(20, 8),
    change_settle    NUMERIC(20, 8),
    volume           BIGINT,
    amount           NUMERIC(24, 4),
    amount_unit_raw  VARCHAR(16),
    open_interest    BIGINT,
    oi_chg           BIGINT,
    up_limit         NUMERIC(20, 8),
    down_limit       NUMERIC(20, 8),
    source           VARCHAR(32) NOT NULL DEFAULT 'tushare',
    synced_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ts_code, trade_date)
);
COMMENT ON TABLE fut_daily IS '期货日线；volume/open_interest 单位为手（非股、非只）';
COMMENT ON COLUMN fut_daily.volume IS '成交量（手）';
COMMENT ON COLUMN fut_daily.open_interest IS '持仓量（手）';
COMMENT ON COLUMN fut_daily.amount IS '成交额统一为元；若源为万元须换算并记 amount_unit_raw';
COMMENT ON COLUMN fut_daily.settle IS '结算价；日终盯市与浮动盈亏落地基准（指挥官约定：日终用 settle）';
COMMENT ON COLUMN fut_daily.synced_at IS '本行写入/同步时间，用于 US-P0-1 延迟验收';

CREATE INDEX IF NOT EXISTS idx_fut_daily_date
    ON fut_daily (trade_date);

-- ========== 4) 保证金 / 手续费快照 ==========
CREATE TABLE IF NOT EXISTS fut_margin_fee (
    ts_code                     VARCHAR(32) NOT NULL,
    trade_date                  DATE NOT NULL,
    multiplier                  NUMERIC(20, 8) NOT NULL,
    price_tick                  NUMERIC(20, 8),
    long_margin_rate            NUMERIC(12, 8),
    short_margin_rate           NUMERIC(12, 8),
    long_hedge_margin_rate      NUMERIC(12, 8),
    short_hedge_margin_rate     NUMERIC(12, 8),
    m_ratio_min                 NUMERIC(12, 8),
    open_fee_rate               NUMERIC(12, 8),
    open_fee_per_lot            NUMERIC(20, 8),
    close_fee_rate              NUMERIC(12, 8),
    close_fee_per_lot           NUMERIC(20, 8),
    close_today_fee_rate        NUMERIC(12, 8),
    close_today_fee_per_lot     NUMERIC(20, 8),
    delivery_fee                NUMERIC(20, 8),
    source                      VARCHAR(32) NOT NULL DEFAULT 'tushare',
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ts_code, trade_date)
);
COMMENT ON TABLE fut_margin_fee IS '按合约×日的保证金率与手续费快照；仿真缺省时可回退品种默认表';
COMMENT ON COLUMN fut_margin_fee.long_margin_rate IS '买投机保证金率（小数，如 0.10）；来源 fut_settle.long_margin_rate';
COMMENT ON COLUMN fut_margin_fee.close_today_fee_rate IS '平今费率；MVP 可空，完整版区分平今/平昨';

CREATE INDEX IF NOT EXISTS idx_fut_margin_fee_date
    ON fut_margin_fee (trade_date);

-- ========== 5) 期货资金账本（与 A 股账户隔离）==========
-- 说明：若现有 account 表可扩展，可改为 account_id FK + asset_class 列；
-- P0 推荐独立表，避免污染股票资金字段。
CREATE TABLE IF NOT EXISTS fut_account_ledger (
    ledger_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id       VARCHAR(64) NOT NULL,
    asset_class      VARCHAR(16) NOT NULL DEFAULT 'futures',
    balance          NUMERIC(24, 4) NOT NULL DEFAULT 0,
    available        NUMERIC(24, 4) NOT NULL DEFAULT 0,
    margin_used      NUMERIC(24, 4) NOT NULL DEFAULT 0,
    frozen_margin    NUMERIC(24, 4) NOT NULL DEFAULT 0,
    frozen_fee       NUMERIC(24, 4) NOT NULL DEFAULT 0,
    realized_pnl     NUMERIC(24, 4) NOT NULL DEFAULT 0,
    unrealized_pnl   NUMERIC(24, 4) NOT NULL DEFAULT 0,
    currency         VARCHAR(8) NOT NULL DEFAULT 'CNY',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fut_account_ledger UNIQUE (account_id, asset_class),
    CONSTRAINT chk_fut_ledger_asset CHECK (asset_class = 'futures')
);
COMMENT ON TABLE fut_account_ledger IS '期货仿真/对接资金视图；asset_class 固定 futures，与 A 股资金隔离';
COMMENT ON COLUMN fut_account_ledger.unrealized_pnl IS '账户级浮动盈亏合计。约定：盘中可用最新价汇总；日终用 settle 重算落地';
COMMENT ON COLUMN fut_account_ledger.available IS '可用资金；开仓校验 available >= 保证金+费用';

-- ========== 6) 订单 ==========
CREATE TABLE IF NOT EXISTS fut_order (
    order_id           VARCHAR(64) PRIMARY KEY,
    account_id         VARCHAR(64) NOT NULL,
    mode               VARCHAR(16) NOT NULL,
    ts_code            VARCHAR(32) NOT NULL,
    direction          VARCHAR(8) NOT NULL,
    offset             VARCHAR(32) NOT NULL,
    order_type         VARCHAR(16) NOT NULL DEFAULT 'limit',
    price              NUMERIC(20, 8),
    volume             INTEGER NOT NULL,
    filled_volume      INTEGER NOT NULL DEFAULT 0,
    status             VARCHAR(32) NOT NULL,
    frozen_margin      NUMERIC(24, 4) NOT NULL DEFAULT 0,
    frozen_fee         NUMERIC(24, 4) NOT NULL DEFAULT 0,
    reject_reason      TEXT,
    broker_order_id    VARCHAR(64),
    approval_id        VARCHAR(64),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_fut_order_mode CHECK (mode IN ('paper', 'live_shadow', 'live')),
    CONSTRAINT chk_fut_order_dir CHECK (direction IN ('long', 'short')),
    CONSTRAINT chk_fut_order_offset CHECK (offset IN ('open', 'close', 'close_today', 'close_yesterday')),
    CONSTRAINT chk_fut_order_type CHECK (order_type IN ('limit', 'market')),
    CONSTRAINT chk_fut_order_status CHECK (status IN (
        'pending_new', 'submitted', 'partial_filled', 'filled', 'canceled', 'rejected'
    )),
    CONSTRAINT chk_fut_order_volume CHECK (volume > 0 AND filled_volume >= 0 AND filled_volume <= volume)
);
COMMENT ON TABLE fut_order IS '期货订单；volume 单位为手。mode=live 必须带 approval_id（未经批准不真实下单）';
COMMENT ON COLUMN fut_order.ts_code IS '必须为月合约；禁止对主连/连续代码直接下单';
COMMENT ON COLUMN fut_order.direction IS 'long/short；与 offset 组合表达开多开空平多平空';
COMMENT ON COLUMN fut_order.offset IS 'MVP 可用 open/close；完整版 close_today/close_yesterday';
COMMENT ON COLUMN fut_order.mode IS 'paper=仿真；live_shadow=影子报文不发柜台；live=实盘（默认关+审批）';

CREATE INDEX IF NOT EXISTS idx_fut_order_account_time
    ON fut_order (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fut_order_status
    ON fut_order (status);
CREATE INDEX IF NOT EXISTS idx_fut_order_ts_code
    ON fut_order (ts_code, created_at DESC);

-- ========== 7) 成交 ==========
CREATE TABLE IF NOT EXISTS fut_trade (
    trade_id         VARCHAR(64) PRIMARY KEY,
    order_id         VARCHAR(64) NOT NULL REFERENCES fut_order(order_id),
    account_id       VARCHAR(64) NOT NULL,
    ts_code          VARCHAR(32) NOT NULL,
    direction        VARCHAR(8) NOT NULL,
    offset           VARCHAR(32) NOT NULL,
    price            NUMERIC(20, 8) NOT NULL,
    volume           INTEGER NOT NULL,
    fee              NUMERIC(24, 4) NOT NULL DEFAULT 0,
    realized_pnl     NUMERIC(24, 4) NOT NULL DEFAULT 0,
    mode             VARCHAR(16) NOT NULL,
    trade_time       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_fut_trade_dir CHECK (direction IN ('long', 'short')),
    CONSTRAINT chk_fut_trade_offset CHECK (offset IN ('open', 'close', 'close_today', 'close_yesterday')),
    CONSTRAINT chk_fut_trade_volume CHECK (volume > 0)
);
COMMENT ON TABLE fut_trade IS '期货成交明细；realized_pnl 仅平仓有值';
COMMENT ON COLUMN fut_trade.volume IS '成交手数';

CREATE INDEX IF NOT EXISTS idx_fut_trade_order
    ON fut_trade (order_id);
CREATE INDEX IF NOT EXISTS idx_fut_trade_account_time
    ON fut_trade (account_id, trade_time DESC);

-- ========== 8) 持仓 ==========
CREATE TABLE IF NOT EXISTS fut_position (
    position_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id           VARCHAR(64) NOT NULL,
    ts_code              VARCHAR(32) NOT NULL,
    product_code         VARCHAR(16),
    direction            VARCHAR(8) NOT NULL,
    volume               INTEGER NOT NULL DEFAULT 0,
    volume_today         INTEGER NOT NULL DEFAULT 0,
    volume_yesterday     INTEGER NOT NULL DEFAULT 0,
    open_avg_price       NUMERIC(20, 8),
    position_avg_price   NUMERIC(20, 8),
    margin_occupied      NUMERIC(24, 4) NOT NULL DEFAULT 0,
    unrealized_pnl       NUMERIC(24, 4) NOT NULL DEFAULT 0,
    realized_pnl_cum     NUMERIC(24, 4) NOT NULL DEFAULT 0,
    mark_price           NUMERIC(20, 8),
    mark_price_type      VARCHAR(16),
    last_settle          NUMERIC(20, 8),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fut_position UNIQUE (account_id, ts_code, direction),
    CONSTRAINT chk_fut_pos_dir CHECK (direction IN ('long', 'short')),
    CONSTRAINT chk_fut_pos_vol CHECK (
        volume >= 0 AND volume_today >= 0 AND volume_yesterday >= 0
    ),
    CONSTRAINT chk_fut_pos_mark_type CHECK (
        mark_price_type IS NULL OR mark_price_type IN ('last', 'settle')
    )
);
COMMENT ON TABLE fut_position IS '期货持仓（独立于股票持仓表）；单位手；禁止写入 A 股 real_positions';
COMMENT ON COLUMN fut_position.volume IS '总持仓手数';
COMMENT ON COLUMN fut_position.volume_today IS '今仓；MVP 可先为 0，完整版支持平今';
COMMENT ON COLUMN fut_position.unrealized_pnl IS '浮动盈亏。约定（指挥官已定）：盘中 mark_price_type=last 用最新价；日终改为 settle 重算并落库';
COMMENT ON COLUMN fut_position.mark_price IS '当前计价所用价格（最新价或结算价）';
COMMENT ON COLUMN fut_position.mark_price_type IS 'last=盘中最新价；settle=日终结算价';
COMMENT ON COLUMN fut_position.margin_occupied IS '保证金占用 ≈ mark或settle × multiplier × volume × margin_rate';

CREATE INDEX IF NOT EXISTS idx_fut_position_account
    ON fut_position (account_id);
CREATE INDEX IF NOT EXISTS idx_fut_position_product
    ON fut_position (product_code);

-- ========== 9) 可选：同步运行日志（支撑错误态验收）==========
CREATE TABLE IF NOT EXISTS fut_sync_run (
    run_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name       VARCHAR(64) NOT NULL,
    status         VARCHAR(16) NOT NULL,
    error_code     VARCHAR(64),
    message        TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    CONSTRAINT chk_fut_sync_status CHECK (status IN ('running', 'success', 'failed'))
);
COMMENT ON TABLE fut_sync_run IS '行情/合约同步运行记录；US-P0-1 错误态与健康检查用';

COMMIT;

-- =============================================================================
-- P0 白名单定稿（2026-10-11，指挥官确认默认：IF / IM / RB）
-- =============================================================================
INSERT INTO fut_product_whitelist (fut_code, exchange, enabled, note) VALUES
  ('IF', 'CFFEX', TRUE, 'P0 定稿：沪深300股指'),
  ('IM', 'CFFEX', TRUE, 'P0 定稿：中证1000股指'),
  ('RB', 'SHFE',  TRUE, 'P0 定稿：螺纹钢')
ON CONFLICT (fut_code) DO UPDATE SET
  exchange = EXCLUDED.exchange,
  enabled  = EXCLUDED.enabled,
  note     = EXCLUDED.note,
  updated_at = NOW();
