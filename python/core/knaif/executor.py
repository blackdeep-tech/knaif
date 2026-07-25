"""Command handlers and plan executor."""

from __future__ import annotations

import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .core_tools import CORE_STEP_MAP
from .handler_api import HandlerContext
from .planner import _resolve_path

_FILE_TYPE_PATTERNS: dict[str, list[str]] = {
    "executable": (
        ["*.exe", "*.bat", "*.cmd", "*.com", "*.ps1"]
        if sys.platform == "win32"
        else ["*.sh", "*.run", "*.AppImage"]
    ),
    "text": ["*.txt"],
    "image": ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp", "*.webp"],
    "document": ["*.pdf", "*.doc", "*.docx", "*.xls", "*.xlsx"],
    "script": ["*.py", "*.sh", "*.bat", "*.ps1"],
    "archive": ["*.zip", "*.tar", "*.gz", "*.bz2", "*.rar", "*.7z"],
    "log": ["*.log"],
    "config": ["*.yaml", "*.yml", "*.json", "*.toml", "*.ini", "*.cfg"],
}


# ── helpers ──────────────────────────────────────────────────────────────────


def _iter_files(path: Path, recursive: bool) -> list[Path]:
    if not path.exists():
        return []
    globber = path.rglob("*") if recursive else path.glob("*")
    return [p for p in globber if p.is_file()]


def _patterns_for(args: dict[str, Any]) -> list[str]:
    file_type = args.get("file_type")
    if file_type:
        return _FILE_TYPE_PATTERNS[file_type]
    pattern = args.get("pattern")
    if pattern:
        return [pattern]
    return ["*"]


def _match_files(files: list[Path], patterns: list[str]) -> list[Path]:
    return [p for p in files if any(fnmatch(p.name, pat) for pat in patterns)]


# ── handlers ─────────────────────────────────────────────────────────────────


def cmd_list_files(args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    path = _resolve_path(args["path"], ctx.root, ctx.sandbox)
    files = _match_files(_iter_files(path, recursive=False), _patterns_for(args))
    return {"count": len(files), "files": [str(p) for p in sorted(files)]}


def cmd_find_files(args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    path = _resolve_path(args["path"], ctx.root, ctx.sandbox)
    files = _match_files(_iter_files(path, recursive=True), _patterns_for(args))
    return {"count": len(files), "files": [str(p) for p in sorted(files)]}


def cmd_delete_files(args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    path = _resolve_path(args["path"], ctx.root, ctx.sandbox)
    recursive = args.get("recursive", False)
    files = _match_files(_iter_files(path, recursive=recursive), _patterns_for(args))

    if ctx.dry_run:
        return {
            "mode": "dry_run",
            "would_delete_count": len(files),
            "would_delete": [str(p) for p in sorted(files)],
        }

    deleted: list[str] = []
    errors: list[dict] = []
    for p in files:
        try:
            p.unlink()
            deleted.append(str(p))
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(p), "error": str(exc)})

    return {
        "mode": "execute",
        "deleted_count": len(deleted),
        "deleted": deleted,
        "errors": errors,
    }


def cmd_move_files(args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    src = _resolve_path(args["src"], ctx.root, ctx.sandbox)
    dst = _resolve_path(args["dst"], ctx.root, ctx.sandbox)
    files = _match_files(_iter_files(src, recursive=False), _patterns_for(args))

    if ctx.dry_run:
        return {
            "mode": "dry_run",
            "would_move_count": len(files),
            "would_move": [str(p) for p in sorted(files)],
            "to": str(dst),
        }

    dst.mkdir(parents=True, exist_ok=True)
    moved: list[dict] = []
    errors: list[dict] = []
    for p in files:
        target = dst / p.name
        try:
            p.rename(target)
            moved.append({"from": str(p), "to": str(target)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(p), "error": str(exc)})

    return {
        "mode": "execute",
        "moved_count": len(moved),
        "moved": moved,
        "errors": errors,
    }


# ── handler registries ────────────────────────────────────────────────────────

# Core control tools are defined once, as Step classes in core_tools.py. The legacy
# dict surface (still used as the bare-CommandAgent default and merged by from_skill)
# is derived from those Steps so there is a single source of truth.
CORE_HANDLERS: dict[str, Any] = {name: step.handle for name, step in CORE_STEP_MAP.items()}

HANDLERS: dict[str, Any] = {
    "list_files": cmd_list_files,
    "find_files": cmd_find_files,
    "delete_files": cmd_delete_files,
    "move_files": cmd_move_files,
    **CORE_HANDLERS,
}

# Core tool *metadata* lives in core_tools.yaml (loaded as CORE_TOOL_DEFS in
# core_tools.py), alongside the core Step classes.
