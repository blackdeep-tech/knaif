"""Write tools (fs toolkit): stage an output in the run scratch, check it, move it into place.

Nothing a step writes reaches the user's folder until it is known to be good. A handler asks
`stage_output` for a scratch path with the final file's name, writes there, then hands it to
`commit_output`, which checks it and moves it to the final path. A failure at any point leaves
the user's folder as it was, and the run scratch is deleted when the run ends.

Handlers reach these through `ctx.call_tool(...)`, never by import.
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path
from typing import Any

from knaif.handler_api import HandlerContext
from knaif.tool import Handler


def _resolve(raw: str, ctx: HandlerContext) -> Path:
    """*raw* as an absolute path: relative paths resolve against the sandbox (else root)."""
    p = Path(raw)
    base = ctx.sandbox if ctx.sandbox is not None else ctx.root
    return (p if p.is_absolute() else base / p).resolve()


def _inside(path: Path, folder: Path | None) -> bool:
    if folder is None:
        return False
    try:
        path.relative_to(Path(folder).resolve())
    except ValueError:
        return False
    return True


def _check_writable(path: Path, ctx: HandlerContext, *, allow_scratch: bool) -> None:
    if allow_scratch and _inside(path, ctx.work_dir):
        return
    if ctx.sandbox is not None and not _inside(path, ctx.sandbox):
        raise ValueError(f"Path {str(path)!r} is outside the sandbox {str(ctx.sandbox)!r}")


def _require_work_dir(ctx: HandlerContext, tool: str) -> Path:
    if ctx.work_dir is None:
        raise RuntimeError(f"{tool}: this run has no scratch folder (ctx.work_dir)")
    return Path(ctx.work_dir)


class StageOutput(Handler):
    """A scratch path that will become *path*: same file name, its own folder per call."""

    name = "stage_output"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        work = _require_work_dir(ctx, self.name)
        name = Path(str(args["path"])).name
        stage = work / "stage"
        if ctx.dry_run or ctx.skip_execution:
            return {"staged": str(stage / "0" / name)}
        for n in itertools.count():
            folder = stage / str(n)
            try:
                folder.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            return {"staged": str(folder / name)}
        raise AssertionError("unreachable")


class CommitOutput(Handler):
    """Check a staged file (exists, not empty) and move it to its final *path*."""

    name = "commit_output"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        final = _resolve(str(args["path"]), ctx)
        if _inside(final, ctx.work_dir):
            raise ValueError(
                f"commit_output: a final output cannot go into the run scratch: {final}"
            )
        _check_writable(final, ctx, allow_scratch=False)
        if ctx.dry_run or ctx.skip_execution:
            return {"mode": "dry_run", "path": str(final)}
        staged = Path(str(args["staged"]))
        existed = staged.is_file()
        size = staged.stat().st_size if existed else 0
        if size == 0:
            staged.unlink(missing_ok=True)
            what = "an empty file" if existed else "no file"
            raise ValueError(f"The step produced {what} for {final.name}; nothing was written.")
        final.parent.mkdir(parents=True, exist_ok=True)
        # `move`, not `replace`: the scratch is in the OS temp folder, which may be on another
        # volume than the user's folder, and `replace` cannot cross volumes.
        shutil.move(str(staged), str(final))
        return {"mode": "execute", "path": str(final), "bytes": size}


class EnsureParent(Handler):
    """Make the folder *path* will be written into. The resolved parent, never the spelling."""

    name = "ensure_parent"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        path = _resolve(str(args["path"]), ctx)
        _check_writable(path, ctx, allow_scratch=True)
        if not (ctx.dry_run or ctx.skip_execution):
            # Create the RESOLVED parent: `../escaped/../sb/out.mp4` passes containment, but
            # `mkdir` on its unnormalised parent would create `../escaped` on POSIX first.
            path.parent.mkdir(parents=True, exist_ok=True)
        return {"path": str(path)}


def free_output_path(
    path: Path,
    *,
    taken: set[Path] = frozenset(),  # type: ignore[assignment]
    suffix: str = "",
    separator: str = "_",
    first_number: int = 2,
    keep_requested: bool = False,
) -> Path:
    """The first free name for *path*: neither in *taken* nor on disk.

    Candidates: *path* itself when *keep_requested*, else ``<stem><suffix><ext>``; then
    ``<stem><suffix><separator><n><ext>`` from *first_number* up. ffmpeg's collision repair is
    ``suffix="_converted", separator="_", first_number=2``; documents' default output is
    ``keep_requested=True, separator="-", first_number=1``.
    """

    def free(candidate: Path) -> bool:
        return candidate not in taken and not candidate.exists()

    base = f"{path.stem}{suffix}"
    first = path if keep_requested else path.with_name(f"{base}{path.suffix}")
    if free(first):
        return first
    for n in itertools.count(first_number):
        candidate = path.with_name(f"{base}{separator}{n}{path.suffix}")
        if free(candidate):
            return candidate
    raise AssertionError("unreachable")


class PlanOutputPath(Handler):
    """The one free-name rule; the calling skill decides the suffix and the numbering."""

    name = "plan_output_path"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        path = free_output_path(
            _resolve(str(args["path"]), ctx),
            taken={_resolve(str(t), ctx) for t in args.get("taken") or []},
            suffix=str(args.get("suffix") or ""),
            separator=str(args.get("separator") or "_"),
            first_number=int(args.get("first_number") or 2),
            keep_requested=bool(args.get("keep_requested", False)),
        )
        return {"path": str(path)}


class UserSidePath(Handler):
    """*path*, or where it would sit in the user's folder if it is in the run scratch.

    Routing keeps an intermediate's place relative to the user's folder (`docs/a.pdf` ->
    `<work>/docs/a.pdf`), so the mapping back is exact. A skill anchors a default output on it:
    an output derived from a routed intermediate belongs in the user's folder, not in scratch.
    """

    name = "user_side_path"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        path = _resolve(str(args["path"]), ctx)
        if not _inside(path, ctx.work_dir):
            return {"path": str(path)}
        home = ctx.sandbox if ctx.sandbox is not None else ctx.root
        rel = path.relative_to(Path(ctx.work_dir).resolve())  # type: ignore[arg-type]
        return {"path": str((Path(home) / rel).resolve())}
