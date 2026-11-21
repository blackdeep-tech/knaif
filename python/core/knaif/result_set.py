"""What a plan will leave: the result set every preview ends with.

After linking, the per-file lift and scratch routing, the plan says which files reach the user's
folder; core computes them and the preview ends with them (owner, 2026-10-15): "leaves
beach_small.mp4", "leaves 5 mp4 and 5 mp3". It is the safeguard against a plausible but wrong
chain from a small model: the line shows a result that will not be there.

A step's files:

* its explicit ``output``: one per input file it runs over (one for a join); a name only when it
  runs over one file and the output is no pattern;
* else what its tool's ``produces:`` names: an ``{arg}`` value is an extension, a ``{format}``
  is a fixed one (``compress_video`` writes mp4 whatever it reads), a kind is shown as a kind;
* else each input's own extension (``trim clip.mov`` leaves a ``.mov``).

Outputs in the run scratch, and steps of readonly or control tools, leave nothing. Up to three
files, all named, are listed by name; otherwise counted by type, first seen first. A named file
whose extension disagrees with its tool's declaration is flagged (owner, 2026-10-20). Pure: the
caller expands globs. Rust mirror: ``knaif_core::result_set``; both read
contracts/parity/result_set_cases.json.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .batch_chain import INPUT_KEYS

#: Tools that steer the run and write nothing.
CONTROL_TOOLS = frozenset({"clarify", "reject", "done", "wait_for_confirmation"})


@dataclass(frozen=True)
class Result:
    """One file a plan leaves: its name when the plan gives it, its type either way."""

    tool: str
    name: str | None
    ext: str | None
    kind: str | None
    note: str | None = None

    @property
    def label(self) -> tuple[str, str]:
        """``(singular, plural)`` of its type: ``mp4``, or ``audio file(s)``, or ``file(s)``."""
        if self.ext:
            return self.ext, self.ext
        if self.kind:
            return f"{self.kind} file", f"{self.kind} files"
        return "file", "files"


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _ext(path: str) -> str:
    return posixpath.splitext(posixpath.basename(_norm(path)))[1].lstrip(".").lower()


def _shown(path: str, base: Path) -> str:
    """*path* relative to *base* when inside it, ``/``-separated."""
    p = Path(path)
    if p.is_absolute():
        try:
            return p.relative_to(base).as_posix()
        except ValueError:
            return _norm(path)
    return _norm(path).removeprefix("./")


def _in_scratch(path: str, scratch: str) -> bool:
    s = _norm(scratch).rstrip("/")
    return _norm(path).lower().startswith(s.lower() + "/")


def result_set(
    plan: Sequence[Mapping[str, Any]],
    *,
    scratch: str | Path,
    base: Path,
    expand: Mapping[str, Sequence[str]],
    readonly: set[str],
    joining: set[str],
    produces: Mapping[str, Any],
    file_kinds: Mapping[str, str],
    sets: Mapping[int, Sequence[str]] | None = None,
) -> list[Result]:
    """Every file *plan* leaves, in plan order (rules in the module docstring).

    *sets* maps the position of a step that writes many files from one to the files it will
    write: they are its results, by name, unless they are in the run scratch.
    """
    sets = sets or {}
    results: list[Result] = []
    for position, step in enumerate(plan):
        tool = str(step.get("tool") or "")
        args = step.get("args") or {}
        # A readonly tool leaves nothing, unless core gave it an output to save to (a tool's
        # `batch_output:`, e.g. each PDF's text in a batch).
        if tool in CONTROL_TOOLS or (tool in readonly and not args.get("output")):
            continue
        inputs: list[str] = []
        for key in INPUT_KEYS:
            value = args.get(key)
            for entry in [value] if isinstance(value, str) else value or []:
                if isinstance(entry, str):
                    inputs.extend(expand.get(entry) or ([] if entry in expand else [entry]))
        n = 1 if tool in joining else len(inputs)
        declared = produces.get(tool)
        declared_ext = None
        if isinstance(declared, Mapping):
            value = args.get(str(declared.get("arg")))
            if isinstance(value, str) and value.strip():
                declared_ext = value.strip().lower().lstrip(".")
        output = args.get("output")
        planned = sets.get(position)
        if planned is not None:
            results.extend(
                Result(tool, _shown(f, base), _ext(f) or None, file_kinds.get(_ext(f)))
                for f in planned
                if not _in_scratch(f, str(scratch))
            )
            continue
        if isinstance(output, str) and output:
            if _in_scratch(output, str(scratch)):
                continue
            ext = _ext(output) or None
            note = None
            if isinstance(declared, str) and ext and file_kinds.get(ext) != declared:
                note = f"named .{ext}, but {tool} writes {declared}"
            elif declared_ext and ext and ext != declared_ext:
                note = f"named .{ext}, but {tool} writes {declared_ext}"
            name = _shown(output, base) if n == 1 and "*" not in output else None
            results.extend(
                Result(tool, name, ext, file_kinds.get(ext or ""), note) for _ in range(n)
            )
        elif isinstance(declared, Mapping) and declared.get("format"):
            fixed = str(declared["format"])
            results.extend(Result(tool, None, fixed, file_kinds.get(fixed)) for _ in range(n))
        elif declared_ext:
            results.extend(
                Result(tool, None, declared_ext, file_kinds.get(declared_ext)) for _ in range(n)
            )
        elif isinstance(declared, str):
            results.extend(Result(tool, None, None, declared) for _ in range(n))
        else:
            sources = inputs[:1] if tool in joining else inputs
            results.extend(
                Result(tool, None, _ext(i) or None, file_kinds.get(_ext(i))) for i in sources
            )
    return results


def _join(parts: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def leaves_line(results: Sequence[Result]) -> str:
    """``leaves beach_small.mp4`` / ``leaves 5 mp4 and 5 mp3``; empty when nothing is left."""
    if not results:
        return ""
    if len(results) <= 3 and all(r.name for r in results):
        named = [f"{r.name} ({r.note})" if r.note else str(r.name) for r in results]
        return "leaves " + _join(named)
    counts: dict[tuple[str, str], int] = {}
    for r in results:
        counts[r.label] = counts.get(r.label, 0) + 1
    parts = [f"{n} {one if n == 1 else many}" for (one, many), n in counts.items()]
    notes = [f"{r.name} is {r.note}" for r in results if r.note and r.name]
    return "leaves " + _join(parts) + "".join(f"; {n}" for n in notes)


def same_for_line(rest: Sequence[str]) -> str:
    """The batch preview after the first file's steps: ``the same for 4 more files: …``."""
    names = [_norm(f) for f in rest]
    shown = names if len(names) <= 5 else [*names[:3], "…"]
    noun = "file" if len(names) == 1 else "files"
    return f"the same for {len(names)} more {noun}: " + ", ".join(shown)
