"""M1 同步占位。

不调用 Tushare / AKShare，不写行情，不写订单。
只在 fut_sync_run 留下失败记录，便于健康检查看到明确错误码。
"""

from __future__ import annotations

from app.services.futures.errors import FuturesDataError
from app.services.futures.guard import insert_futures_doc
from app.services.futures.schema import ERROR_MESSAGES
from app.utils.timezone import now_tz


def tushare_token_configured() -> bool:
    """只判断是否配置，不返回、不记录 token 明文。"""
    from app.core.config import settings

    return bool((getattr(settings, "TUSHARE_TOKEN", "") or "").strip())


async def run_readonly_sync_stub(db, *, job_name: str = "fut_readonly_sync") -> dict:
    """记录一次未执行的同步。返回应写入 HTTP 响应的错误体字段。"""
    now = now_tz().isoformat()
    if not tushare_token_configured():
        code = "FUT_SRC_AUTH"
        status_code = 503
    else:
        code = "FUT_SYNC_STUB"
        status_code = 501
    message = ERROR_MESSAGES[code]
    doc = {
        "run_id": _run_id(),
        "job_name": job_name,
        "status": "failed",
        "error_code": code,
        "message": message,
        "started_at": now,
        "finished_at": now,
    }
    await insert_futures_doc(db, "fut_sync_run", doc)
    raise FuturesDataError(code, status_code, {"run": doc})


def _run_id() -> str:
    from uuid import uuid4

    return str(uuid4())
