"""ResolveInputs shared step — resolves paths/dirs/globs inside the sandbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knaif.handler_api import HandlerContext
from knaif.tool import Step


def _assert_in_sandbox(p: Path, sandbox: Path | None) -> None:
    """Raise ValueError if *p* is not inside *sandbox*.

    Skipped entirely when sandbox is None (open / CLI mode).
    """
    if sandbox is None:
        return
    try:
        p.resolve().relative_to(sandbox.resolve())
    except ValueError:
        raise ValueError(
            f"Path {str(p)!r} is outside the sandbox {str(sandbox.resolve())!r}"
        ) from None


class ResolveInputs(Step):
    """Resolve a list of input paths (files, dirs, globs) inside the sandbox.

    Returns ``{"count": N, "files": [...]}``. Consumers that receive the result
    via a ``$var`` reference accept either a bare list or this dict form
    (checked with ``isinstance(v, dict) and "files" in v``).
    """

    name = "resolve_inputs"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        raw_paths = args.get("paths") or []
        if isinstance(raw_paths, str):
            raw_paths = [raw_paths]
        extensions = args.get("extensions")
        resolved: list[str] = []
        for raw in raw_paths:
            p = Path(raw)
            if not p.is_absolute():
                base = ctx.sandbox if ctx.sandbox is not None else ctx.root
                p = (base / p).resolve()
            _assert_in_sandbox(p, ctx.sandbox)
            if p.is_dir():
                iterable = p.rglob("*") if extensions else p.glob("*")
                for child in iterable:
                    if child.is_file():
                        if extensions and child.suffix.lstrip(".").lower() not in [
                            e.lower().lstrip(".") for e in extensions
                        ]:
                            continue
                        resolved.append(str(child))
            elif p.exists():
                resolved.append(str(p))
            elif any(c in str(p) for c in ("*", "?", "[")):
                parent = p.parent
                if parent.is_dir():
                    matched = sorted(parent.glob(p.name))
                    for child in matched:
                        if child.is_file():
                            if extensions and child.suffix.lstrip(".").lower() not in [
                                e.lower().lstrip(".") for e in extensions
                            ]:
                                continue
                            resolved.append(str(child))
            else:
                resolved.append(str(p))  # leave to inspect_media to report missing
        return {"count": len(resolved), "files": resolved}
