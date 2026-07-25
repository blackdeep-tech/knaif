"""Step and Intent ABCs — the two tool interface kinds."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .handler_api import HandlerContext


class Step(ABC):
    """Leaf tool: executes deterministically and returns a result dict."""

    name: str

    @abstractmethod
    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]: ...

    def preflight(self, args: dict[str, Any], **kw: Any) -> list[str]:
        return []


class Intent(ABC):
    """Macro tool: expands into a sub-plan of Steps before execution."""

    name: str

    @abstractmethod
    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]: ...

    def summarize(self, args: dict[str, Any], **kw: Any) -> str:
        return self.name

    def preflight(self, args: dict[str, Any], **kw: Any) -> list[str]:
        return []
