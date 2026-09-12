"""Execute a rendered command chain inside one per-row working directory.

Shared by the eval runner (grading a model's multi-intent plan) and the notebook baseline
reviewer (validating a multi-output row's reference commands). Kept free of UI / notebook
dependencies so it can run inside the eval runtime.

**One path rule, replacing two.** Every path token — each ``-i`` argument and the output — is
re-rooted at the row directory **preserving its directory structure**. That single rule
replaces ``_run_artifact``'s asymmetric rewrite (input to the fixture dir, output somewhere
else, so the two could never be equal) and this module's own prior-output-then-fixture
basename lookup.

Two things follow from it, and both are the point rather than side effects:

* **A collision reaches ffmpeg.** ``ffmpeg_175`` renders a command whose output equals its
  input; re-rooting both the same way keeps them equal, so Python now reproduces the failure
  native already hits instead of tidying it away before execution.
* **A chain feeds itself.** Step 1 reads what step 0 wrote because both resolve into the same
  directory — not because of a lookup that guesses which of two directories a basename meant.

⚠️ **Never by basename.** It merges ``a/clip.mp4`` with ``b/clip.mp4``, and turns the
perfectly legal ``source/clip.mp4 -> exports/clip.mp4`` into a false collision. Native
preserves directories, so a basename rule would also make the lanes disagree about paths —
the opposite of the goal.

*Why not execute verbatim:* Python renders **absolute** sandbox paths where native renders
bare names, so running Python's command untouched would write into the real fixtures
directory and corrupt it. Closing that difference means re-pointing the agent's sandbox per
row at *plan* time, which changes what the planner validates — a coupled change this work
deliberately avoids mid-measurement. It is a genuine lane difference, and it is recorded here
rather than hidden.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5b.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .provisioning import provision_row_dir

#: Tokens whose following argument is a path the command reads.
_INPUT_FLAGS = {"-i"}


def _relative_part(token: str, anchors: list[Path]) -> tuple[Path, bool]:
    """``(part to re-root, collapsed)`` — the token's path relative to its first anchor.

    An absolute path matching no anchor falls back to its **basename**, which loses a
    directory. That is the last resort, not the rule, and the second element says when it
    happened so the caller can report it instead of silently producing a different command.

    Anchors are resolved by the caller. They must be: the CLI's default fixture directory is
    *relative* (``sandbox/fixtures/ffmpeg``) while Python renders *absolute* paths, so an
    unresolved anchor never matches — and every path collapsed to its basename, turning the
    perfectly legal ``source/clip.mp4 -> exports/clip.mp4`` into ``row/clip.mp4 ->
    row/clip.mp4``: the false collision the design forbids, on the default code path.
    """
    p = Path(token)
    if not p.is_absolute():
        return p, False
    for anchor in anchors:
        try:
            return p.relative_to(anchor), False
        except ValueError:
            continue
    return Path(p.name), True


def resolve_command(
    command: str, row_dir: Path | str, anchors: list[Path] | None = None
) -> tuple[str, list[str]]:
    """``(resolved command, collapsed tokens)`` — every path token re-rooted at *row_dir*.

    Pure: it touches no filesystem and decides nothing from what happens to exist. Availability
    is a property of the directory the command executes in, which is what provisioning
    guarantees — checking it here is how the old lookup ended up choosing between two
    directories by basename.

    *collapsed* lists any absolute token that matched no anchor and was therefore reduced to
    its basename. It is empty on every path this harness is expected to take; a non-empty list
    means the command reached outside the eval tree and the re-rooting lost a directory.
    """
    row_dir = Path(row_dir)
    anchors = [Path(a).resolve() for a in (anchors or [])]
    toks = command.split()
    if not toks:
        return command, []

    collapsed: list[str] = []

    def reroot(tok: str) -> str:
        part, lost = _relative_part(tok, anchors)
        if lost:
            collapsed.append(tok)
        return str(row_dir / part)

    for i, tok in enumerate(toks):
        if tok in _INPUT_FLAGS and i + 1 < len(toks):
            toks[i + 1] = reroot(toks[i + 1])
    toks[-1] = reroot(toks[-1])
    return " ".join(toks), collapsed


def run_command_chain(
    commands: list[str],
    fixture_dir: Path | str,
    out_dir: Path | str,
    *,
    timeout: int = 120,
    fixture_file: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Provision ``fixture_dir`` into ``out_dir``, then run ``commands`` there in sequence.

    Provisioning lives here rather than in each caller so there is exactly one answer to
    "what was this command run against". The eval runner, the native lane and the notebook
    baseline reviewer all reach it through this function; when they each did their own, the
    reviewer did none at all and resolved inputs out of the shared fixture directory.
    Copying is idempotent, so a caller that has already provisioned loses nothing.

    The chain halts on the first non-zero exit, since later commands depend on earlier
    outputs. Returns one dict per executed command:
    ``{command, resolved_command, returncode, stderr, output}`` — ``command`` is what the plan
    rendered and ``resolved_command`` is what ran, so a report can show both.
    """
    fixture_dir = Path(fixture_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if fixture_dir.is_dir():
        # Fresh: a row directory is deterministic and a kept failure from an earlier run
        # would otherwise satisfy a later plan's missing input.
        provision_row_dir(fixture_dir, out_dir, fresh=True)
    anchors = [out_dir.resolve(), fixture_dir.resolve()]
    if fixture_file is not None:
        anchors.append(Path(fixture_file).resolve().parent)
    results: list[dict[str, Any]] = []

    for command in commands:
        resolved, collapsed = resolve_command(command, out_dir, anchors)
        toks = resolved.split()
        output = Path(toks[-1]) if toks else out_dir
        output.parent.mkdir(parents=True, exist_ok=True)

        try:
            proc = subprocess.run(toks, capture_output=True, text=True, timeout=timeout)
            returncode, stderr = proc.returncode, proc.stderr
        except FileNotFoundError:
            returncode, stderr = 127, "command not found on PATH"
        except subprocess.TimeoutExpired:
            returncode, stderr = 124, f"timeout after {timeout}s"

        results.append(
            {
                "command": command,
                "resolved_command": resolved,
                "returncode": returncode,
                "stderr": stderr,
                "output": output,
                # Empty on every expected path. Non-empty means a token pointed outside the
                # eval tree and re-rooting lost a directory — recorded rather than silent,
                # because that is how a false collision would otherwise look like a model bug.
                "collapsed_paths": collapsed,
            }
        )
        if returncode != 0:
            break

    return results
