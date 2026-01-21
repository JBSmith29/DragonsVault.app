"""Shared service response wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ServiceResult:
    template: str | None = None
    context: dict[str, Any] | None = None
    response: Any = None
    status: int | None = None
