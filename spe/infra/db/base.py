"""服务端业务模块。"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """封装领域状态与业务约束。"""
