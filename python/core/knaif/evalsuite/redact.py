"""Strip local paths from evidence before it is committed (AGENTS.md, Public Output Hygiene).

Parity reports and scoreboards record rendered commands with absolute fixture and model paths.
The repo is public, and a home directory names a person, so the writers of committed evidence
pass their JSON through `redact_local_paths`: the checkout becomes `<repo>`, the home directory
`~`. Separators are kept as written, so a redacted Windows path still reads as one.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def _prefix_pattern(prefix: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in re.split(r"[/\\]+", prefix) if p]
    body = r"[/\\]+".join(parts)
    if not re.match(r"^[A-Za-z]:", prefix):
        body = r"[/\\]+" + body
    flags = re.IGNORECASE if re.match(r"^[A-Za-z]:", prefix) else 0
    return re.compile(body + r"(?![A-Za-z0-9_.-])", flags)


def _scratch_pattern(scratch: str) -> re.Pattern[str]:
    """A run folder under the scratch root, run id included (`<root>/run-<id>`). The one
    definition lives in `knaif.scratch`; evidence keeps the rest of the path as written."""
    from knaif.scratch import _run_folder_pattern

    full = _run_folder_pattern(Path(scratch))
    return re.compile(full.pattern.split("(?P<rest>")[0], full.flags)


def redact_local_paths(
    obj: Any,
    root: str | Path | None = None,
    home: str | Path | None = None,
    scratch: str | Path | None = None,
) -> Any:
    """Return *obj* with the checkout (*root*, default: the cwd) and the home directory (*home*,
    default: the user's) replaced by `<repo>` and `~` in every string. The checkout goes first,
    since it often lives under the home directory. A container's `/root` home names nobody and
    is left alone.

    A run's scratch folder (*scratch*, default: `knaif.scratch.scratch_root()`, plus the
    `run-<id>` below it) becomes `<work>` before either: it sits in the OS temp dir, which is
    under the home directory on Windows, and its run id differs on every run."""
    from knaif.scratch import scratch_root

    root = str(root if root is not None else Path.cwd().resolve())
    home = str(home if home is not None else os.path.expanduser("~"))
    scratch = str(scratch if scratch is not None else scratch_root())
    subs = [(_scratch_pattern(scratch), "<work>"), (_prefix_pattern(root), "<repo>")]
    if home and home.rstrip("/\\") not in ("", "/root"):
        subs.append((_prefix_pattern(home), "~"))

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            for pattern, repl in subs:
                value = pattern.sub(repl, value)
            return value
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, tuple):
            return tuple(walk(v) for v in value)
        return value

    return walk(obj)
