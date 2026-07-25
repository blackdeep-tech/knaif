"""ffmpeg skill — reporting module (see handlers.py / SPEC.md)."""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Any

from ._engine import _coerce_inputs, _load_yaml

# ─────────────────────────────────────────────────────────────────────────────
# Summarizers — produce a short human-readable clause for each intent tool.
# ─────────────────────────────────────────────────────────────────────────────


def _fmt_files(value: Any) -> str:
    """Format a string or list of paths as a short human-readable list of basenames."""
    if value is None:
        return "the inputs"
    if isinstance(value, str):
        return Path(value).name or value
    if isinstance(value, list):
        names = [Path(str(v)).name or str(v) for v in value if v]
        if not names:
            return "the inputs"
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} and {names[1]}"
        return ", ".join(names[:-1]) + f", and {names[-1]}"
    return str(value)


def _load_platform_summary(platform: str, skill_dir: Path) -> str:
    """Return a short technical spec string for *platform*, e.g. '720p MP4 H.264/AAC'.

    Returns an empty string when the profile file cannot be found or parsed.
    """
    try:
        path = skill_dir / "profiles" / "platforms" / f"{platform}.yaml"
        if not path.exists():
            return ""
        p = _load_yaml(path)
        max_h = p.get("max_height")
        container = (p.get("container") or "mp4").upper()
        v_codec = (
            (p.get("video_codec") or "H.264")
            .upper()
            .replace("H264", "H.264")
            .replace("H265", "H.265")
        )
        a_codec = (p.get("audio_codec") or "AAC").upper()
        parts: list[str] = []
        if max_h:
            parts.append(f"{max_h}p")
        parts.append(container)
        parts.append(f"{v_codec}/{a_codec}")
        return " ".join(parts)
    except Exception:  # noqa: BLE001
        return ""


def _load_quality_hint(quality: str, skill_dir: Path) -> str:
    """Return a short quality hint, e.g. 'CRF 28, slow preset'."""
    try:
        path = skill_dir / "profiles" / "quality" / f"{quality}.yaml"
        if not path.exists():
            return ""
        q = _load_yaml(path)
        crf = q.get("video_crf")
        preset = q.get("encoder_preset")
        if crf and preset:
            return f"CRF {crf}, {preset} preset"
        if crf:
            return f"CRF {crf}"
        return ""
    except Exception:  # noqa: BLE001
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Preflights — run before the approval gate to surface errors early.
# ─────────────────────────────────────────────────────────────────────────────


def _preflight_inputs(
    args: dict[str, Any],
    *,
    root: Path,
    sandbox: Path | None = None,
    planned_output_names: set[str] | None = None,
    **kwargs: Any,
) -> list[str]:
    """Return error strings for any input paths that do not exist."""
    raw = args.get("inputs") or args.get("input") or args.get("paths")
    if not raw:
        return []
    try:
        paths = _coerce_inputs(raw)
    except ValueError:
        return []
    errors: list[str] = []
    base = sandbox if sandbox is not None else root
    for raw_path in paths:
        if not isinstance(raw_path, str) or raw_path.startswith("$"):
            continue
        # Skip files that will be produced by an earlier step in the same plan.
        if planned_output_names and Path(raw_path).name in planned_output_names:
            continue
        p = Path(raw_path)
        if not p.is_absolute():
            p = (base / raw_path).resolve()
        else:
            p = p.resolve()
            if sandbox is not None:
                try:
                    p.relative_to(sandbox.resolve())
                except ValueError:
                    errors.append(f"{Path(raw_path).name!r} is outside the sandbox")
                    continue
        if not p.exists():
            name = Path(raw_path).name
            location = "sandbox" if sandbox is not None else "working directory"
            # Chaining check: if an earlier step produces a similarly-named file,
            # this is almost certainly a mismatched intermediate (small-LLM typo),
            # not a missing user file — say so and suggest the produced name.
            close = difflib.get_close_matches(
                name, sorted(planned_output_names or []), n=1, cutoff=0.7
            )
            if close:
                errors.append(
                    f"{name!r} is not produced by any earlier step "
                    f"(did you mean {close[0]!r}, produced by an earlier step?). "
                    f"Chained intermediate filenames must match an earlier step's output exactly."
                )
                continue
            hint = (
                " (model may have dropped the directory — try --dry-run to inspect the plan)"
                if name == raw_path and "/" not in raw_path and "\\" not in raw_path
                else ""
            )
            errors.append(f"{name!r} not found in {location}{hint}")
    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Result formatter — convert execution results into structured CLI items.
# ─────────────────────────────────────────────────────────────────────────────


def _format_results(results: list[dict[str, Any]], *, dry_run: bool) -> list[dict[str, str]]:
    """Convert a list of execution results into structured items for the CLI.

    Output files are sourced from ``run_batch`` / ``run_concat`` outputs rather
    than ``generate_report.files`` because the latter is readonly and may be
    stripped by ``optimize_plan`` in multi-intent plans (its ``$report`` output
    is never referenced by a downstream step). ``run_batch``/``run_concat`` are
    non-readonly and always preserved.
    """
    items: list[dict[str, str]] = []
    inspect_errors: list[str] = []
    batch_outputs: list[dict[str, Any]] = []

    for step in results:
        tool = step["tool"]
        res = step.get("result") or {}
        if tool == "inspect_media":
            for e in res.get("errors") or []:
                msg = e.get("error") if isinstance(e, dict) else str(e)
                if msg:
                    inspect_errors.append(msg)
        elif tool in ("run_batch", "run_concat"):
            batch_outputs.extend(res.get("outputs") or [])

    for err in inspect_errors:
        items.append({"kind": "error", "message": err})
    if inspect_errors:
        return items  # Bail out on errors, matches old behavior.

    if dry_run:
        if batch_outputs:
            for out in batch_outputs:
                cmd = out.get("command") or []
                items.append(
                    {
                        "kind": "command",
                        "message": " ".join(str(c) for c in cmd),
                    }
                )
        else:
            items.append({"kind": "info", "message": "(nothing to execute)"})
    else:
        produced = []
        for out in batch_outputs:
            if out.get("returncode", 0) != 0:
                stderr = (out.get("stderr_tail") or "").strip()
                msg = f"ffmpeg failed (exit {out['returncode']})"
                if stderr:
                    # Skip the generic "Conversion failed!" sign-off and ffmpeg
                    # progress lines to surface the line that names the real error.
                    skip = {"conversion failed!", "error while", ""}
                    diag = next(
                        (
                            ln.strip()
                            for ln in reversed(stderr.splitlines())
                            if ln.strip().lower() not in skip
                            and not ln.strip().startswith("frame=")
                            and not ln.strip().startswith("size=")
                        ),
                        None,
                    )
                    if diag:
                        msg = f"{msg}: {diag}"
                items.append({"kind": "error", "message": msg})
            elif out.get("output"):
                produced.append(out["output"])
        if produced:
            for f in produced:
                items.append({"kind": "output", "message": Path(f).name})
        elif not any(i["kind"] == "error" for i in items):
            items.append({"kind": "info", "message": "(no output files produced)"})

    return items


# ─────────────────────────────────────────────────────────────────────────────
# Artifact runner — re-execute a rendered ffmpeg command against a fixture.
# Used by the eval suite for end-to-end output verification.
# ─────────────────────────────────────────────────────────────────────────────


def _run_artifact(command_str: str, fixture_path: Path, out_dir: Path) -> Path | None:
    """Re-execute an ffmpeg artifact against a fixture file."""
    toks = command_str.split()
    if not toks or toks[0] != "ffmpeg":
        return None
    try:
        i_idx = toks.index("-i")
        toks[i_idx + 1] = str(fixture_path)
    except (ValueError, IndexError):
        return None
    original_output = Path(toks[-1]).name
    output_path = out_dir / original_output
    toks[-1] = str(output_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(toks, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return output_path if output_path.exists() else None
