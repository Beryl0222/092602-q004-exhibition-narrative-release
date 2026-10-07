"""可替换的时钟，便于模拟媒体开放日之后的时间推进。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .domain import normalize_iso, now_iso


class Clock:
    """时钟接口：返回规范化 UTC ISO 时间。"""

    def now(self) -> str:  # pragma: no cover - 接口
        raise NotImplementedError


class SystemClock(Clock):
    def now(self) -> str:
        return now_iso()


class FakeClock(Clock):
    """测试 / 演示用时钟：停在某一时刻，可手动推进。"""

    def __init__(self, start: str | None = None) -> None:
        self._at = normalize_iso(start) if start else now_iso()

    def now(self) -> str:
        return self._at

    def advance(self, **delta) -> str:
        parsed = datetime.fromisoformat(self._at) + timedelta(**delta)
        self._at = parsed.astimezone(timezone.utc).isoformat()
        return self._at

    def set(self, value: str) -> str:
        self._at = normalize_iso(value)
        return self._at
