"""Handler and Intent ABCs — the two kinds of tool implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .handler_api import HandlerContext


class Handler(ABC):
    """A tool that executes: runs deterministically and returns a result dict."""

    name: str

    @abstractmethod
    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]: ...

    def preflight(self, args: dict[str, Any], **kw: Any) -> list[str]:
        return []


class Intent(ABC):
    """A tool that expands into a sub-plan of plan steps before execution."""

    name: str

    @abstractmethod
    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]: ...

    def summarize(self, args: dict[str, Any], **kw: Any) -> str:
        return self.name

    def preflight(self, args: dict[str, Any], **kw: Any) -> list[str]:
        return []
