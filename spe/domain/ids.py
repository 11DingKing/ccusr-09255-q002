"""服务端业务模块。"""

from __future__ import annotations

import uuid
from itertools import count
from typing import Protocol


class IdGenerator(Protocol):
    """封装领域状态与业务约束。"""

    def new_id(self) -> str:
        """执行确定性的业务处理。"""
        ...


class UuidGenerator:
    """封装领域状态与业务约束。"""

    def new_id(self) -> str:
        return str(uuid.uuid4())


class SequentialIdGenerator:
    """封装领域状态与业务约束。"""

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = count(1)

    def new_id(self) -> str:
        return f"{self._prefix}-{next(self._counter)}"
