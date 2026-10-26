"""The per-run scratch folder: where intermediate files live, outside the user's folders.

Every run that executes gets a fresh ``<OS temp>/knaif/run-<id>/``, handed to handlers as
``ctx.work_dir``. A chain's intermediate, a preview clip, and an output being written before it
is verified all go there, so the user's folder only ever receives the outputs they asked for.

Lifetime rules:

* **Deleted when the run ends, on success and on failure.** There is no persistent cache.
* **Kept under ``KNAIF_DEBUG``** (any non-empty value), the developer debugging switch, or
  ``KNAIF_KEEP_SCRATCH`` (the eval lane grades chain intermediates from the scratch).
* **A dry run writes nothing.** ``work_dir`` is still a path, so steps can render commands
  that name it, but the folder is never created. ``skip_execution`` (the planning pass with
  real probes) counts as a dry run here.
* **Stale folders are swept at start.** A run killed hard cannot delete its own folder, so the
  next run removes ``run-*`` folders older than :data:`STALE_AFTER_S`. Age, not "every folder",
  because another knaif process may be running right now; a day is far longer than any run.

``KNAIF_SCRATCH_ROOT`` overrides the root (tests, and anyone whose temp is too small).

Scratch paths name the temp directory, which names the user. They are never printed in normal
output (a preview clip is the one deliberate exception) and are redacted to ``<work>`` in
evidence (``evalsuite.redact``).
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from types import TracebackType

#: How old a ``run-*`` folder must be before a new run may assume its owner is gone.
STALE_AFTER_S = 24 * 3600


def scratch_root() -> Path:
    """``$KNAIF_SCRATCH_ROOT``, else ``<OS temp>/knaif``; always absolute.

    A relative root is joined to the working folder. Left relative, a run scratch path never
    matched itself once a tool resolved it against the sandbox (``user_side_path``), and a
    refined chain's results went with the scratch (2026-10-20).
    """
    env = os.environ.get("KNAIF_SCRATCH_ROOT")
    return Path(env).absolute() if env else Path(tempfile.gettempdir()) / "knaif"


def _run_folder_pattern(root: Path) -> re.Pattern[str]:
    """``<root>/run-<id>`` with either separator, case-insensitive on a Windows drive path."""
    parts = [re.escape(p) for p in re.split(r"[/\\]+", str(root)) if p]
    body = r"[/\\]+".join(parts)
    if not re.match(r"^[A-Za-z]:", str(root)):
        body = r"[/\\]+" + body
    flags = re.IGNORECASE if re.match(r"^[A-Za-z]:", str(root)) else 0
    return re.compile(body + r"[/\\]+run-[A-Za-z0-9_-]+(?P<rest>(?:[/\\][^\s\"']*)?)", flags)


def display(text: str, root: Path | None = None) -> str:
    """*text* as a person should read it: every run scratch path becomes ``<work>/…``.

    The real path names the temp folder (and so the user), and nothing in it outlives the run.
    Native prints the same spelling (``apps/cli`` ``display_path``). The one path shown for real
    is a preview clip, which the user is meant to open.
    """
    pattern = _run_folder_pattern(root if root is not None else scratch_root())
    return pattern.sub(lambda m: "<work>" + m.group("rest").replace("\\", "/"), text)


# ── keep every step's output (`--keep-intermediates` and its request words) ──────────────────
#
# The flag on `knaif run` in both CLIs, and request words turn it on too (owner, 2026-10-14).
# A keep verb next to a scope: intermediate files, everything, all the files, every step.
# English allows only determiners between them, so "keep the audio, convert everything" does not
# match; languages that put the verb last match in either order within a word or two. `save` and
# its translations are never keep verbs: they name outputs ("save all the files as mp3"). Rust
# mirror: `knaif_core::scratch::wants_everything_kept`; both read
# contracts/parity/keep_request_cases.json in their tests. No lookaround (the Rust regex crate
# has none), so one pattern set serves both runtimes.
_KEEP_NEGATION = re.compile(r"\b(?:don['’]?t|do\s+not|never|no\s+need\s+to)\s+(?:keep|leave)\b")
_KEEP_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        # en
        r"\b(?:keep|leave)\s+(?:(?:all|of|the|my|these|those|any)\s+)*"
        r"(?:intermediate\w*|in-between\s+files?|everything)\b",
        r"\b(?:keep|leave)\s+(?:all\s+(?:of\s+)?(?:the\s+)?|every\s+|each\s+)"
        r"(?:files?|outputs?|results?|versions?|steps?)\b",
        r"\b(?:keep|leave)\s+(?:them|these|those)\s+all\b",
        # de: either order
        r"\b(?:behalt\w*|behält|beibehalten|aufbewahren|aufheben)\s+(?:\S+\s+){0,1}?"
        r"(?:zwischen\w*|alles|alle\s+dateien)\b",
        r"\b(?:zwischen\w*|alles|alle\s+dateien)\s+(?:\S+\s+){0,1}?"
        r"(?:behalt\w*|behält|beibehalten|aufbewahren|aufheben)\b",
        # es, fr
        r"\b(?:conserv\w*|manten\w*|mantén|deja\w*|gard\w*)\s+(?:\S+\s+){0,2}?"
        r"(?:intermedi\w*|interm[ée]diaires?|todo|tout|todos\s+los\s+archivos|"
        r"tous\s+les\s+fichiers)\b",
        # bg
        r"\b(?:запаз\w*|остав\w*)\s+(?:\S+\s+){0,1}?(?:междинн\w*|всичко)\b",
        # zh: written without spaces
        r"保留.{0,2}?(?:中间|所有|全部)",
        r"(?:全部|都|所有)保留",
    )
)


def wants_everything_kept(utterance: str | None) -> bool:
    """True when the request asks to keep every step's output ("keep the intermediate files",
    "keep everything", "保留中间文件"): the same as ``--keep-intermediates``. A negated English
    request ("don't keep the intermediate files") keeps the default."""
    if not utterance:
        return False
    text = _KEEP_NEGATION.sub(" ", utterance.lower())
    return any(p.search(text) for p in _KEEP_PATTERNS)


def debug_keep() -> bool:
    """True when the scratch is kept: ``KNAIF_DEBUG`` (debugging) or ``KNAIF_KEEP_SCRATCH``
    (the eval lane, which grades chain intermediates from it). Same rule in the native runtime."""
    return any(os.environ.get(k) for k in ("KNAIF_DEBUG", "KNAIF_KEEP_SCRATCH"))


def sweep_stale(root: Path, *, now: float | None = None) -> list[Path]:
    """Remove ``run-*`` folders under *root* older than :data:`STALE_AFTER_S`. Returns them."""
    if not root.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - STALE_AFTER_S
    removed: list[Path] = []
    for entry in root.glob("run-*"):
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(entry)
        except OSError:
            continue
    return removed


class RunScratch:
    """One run's scratch folder. Use as a context manager around the run's execution.

    The path is fixed at construction, so a plan can be rewritten to name it before anything
    executes. The folder itself is created on ``__enter__`` (unless *dry_run*) and removed on
    ``__exit__`` whatever happened (unless ``KNAIF_DEBUG``).
    """

    def __init__(self, *, dry_run: bool, root: Path | None = None) -> None:
        self.dry_run = dry_run
        self.root = root if root is not None else scratch_root()
        self.path = self.root / f"run-{os.getpid()}-{secrets.token_hex(4)}"
        self._depth = 0

    def __enter__(self) -> RunScratch:
        # Re-entrant: a stage that needs the folder early (URL downloads, before preflight)
        # enters it first; the folder is created once and removed when the outermost exits.
        if self._depth == 0 and not self.dry_run:
            sweep_stale(self.root)
            self.path.mkdir(parents=True, exist_ok=False)
        self._depth += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._depth -= 1
        if self._depth == 0 and not self.dry_run and not debug_keep():
            shutil.rmtree(self.path, ignore_errors=True)

    def contains(self, path: str | Path) -> bool:
        """True when *path* lies inside this run's folder (lexically, after resolving)."""
        try:
            Path(path).resolve().relative_to(self.path.resolve())
        except ValueError:
            return False
        return True
