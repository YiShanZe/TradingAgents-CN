"""国内期货账本（M1 数据模型）。

与股票 RealPosition / real_positions 隔离。撮合引擎见 matching.py，M2 才实现。
"""

from app.services.futures.schema import ASSET_CLASS_FUTURES, PNL_POLICY

__all__ = ["ASSET_CLASS_FUTURES", "PNL_POLICY"]
