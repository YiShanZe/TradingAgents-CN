"""期货读接口错误。文案禁止空白或 NaN 冒充成功。"""

from __future__ import annotations

from typing import Any

from app.services.futures.schema import ERROR_MESSAGES


class FuturesDataError(Exception):
    def __init__(
        self,
        code: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
        message: str | None = None,
    ):
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        self.message = message or ERROR_MESSAGES.get(code, code)
        super().__init__(self.message)
