"""resolve_inputs — resolves paths, folders and globs inside the sandbox (fs toolkit)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knaif.batch_chain import list_files
from knaif.handler_api import HandlerContext
from knaif.tool import Handler


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


def _in_run_scratch(p: Path, ctx: HandlerContext) -> bool:
    """True when *p* is in this run's scratch, where a routed chain intermediate lives.

    Only a path core or an earlier step put there can name it: the model's own paths were
    checked against the sandbox before routing, and the run id is fresh every run.
    """
    work = getattr(ctx, "work_dir", None)
    if not isinstance(work, (str, Path)):
        return False
    return p.resolve().is_relative_to(Path(work).resolve())


def _absolute(raw: str, base: Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (base / p).resolve()


class ResolveInputs(Handler):
    """Resolve a list of input paths (files, dirs, globs) inside the sandbox.

    Returns ``{"count": N, "files": [...]}``. Consumers that receive the result
    via a ``$var`` reference accept either a bare list or this dict form
    (checked with ``isinstance(v, dict) and "files" in v``).

    A folder or a glob comes back in natural order (``clip1``, ``clip2``, ``clip10``), the order
    a batch runs its files in, so a merge and a batch agree; the entries keep their own order
    (``contracts/parity/batch_cases.json``). ``exclude`` drops a folder's or a glob's match that
    is one of the given paths: a join passes its own output, which a second run of "merge all the
    PDFs here" would otherwise read back. A path named on its own is kept.
    """

    name = "resolve_inputs"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        raw_paths = args.get("paths") or []
        if isinstance(raw_paths, str):
            raw_paths = [raw_paths]
        extensions = args.get("extensions")
        # Filtering and recursion are separate requests. `extensions` used to imply `rglob`,
        # so keeping only media also descended into every nested directory; nothing passed
        # `extensions` while that held, so the coupling was never exercised.
        recursive = bool(args.get("recursive"))
        base = ctx.sandbox if ctx.sandbox is not None else ctx.root
        excluded = {_absolute(e, base).resolve() for e in args.get("exclude") or []}
        wanted = [e.lower().lstrip(".") for e in extensions or []]
        resolved: list[str] = []
        for raw in raw_paths:
            p = _absolute(raw, base)
            if not _in_run_scratch(p, ctx):
                _assert_in_sandbox(p, ctx.sandbox)
            if p.is_dir() or (not p.exists() and any(c in str(p) for c in ("*", "?", "["))):
                # A folder, or a glob in one: the one listing core's batch uses too.
                listed = (
                    list_files(p, recursive=recursive)
                    if p.is_dir()
                    else list_files(p.parent, p.name)
                )
                for _rel, child in listed:
                    if child.resolve() in excluded or (
                        wanted and child.suffix.lstrip(".").lower() not in wanted
                    ):
                        continue
                    resolved.append(str(child))
            elif p.exists():
                resolved.append(str(p))
            else:
                resolved.append(str(p))  # leave to inspect_media to report missing
        return {"count": len(resolved), "files": resolved}
