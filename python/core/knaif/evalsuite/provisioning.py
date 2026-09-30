"""Per-row fixture provisioning, shared by both eval lanes.

One rule, in one place, so the two lanes cannot drift on *what the command was run against*.
Native already worked this way — every utterance gets its own work directory holding every
fixture, and the binary runs with ``cwd=work_dir`` and no path rewriting. Python ran the whole
corpus in one shared sandbox and rewrote paths on the way into ffmpeg, which is why it could
not reproduce failures native hit.

**Copy, not hardlink.** The native lane hard-linked to avoid ~7 GB of duplication across 847
utterances. That is a real cost, and it was the wrong trade: every rendered command carries
``-y``, so a plan whose output lands on a fixture's *name* writes **through the link and
corrupts the shared fixture for every later row**. Nothing would have caught it —
``.cache.json`` hashes the generation command, not the bytes — and every score after the
corruption would be measured against media nobody chose. Delete a row's work dir once its
artifacts are graded (see ``cleanup_row_dir``) and the disk cost is bounded by concurrency
rather than by corpus size.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5b.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

#: Bookkeeping that lives in the fixture directory but is not media.
_NOT_MEDIA = {".cache.json"}


def _media_files(fixture_dir: Path) -> list[Path]:
    """Every fixture file, recursively, in a stable order."""
    out = [
        p
        for p in sorted(fixture_dir.rglob("*"))
        if p.is_file() and p.name not in _NOT_MEDIA and not p.name.startswith(".")
    ]
    return out


def provision_row_dir(fixture_dir: Path | str, row_dir: Path | str, *, fresh: bool = True) -> int:
    """Copy every fixture into *row_dir*, preserving directory structure. Returns the count.

    **Every** fixture, not only the one the row declares: Python's executing verifiers ran the
    whole corpus in one shared sandbox, so every fixture was visible to every row. Provisioning
    only the declared one is stricter than the reference behaviour, and the difference was
    scored against the runtime instead of the harness — ``ffmpeg_084`` plans ``clip.mp4``, a
    real fixture, while the row declares ``clip_4k.mp4``.

    **Output isolation is the property worth having** and is untouched: each row still gets its
    own directory, so a file produced by one row can never satisfy another.

    *fresh* (default) empties the directory first, so isolation holds across *runs* too — pass
    ``fresh=False`` only to top up a directory you already own within one run.
    """
    fixture_dir = Path(fixture_dir).resolve()
    row_dir = Path(row_dir)
    if fixture_dir == row_dir.resolve() or fixture_dir in row_dir.resolve().parents:
        raise ValueError(
            f"row dir {row_dir} is inside the fixture dir {fixture_dir}: provisioning would "
            "copy a row's outputs into every later row's inventory"
        )
    if fresh and row_dir.exists():
        # Row directories are deterministic (`<id>__<idx>`) and failed ones are deliberately
        # kept, so overlaying onto whatever is there lets an intermediate from an earlier run
        # satisfy a later plan's *missing* input — a pass that the plan did not earn. Native
        # already removes its work dir first; this is the same rule in the shared place.
        shutil.rmtree(row_dir, ignore_errors=True)
    row_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in _media_files(fixture_dir):
        dest = row_dir / src.relative_to(fixture_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        count += 1
    return count


def cleanup_row_dir(row_dir: Path | str, *, keep: bool = False) -> None:
    """Remove a graded row's work directory. Kept on failure, for debugging.

    Copy-all is ~8.6 MB per row, so a full corpus is ~7 GB of work dirs — and they were never
    cleaned up (each fixture showed ~857 links). Bounding it to the rows currently in flight is
    what makes copying affordable.
    """
    if keep:
        return
    shutil.rmtree(Path(row_dir), ignore_errors=True)


def fixture_content_hashes(fixture_dir: Path | str) -> dict[str, str]:
    """SHA-256 per fixture, keyed by path relative to *fixture_dir* (POSIX separators).

    Recorded alongside ``.cache.json``'s command hash, which keeps its own job of deciding
    whether a fixture needs regenerating. A command hash cannot notice a fixture whose *bytes*
    changed underneath it, which is exactly the failure hardlinked provisioning used to cause.
    With this, a score traces to the media it was measured against.
    """
    fixture_dir = Path(fixture_dir)
    hashes: dict[str, str] = {}
    for path in _media_files(fixture_dir):
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
        hashes[path.relative_to(fixture_dir).as_posix()] = digest.hexdigest()
    return hashes


def verify_fixture_integrity(fixture_dir: Path | str) -> list[str]:
    """Compare the fixtures on disk against the content hashes recorded for them.

    Returns one human-readable line per discrepancy, empty when everything matches or when
    no hashes have been recorded yet (an older ``.cache.json``, or a directory that has never
    been regenerated — neither is a failure).

    Checked at the start of an executing run, because a score is only as meaningful as the
    media behind it. Hard-linked provisioning used to let one row's output overwrite a shared
    fixture, and every row after it was then measured against something nobody chose — with
    nothing in the record to say so.
    """
    import json

    fixture_dir = Path(fixture_dir)
    cache_path = fixture_dir / ".cache.json"
    if not cache_path.exists():
        return []
    try:
        recorded = (json.loads(cache_path.read_text(encoding="utf-8")) or {}).get("__content__")
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(recorded, dict) or not recorded:
        return []

    actual = fixture_content_hashes(fixture_dir)
    problems: list[str] = []
    for name, digest in sorted(recorded.items()):
        if name not in actual:
            problems.append(f"fixture {name!r} is recorded but missing")
        elif actual[name] != digest:
            problems.append(f"fixture {name!r} changed since it was generated")
    return problems
