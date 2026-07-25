"""Terminal + JSON scoreboard rendering + multi-arm HTML/Markdown report."""

from __future__ import annotations

import json
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote

_WIDTH = 88


# ── existing terminal helpers (unchanged) ─────────────────────────────────────


def _write(file: Any, text: str) -> None:
    """Write *text*, degrading unencodable characters instead of raising.

    A default Windows console is cp1252, which cannot encode the box-drawing rules ('═',
    '─') this report is built from — nor CJK/Cyrillic corpus utterances. An uncaught
    UnicodeEncodeError here killed the process *after* a full eval had run but *before* the
    scoreboard was saved, losing the entire run. Rendering must never be able to do that.
    """
    try:
        file.write(text)
    except UnicodeEncodeError:
        enc = getattr(file, "encoding", None) or "ascii"
        file.write(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _hr(char: str = "─") -> str:
    return char * _WIDTH


def _fmt_score(score: float | None) -> str:
    if score is None:
        return "  n/a"
    return f"{score:5.1%}"


def print_scoreboard(
    scoreboard: dict[str, Any], *, backend: str = "", verbose: bool = False, file: Any = None
) -> None:
    """Print a scoreboard to *file* (default: stdout)."""
    if file is None:
        file = sys.stdout

    lines: list[str] = []
    lines.append(_hr("═"))
    label = f"EVAL SCOREBOARD — {backend}" if backend else "EVAL SCOREBOARD"
    lines.append(f"  {label}")
    lines.append(_hr("═"))
    lines.append("")
    lines.append(f"  Verifier          : {scoreboard.get('verifier', '?')}")
    lines.append(f"  Total rows        : {scoreboard.get('total', 0)}")
    lines.append(f"  Outcome accuracy  : {_fmt_score(scoreboard.get('outcome_accuracy'))}")
    lines.append(f"  Avg knaif score   : {_fmt_score(scoreboard.get('avg_knaif_score'))}")
    lines.append(f"  Avg baseline score: {_fmt_score(scoreboard.get('avg_baseline_score'))}")

    intent = scoreboard.get("intent_metrics") or {}
    if intent:
        lines.append("")
        lines.append(f"  Tool accuracy     : {_fmt_score(intent.get('tool_accuracy'))}")
        arg_acc = intent.get("arg_accuracy")
        if arg_acc is not None:
            lines.append(f"  Arg accuracy      : {_fmt_score(arg_acc)}")
        lines.append(f"  Schema validity   : {_fmt_score(intent.get('schema_validity'))}")

    tta = scoreboard.get("time_to_artifact_ms")
    if tta:
        lines.append("")
        lines.append("  Time to artifact (plan rows, ms; first row excluded as warmup):")
        lines.append(
            f"    mean={tta['mean_ms']:>7.0f}  p50={tta['p50_ms']:>7.0f}  "
            f"p95={tta['p95_ms']:>7.0f}  max={tta['max_ms']:>7.0f}  "
            f"(n={tta['count']}, total={tta['total_s']:.1f}s)"
        )

    lines.append("")
    lines.append(_hr())
    lines.append(
        f"  {'Tag':<24} {'Total':>6} {'Outcome':>8} {'Knaif':>7} {'Baseline':>9} "
        f"{'p50 ms':>8} {'p95 ms':>8}"
    )
    lines.append(_hr())

    for tag, data in sorted((scoreboard.get("by_tag") or {}).items()):
        tag_tta = data.get("time_to_artifact_ms") or {}
        p50 = f"{tag_tta['p50_ms']:.0f}" if tag_tta else "n/a"
        p95 = f"{tag_tta['p95_ms']:.0f}" if tag_tta else "n/a"
        lines.append(
            f"  {tag:<24} {data['total']:>6} "
            f"{_fmt_score(data['outcome_accuracy']):>8} "
            f"{_fmt_score(data.get('avg_knaif_score')):>7} "
            f"{_fmt_score(data.get('avg_baseline_score')):>9} "
            f"{p50:>8} {p95:>8}"
        )

    lines.append(_hr())
    lines.append("")

    if verbose:
        rows = scoreboard.get("rows") or []
        if rows:
            lines.append("  PER-ROW DETAIL")
            lines.append(_hr())
            _write(file, "\n".join(lines) + "\n")
            lines = []
            for row in rows:
                print_row_detail(row, file=file)
            lines.append(_hr())
            lines.append("")

    _write(file, "\n".join(lines) + "\n")


def print_row_detail(row: dict[str, Any], *, file: Any = None) -> None:
    """Print a single scored row in detail."""
    if file is None:
        file = sys.stdout
    correct_mark = "OK" if row["outcome_correct"] else "FAIL"
    latency = row.get("latency_ms")
    latency_str = f"  {latency:.0f}ms" if latency is not None else ""
    _write(
        file,
        f"  [{row['id']}] {row['utterance'][:70]}\n"
        f"    outcome: {row['actual_outcome']} (expected {row['expected_outcome']}) "
        f"[{correct_mark}]{latency_str}\n",
    )
    if row.get("knaif_score") is not None:
        _write(
            file,
            f"    knaif={row['knaif_score']:.2f}  baseline={row.get('baseline_score', 'n/a')}\n",
        )
    is_failure = not row["outcome_correct"] or bool(row.get("knaif_failed"))
    if is_failure:
        artifact = row.get("artifact") or "none"
        _write(file, f"    generated: {artifact}\n")
        plan = row.get("plan")
        if plan:
            steps = (plan or {}).get("plan") or []
            if steps:
                tool = steps[0].get("tool", "?")
                args = steps[0].get("args", {})
                _write(file, f"    tool     : {tool}  args={args}\n")
    matched = row.get("knaif_matched") or []
    failed = row.get("knaif_failed") or []
    for criterion in matched:
        _write(file, f"      + {criterion}\n")
    for criterion in failed:
        _write(file, f"      - {criterion}\n")
    if row.get("error"):
        _write(file, f"    error: {row['error']}\n")


def save_scoreboard_json(scoreboard: dict[str, Any], path: Path | str) -> None:
    """Write scoreboard as JSON to *path*."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(scoreboard, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def print_baseline_row(row: dict[str, Any], *, file: Any = None) -> None:
    """Print the baseline freeform command side-by-side comparison."""
    if file is None:
        file = sys.stdout
    _write(
        file,
        f"\n  === {row['id']} ===\n"
        f"  Utterance: {row['utterance']}\n"
        f"  Knaif    : {row.get('knaif_artifact', 'n/a')}\n"
        f"  Baseline : {row.get('baseline_artifact', 'n/a')}\n",
    )


# ── multi-arm report ──────────────────────────────────────────────────────────


@dataclass
class ArmEntry:
    id: str
    utterance_idx: int
    utterance: str
    score: float | None
    matched: list[str]
    failed: list[str]
    artifact_path: str | None
    baseline_path: str | None
    outcome_correct: bool | None
    tags: list[str] = field(default_factory=list)
    latency_ms: float | None = None
    actual_outcome: str | None = None
    is_warmup: bool = False


def _arm_latency_stats(entries: list[ArmEntry]) -> dict[str, float] | None:
    """Compute time-to-artifact stats (ms) over plan-outcome entries.

    Skips entries marked is_warmup so first-row model-load cost does not skew
    the mean.  Returns None when no qualifying entries exist.
    """
    series = sorted(
        float(e.latency_ms)
        for e in entries
        if e.latency_ms is not None and not e.is_warmup and e.actual_outcome == "plan"
    )
    if not series:
        return None
    n = len(series)

    def _q(p: float) -> float:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return series[idx]

    return {
        "count": n,
        "mean_ms": sum(series) / n,
        "p50_ms": _q(0.50),
        "p95_ms": _q(0.95),
        "max_ms": series[-1],
    }


_SKIP_JSON: frozenset[str] = frozenset({"review_log.json"})


def _detect_format(data: dict) -> str:
    # score-external emits a backward-compat "entries" list (the local runner never
    # does), even though it now ALSO emits local-shaped "rows". Detect by "entries"
    # so a score-external file is still named by its arm subdir, not the "score" stem.
    return "score_external" if "entries" in data else "local_runner"


def load_arm_entries(
    json_path: Path,
    corpus_rows: list[Any],
    skill: str = "",
) -> tuple[str, list[ArmEntry]]:
    """Load a score JSON file and return (arm_name, entries).

    Handles both the score-external format and the local runner format.
    Missing utterance/tag data is backfilled from corpus_rows.
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    rows_by_id = {r.id: r for r in corpus_rows}
    fmt = _detect_format(data)

    if fmt == "score_external":
        arm_name = json_path.parent.name
    else:  # local_runner: name from "<skill>_<backend>_<verifier>.json"
        verifier = data.get("verifier", "")
        stem = json_path.stem
        arm_name = stem
        if skill and stem.startswith(f"{skill}_"):
            arm_name = stem[len(skill) + 1 :]
        if verifier and arm_name.endswith(f"_{verifier}"):
            arm_name = arm_name[: -len(verifier) - 1]

    # Prefer the local-shaped "rows" list — the local runner always has it, and
    # score-external now emits it too (richer than "entries": it also carries
    # clarify/reject rows, latency, and outcome). Fall back to legacy "entries".
    if data.get("rows") is not None:
        entries: list[ArmEntry] = []
        for row_data in data["rows"]:
            corpus_row = rows_by_id.get(row_data["id"])
            tags = row_data.get("tags") or (list(corpus_row.tags) if corpus_row else [])
            entries.append(
                ArmEntry(
                    id=row_data["id"],
                    utterance_idx=row_data.get("utterance_idx", 0),
                    utterance=row_data.get("utterance", ""),
                    score=row_data.get("knaif_score"),
                    matched=row_data.get("knaif_matched") or [],
                    failed=row_data.get("knaif_failed") or [],
                    artifact_path=row_data.get("artifact_path"),
                    baseline_path=None,
                    outcome_correct=row_data.get("outcome_correct"),
                    tags=tags,
                    latency_ms=row_data.get("latency_ms"),
                    actual_outcome=row_data.get("actual_outcome"),
                    is_warmup=bool(row_data.get("is_warmup")),
                )
            )
        return arm_name, entries

    # legacy score-external: only the "entries" list is present
    entries = []
    for entry in data.get("entries") or []:
        row_id = entry["id"]
        utt_idx = entry.get("utterance_idx", 0)
        corpus_row = rows_by_id.get(row_id)
        utterance = ""
        tags = []
        if corpus_row:
            utterance = (
                corpus_row.utterances[utt_idx] if utt_idx < len(corpus_row.utterances) else ""
            )
            tags = list(corpus_row.tags)
        entries.append(
            ArmEntry(
                id=row_id,
                utterance_idx=utt_idx,
                utterance=utterance,
                score=entry.get("score"),
                matched=entry.get("matched") or [],
                failed=entry.get("failed") or [],
                artifact_path=entry.get("artifact_path"),
                baseline_path=entry.get("baseline_path"),
                outcome_correct=None,
                tags=tags,
            )
        )
    return arm_name, entries


def discover_arms(
    results_dir: Path,
    corpus_rows: list[Any],
    skill: str = "",
) -> dict[str, list[ArmEntry]]:
    """Scan results_dir for arm entries keyed by arm name.

    Two layouts are supported:

    * **Flat** — JSON files sit directly in *results_dir* (output of
      ``eval-success --save DIR``).  Arm names are derived from the filenames.
      This layout is detected first; if any flat files are found the function
      returns immediately without scanning subdirectories.

    * **Subdirs** — each subdirectory of *results_dir* may contain one or more
      JSON files (external-agent or local-runner format).  When the same arm
      name appears in more than one subdirectory (e.g. ``local2/`` and
      ``local3/`` both contain ``ffmpeg_qwen3-4b_success.json``), *every*
      occurrence is qualified as ``{subdir}/{arm}`` so the arms stay distinct
      rather than being merged.
    """
    arms: dict[str, list[ArmEntry]] = {}

    # ── flat files directly in results_dir ───────────────────────────────────
    flat_arms: dict[str, list[ArmEntry]] = {}
    for json_file in sorted(results_dir.glob("*.json")):
        if json_file.name in _SKIP_JSON:
            continue
        try:
            arm_name, entries = load_arm_entries(json_file, corpus_rows, skill)
        except Exception:
            continue
        if not entries:
            continue
        flat_arms.setdefault(arm_name, []).extend(entries)
    if flat_arms:
        return flat_arms

    # ── subdirectory layout ───────────────────────────────────────────────────
    # Collect (subdir_name, entries) per arm_name, then resolve collisions.
    by_arm: dict[str, list[tuple[str, list[ArmEntry]]]] = {}
    for child in sorted(results_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        for json_file in sorted(child.glob("*.json")):
            if json_file.name in _SKIP_JSON:
                continue
            try:
                arm_name, entries = load_arm_entries(json_file, corpus_rows, skill)
            except Exception:
                continue
            if not entries:
                continue
            by_arm.setdefault(arm_name, []).append((child.name, entries))

    for arm_name, occurrences in by_arm.items():
        if len(occurrences) == 1:
            arms[arm_name] = occurrences[0][1]
        else:
            # Same arm name in multiple subdirs — qualify every one so they stay distinct.
            for subdir_name, entries in occurrences:
                arms[f"{subdir_name}/{arm_name}"] = entries

    return arms


def _build_cross_arm_index(
    arms: dict[str, list[ArmEntry]],
) -> dict[tuple[str, int], dict[str, ArmEntry]]:
    cross: dict[tuple[str, int], dict[str, ArmEntry]] = {}
    for arm_name, entries in arms.items():
        for e in entries:
            cross.setdefault((e.id, e.utterance_idx), {})[arm_name] = e
    return cross


def _review_status_str(review_log: Any, row_id: str, utt_idx: int) -> str:
    if review_log is None:
        return ""
    entry = review_log.get(row_id, utt_idx)
    if entry is None:
        return ""
    status = entry.get("status", "")
    _icons = {"reviewed": "✓", "rejected": "✗"}
    icon = _icons.get(status, "")
    return f" {icon} {status}".strip() if status else ""


# ── Markdown renderer ─────────────────────────────────────────────────────────


def render_report_md(
    arms: dict[str, list[ArmEntry]],
    rows_by_id: dict,
    review_log: Any = None,
) -> str:
    arm_names = sorted(arms.keys())
    cross = _build_cross_arm_index(arms)

    lines: list[str] = [
        "# Eval Report",
        "",
        "> **Note:** A passing score means 'didn't fail a deterministic check,' not 'did the right thing.'",
        "",
    ]

    # Summary
    lines += ["## Summary", ""]
    lines.append(
        "| Arm | Rows | Pass rate | Avg score | Time-to-artifact mean ms | p50 ms | p95 ms |"
    )
    lines.append(
        "|-----|------|-----------|-----------|-------------------------|--------|--------|"
    )
    for arm_name in arm_names:
        entries = arms[arm_name]
        scored = [e for e in entries if e.score is not None]
        passes = [e for e in scored if e.score is not None and e.score >= 0.95]
        avg = sum(e.score for e in scored if e.score is not None) / len(scored) if scored else None
        pass_rate = f"{len(passes)}/{len(scored)}" if scored else "n/a"
        avg_str = f"{avg:.3f}" if avg is not None else "n/a"
        lat = _arm_latency_stats(entries)
        mean_str = f"{lat['mean_ms']:.0f}" if lat else "n/a"
        p50_str = f"{lat['p50_ms']:.0f}" if lat else "n/a"
        p95_str = f"{lat['p95_ms']:.0f}" if lat else "n/a"
        lines.append(
            f"| {arm_name} | {len(entries)} | {pass_rate} | {avg_str} | "
            f"{mean_str} | {p50_str} | {p95_str} |"
        )
    lines.append("")
    lines.append(
        "_Time-to-artifact: wall-clock from utterance to ready command string. "
        "Plan-outcome rows only; first row excluded as warmup._"
    )
    lines.append("")

    # Per-tag breakdown
    all_tags: set[str] = set()
    for entries in arms.values():
        for e in entries:
            all_tags.update(e.tags)

    if all_tags:
        lines += ["## Per-Tag Breakdown", ""]
        header = "| Tag | " + " | ".join(arm_names) + " |"
        sep = "|-----|" + "------|" * len(arm_names)
        lines += [header, sep]
        for tag in sorted(all_tags):
            row_parts = [tag]
            for arm_name in arm_names:
                tagged = [e for e in arms[arm_name] if tag in e.tags and e.score is not None]
                if not tagged:
                    row_parts.append("n/a")
                else:
                    n_pass = sum(1 for e in tagged if e.score is not None and e.score >= 0.95)
                    row_parts.append(f"{n_pass}/{len(tagged)}")
            lines.append("| " + " | ".join(row_parts) + " |")
        lines.append("")

    # Top disagreements
    lines += ["## Top Disagreements", ""]
    disagreements: list[tuple[str, int, dict[str, ArmEntry]]] = []
    for (row_id, utt_idx), arm_map in cross.items():
        scores = [e.score for e in arm_map.values() if e.score is not None]
        if len(scores) >= 2 and max(scores) >= 0.95 and min(scores) < 0.5:
            disagreements.append((row_id, utt_idx, arm_map))
    disagreements.sort(key=lambda x: x[0])

    if disagreements:
        for row_id, utt_idx, arm_map in disagreements[:50]:
            corpus_row = rows_by_id.get(row_id)
            utt = ""
            if corpus_row and utt_idx < len(corpus_row.utterances):
                utt = corpus_row.utterances[utt_idx]
            status = _review_status_str(review_log, row_id, utt_idx)
            lines.append(f"### {row_id}__{utt_idx}{status}")
            if utt:
                lines.append(f"utterance: {utt}")
            for arm_name in arm_names:
                entry = arm_map.get(arm_name)
                score_str = f"{entry.score:.3f}" if entry and entry.score is not None else "—"
                lines.append(f"- **{arm_name}**: {score_str}")
            lines.append("")
    else:
        lines += ["_No disagreements found across arms._", ""]

    # Close-miss fails
    lines += ["## Close-Miss Fails", ""]
    close_misses: list[tuple[float, str, int, str, ArmEntry]] = []
    for arm_name, entries in arms.items():
        for e in entries:
            if e.score is not None and 0.3 <= e.score < 0.95:
                close_misses.append((e.score, e.id, e.utterance_idx, arm_name, e))
    close_misses.sort(key=lambda x: -x[0])

    if close_misses:
        lines.append("| Row | Arm | Score | Failed | Review |")
        lines.append("|-----|-----|-------|--------|--------|")
        for score, row_id, utt_idx, arm_name, entry in close_misses[:50]:
            failed_str = ", ".join(entry.failed[:3]) or "—"
            status = _review_status_str(review_log, row_id, utt_idx).strip()
            lines.append(
                f"| {row_id}__{utt_idx} | {arm_name} | {score:.3f} | {failed_str} | {status} |"
            )
        lines.append("")
    else:
        lines += ["_No close-miss fails._", ""]

    # Sampled passes
    lines += ["## Sampled Passes", ""]
    rng = random.Random(42)
    for arm_name in arm_names:
        passes = [e for e in arms[arm_name] if e.score is not None and e.score >= 0.95]
        sample = rng.sample(passes, min(20, len(passes)))
        lines.append(f"### {arm_name} ({len(passes)} passes, showing {len(sample)})")
        lines.append("")
        if sample:
            lines.append("| Row | Utterance | Score |")
            lines.append("|-----|-----------|-------|")
            for e in sample:
                utt = e.utterance[:60] if e.utterance else "—"
                lines.append(f"| {e.id}__{e.utterance_idx} | {utt} | {e.score:.3f} |")
        lines.append("")

    # All entries table
    lines += ["## All Entries", ""]
    header = "| Row | Utterance | Tags | " + " | ".join(arm_names) + " | Review |"
    sep = "|-----|-----------|------|" + "------|" * len(arm_names) + "--------|"
    lines += [header, sep]
    for row_id, utt_idx in sorted(cross.keys()):
        arm_map = cross[(row_id, utt_idx)]
        first_entry = next(iter(arm_map.values()))
        utt = first_entry.utterance[:50] if first_entry.utterance else ""
        corpus_row = rows_by_id.get(row_id)
        tags_str = ", ".join(corpus_row.tags) if corpus_row else ", ".join(first_entry.tags)
        score_cells = []
        for arm_name in arm_names:
            arm_e: ArmEntry | None = arm_map.get(arm_name)
            if arm_e is None:
                score_cells.append("—")
            elif arm_e.score is None:
                score_cells.append("n/a")
            else:
                score_cells.append(f"{arm_e.score:.3f}")
        status = _review_status_str(review_log, row_id, utt_idx).strip()
        lines.append(
            f"| {row_id}__{utt_idx} | {utt} | {tags_str} | "
            + " | ".join(score_cells)
            + f" | {status} |"
        )
    lines.append("")

    return "\n".join(lines) + "\n"


# ── HTML renderer ─────────────────────────────────────────────────────────────

_REVIEW_JS = """
var initialState = JSON.parse(document.getElementById('rv-initial').textContent || '{}');
var STORAGE_KEY = 'knaif_review';

function _getState() {
  try { return Object.assign({}, initialState, JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')); }
  catch (e) { return Object.assign({}, initialState); }
}
function setState(key, status) {
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch(e) {}
  if (status) { saved[key] = status; } else { delete saved[key]; }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  _applyState(_getState());
}
function _applyState(state) {
  document.querySelectorAll('tr[data-rid]').forEach(function(tr) {
    var key = tr.dataset.rid;
    var status = state[key] || '';
    tr.className = status;
    var badge = tr.querySelector('.rv-badge');
    if (badge) badge.textContent = status || '—';
  });
}
function downloadLog() {
  var state = _getState();
  var entries = Object.entries(state).map(function(pair) {
    var parts = pair[0].split('__');
    return {id: parts[0], utterance_idx: parseInt(parts[1] || '0', 10), status: pair[1], notes: ''};
  });
  var blob = new Blob([JSON.stringify({entries: entries}, null, 2)], {type: 'application/json'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'review_log.json';
  a.click();
}
document.addEventListener('DOMContentLoaded', function() { _applyState(_getState()); });
"""

_SORT_JS = """
function sortTable(n) {
  var tbl = document.getElementById('results-table');
  var rows = Array.from(tbl.tBodies[0].rows).filter(function(r) {
    return !r.cells[0].colSpan || r.cells[0].colSpan === 1;
  });
  var asc = tbl.getAttribute('data-sort-col') != n
            || tbl.getAttribute('data-sort-dir') === 'desc';
  rows.sort(function(a, b) {
    var x = a.cells[n].textContent.trim();
    var y = b.cells[n].textContent.trim();
    var nx = parseFloat(x), ny = parseFloat(y);
    var cmp = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : x.localeCompare(y);
    return asc ? cmp : -cmp;
  });
  // Re-attach in sorted order, keeping detail rows after their data row
  var tbody = tbl.tBodies[0];
  rows.forEach(function(r) {
    tbody.appendChild(r);
    var next = r.nextSibling;
    if (next && next.cells && next.cells[0] && next.cells[0].colSpan > 1) {
      tbody.appendChild(next);
    }
  });
  tbl.setAttribute('data-sort-col', n);
  tbl.setAttribute('data-sort-dir', asc ? 'asc' : 'desc');
}
"""

_CSS = """
body{font-family:sans-serif;font-size:13px;padding:12px;max-width:1400px;}
h1{font-size:18px;}h2{font-size:15px;margin-top:24px;}
table{border-collapse:collapse;width:100%;margin-bottom:16px;}
td,th{border:1px solid #ccc;padding:3px 7px;white-space:nowrap;}
th{background:#f0f0f0;cursor:pointer;user-select:none;}
th:hover{background:#e0e0e0;}
tr:nth-child(even){background:#f9f9f9;}
.pass{color:green;font-weight:bold;}.fail{color:#c00;}.na{color:#999;}
.reviewed{background:#eaffea!important;}.rejected{background:#ffeaea!important;}
details summary{cursor:pointer;font-weight:bold;padding:4px 0;}
details>div{padding:8px;border:1px solid #ddd;margin-top:4px;background:#fafafa;}
video,audio{max-width:320px;display:block;margin:4px 0;}
img.media{max-width:320px;max-height:240px;display:block;margin:4px 0;}
.arm-grid{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;}
.arm-card{border:1px solid #ccc;padding:8px;min-width:180px;background:#fff;}
.arm-card h4{margin:0 0 6px;font-size:12px;color:#444;}
.tag{background:#e0e0ff;border-radius:3px;padding:1px 5px;margin-right:3px;font-size:11px;}
.ffprobe-diff{width:auto;margin-top:8px;font-size:11px;}
.ffprobe-diff td,.ffprobe-diff th{padding:2px 6px;}
.review-cmd{margin-top:8px;font-size:11px;color:#555;}
.rv-badge{display:inline-block;min-width:58px;color:#666;font-style:italic;}
button.rv{padding:1px 5px;cursor:pointer;font-size:11px;border:1px solid #bbb;border-radius:3px;margin:0 1px;}
"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _score_cell_html(score: float | None, outcome_correct: bool | None = None) -> str:
    if score is None:
        if outcome_correct is False:
            return '<td class="fail">n/a ✗</td>'
        return '<td class="na">n/a</td>'
    css = "pass" if score >= 0.95 else ("fail" if score < 0.5 else "")
    return f'<td class="{css}">{score:.3f}</td>'


def _path_fwd(p: str | None) -> str:
    """Convert backslash path to forward slashes and URL-encode for HTML src= attributes.

    Encodes special characters (spaces → %20, # → %23, etc.) so media paths with
    spaces or non-ASCII characters work in both file:// and http:// contexts.
    The forward-slash separator is NOT encoded so the path remains navigable.
    """
    if not p:
        return ""
    fwd = p.replace("\\", "/")
    # URL-encode each path segment individually (preserve / separators).
    return "/".join(_url_quote(segment, safe="") for segment in fwd.split("/"))


def _rel_path(path_str: str | None, base_dir: Path | None) -> str:
    """Return a forward-slash, URL-encoded path of path_str relative to base_dir.

    Used so that media src= attributes resolve correctly when the HTML is
    opened via file:// or served by http.server from the project root.
    Falls back to the original forward-slash path if relativization fails
    (e.g. different drive on Windows).
    """
    if not path_str:
        return ""
    if base_dir is None:
        return _path_fwd(path_str)
    try:
        return _path_fwd(os.path.relpath(path_str, base_dir))
    except ValueError:
        return _path_fwd(path_str)


def _triage_key(arm_map: dict) -> tuple:
    """Return a sort key that puts suspicious rows first.

    Priority (lower tuple = appears first):
      0 — execution errors or missing outputs
      1 — wrong outcome (outcome_correct=False)
      2 — low score (< 0.5) or no score on a plan row
      3 — mid score (0.5–0.94)
      4 — perfect / near-perfect (≥ 0.95)
    """
    entries = [e for e in arm_map.values() if not isinstance(e, str)]
    if not entries:
        return (4,)

    # Errors / missing outputs
    if any(getattr(e, "actual_outcome", "") in ("error", "parse_error") for e in entries):
        return (0,)

    # Wrong outcome bucket
    if any(getattr(e, "outcome_correct", True) is False for e in entries):
        return (1,)

    scores = [e.score for e in entries if e.score is not None]
    if not scores:
        # plan rows with no score → artifact missing
        if any(getattr(e, "actual_outcome", "") == "plan" for e in entries):
            return (2,)
        return (4,)  # clarify/reject with no score is expected

    min_score = min(scores)
    if min_score < 0.5:
        return (2, round(min_score, 3))
    if min_score < 0.95:
        return (3, round(min_score, 3))
    return (4, round(min_score, 3))


def _esc_js(s: str) -> str:
    """Escape a string for embedding inside a JS single-quoted string literal."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def render_report_html(
    arms: dict[str, list[ArmEntry]],
    rows_by_id: dict,
    review_log: Any = None,
    reviewer: Any = None,
    report_dir: Path | None = None,
) -> str:
    arm_names = sorted(arms.keys())
    cross = _build_cross_arm_index(arms)

    # Build initial review state for JS embedding
    initial_review: dict[str, str] = {}
    if review_log is not None:
        for entry in review_log.all_entries():
            key = f"{entry['id']}__{entry.get('utterance_idx', 0)}"
            initial_review[key] = entry.get("status", "")

    # Summary table
    summary_rows_html = []
    for arm_name in arm_names:
        entries = arms[arm_name]
        scored = [e for e in entries if e.score is not None]
        passes = [e for e in scored if e.score is not None and e.score >= 0.95]
        avg = sum(e.score for e in scored if e.score is not None) / len(scored) if scored else None
        pass_rate = f"{len(passes)}/{len(scored)}" if scored else "—"
        avg_str = f"{avg:.3f}" if avg is not None else "—"
        lat = _arm_latency_stats(entries)
        mean_str = f"{lat['mean_ms']:.0f}" if lat else "—"
        p50_str = f"{lat['p50_ms']:.0f}" if lat else "—"
        p95_str = f"{lat['p95_ms']:.0f}" if lat else "—"
        summary_rows_html.append(
            f"<tr><td>{_esc(arm_name)}</td><td>{len(entries)}</td>"
            f"<td>{pass_rate}</td><td>{avg_str}</td>"
            f"<td>{mean_str}</td><td>{p50_str}</td><td>{p95_str}</td></tr>"
        )
    summary_table = (
        "<table><thead><tr><th>Arm</th><th>Rows</th>"
        "<th>Passes / Scored</th><th>Avg score</th>"
        "<th title='Wall-clock ms from utterance to ready command. "
        "Plan rows only; first row excluded as warmup.'>Mean ms</th>"
        "<th>p50 ms</th><th>p95 ms</th>"
        "</tr></thead><tbody>" + "".join(summary_rows_html) + "</tbody></table>"
        '<p style="font-size:11px;color:#666;margin-top:-12px;">'
        "Time-to-artifact: wall-clock from utterance to ready command string. "
        "Plan-outcome rows only; first row excluded as warmup.</p>"
    )

    # Results table header — fixed cols + one per arm + review
    n_fixed = 3  # row, utterance, tags
    arm_th = "".join(
        f'<th onclick="sortTable({n_fixed + i})">{_esc(arm_name)}</th>'
        for i, arm_name in enumerate(arm_names)
    )
    thead = (
        "<thead><tr>"
        '<th onclick="sortTable(0)">Row</th>'
        "<th>Utterance</th>"
        "<th>Tags</th>" + arm_th + "<th>Review</th></tr></thead>"
    )
    n_cols = n_fixed + len(arm_names) + 1

    tbody_parts: list[str] = []
    triage_sorted = sorted(cross.keys(), key=lambda k: (_triage_key(cross[k]), k))
    for row_id, utt_idx in triage_sorted:
        arm_map = cross[(row_id, utt_idx)]
        first_entry = next(iter(arm_map.values()))
        utt = first_entry.utterance or ""
        corpus_row = rows_by_id.get(row_id)
        tags = corpus_row.tags if corpus_row else first_entry.tags
        tags_html = "".join(f'<span class="tag">{_esc(t)}</span>' for t in tags)

        review_entry = review_log.get(row_id, utt_idx) if review_log else None
        review_status = review_entry.get("status", "") if review_entry else ""
        row_class = (
            f' class="{_esc(review_status)}"' if review_status in ("reviewed", "rejected") else ""
        )

        score_cells = "".join(
            (
                _score_cell_html(arm_map[arm_name].score, arm_map[arm_name].outcome_correct)
                if arm_name in arm_map
                else '<td class="na">—</td>'
            )
            for arm_name in arm_names
        )

        rid_key = _esc_js(f"{row_id}__{utt_idx}")
        review_cell = (
            f"<td>"
            f'<span class="rv-badge">{_esc(review_status) or "—"}</span>'
            f" <button class=\"rv\" onclick=\"setState('{rid_key}','reviewed')\">✓</button>"
            f" <button class=\"rv\" onclick=\"setState('{rid_key}','rejected')\">✗</button>"
            f" <button class=\"rv\" onclick=\"setState('{rid_key}','')\">↩</button>"
            f"</td>"
        )

        data_tr = (
            f'<tr data-rid="{_esc(f"{row_id}__{utt_idx}")}" {row_class}>'
            f"<td>{_esc(f'{row_id}__{utt_idx}')}</td>"
            f"<td>{_esc(utt[:80])}</td>"
            f"<td>{tags_html}</td>"
            f"{score_cells}"
            f"{review_cell}"
            "</tr>"
        )
        tbody_parts.append(data_tr)

        # Reviewer card
        if reviewer is not None and corpus_row is not None:
            arm_outputs: dict[str, Any] = {}
            reference: Path | None = None
            src_overrides: dict[str, str] = {}
            reference_src: str | None = None
            for arm_name, entry in arm_map.items():
                arm_outputs[arm_name] = (
                    Path(_path_fwd(entry.artifact_path)) if entry.artifact_path else None
                )
                if entry.artifact_path:
                    src_overrides[arm_name] = _rel_path(entry.artifact_path, report_dir)
                if entry.baseline_path and reference is None:
                    reference = Path(_path_fwd(entry.baseline_path))
                    reference_src = _rel_path(entry.baseline_path, report_dir)
            arm_outputs["_src_overrides"] = src_overrides
            if reference_src is not None:
                arm_outputs["_reference_src"] = reference_src
            arm_outputs["_scores"] = {
                arm_name: (entry.matched, entry.failed) for arm_name, entry in arm_map.items()
            }
            arm_outputs["_review_cmd"] = (
                f"evalsuite review --log review_log.json "
                f"--row {row_id} --utterance-idx {utt_idx} --status reviewed"
            )
            row_dict = {
                "id": row_id,
                "utterances": corpus_row.utterances,
                "tags": corpus_row.tags,
            }
            try:
                card = reviewer.render_row(row_dict, arm_outputs, reference)
                details_tr = (
                    f'<tr><td colspan="{n_cols}"><details>'
                    f"<summary>{_esc(row_id)}__{utt_idx} — details</summary>"
                    f"<div>{card}</div></details></td></tr>"
                )
                tbody_parts.append(details_tr)
            except Exception:
                pass

    results_table = (
        '<table id="results-table">' + thead + "<tbody>" + "".join(tbody_parts) + "</tbody></table>"
    )

    initial_review_json = json.dumps(initial_review, ensure_ascii=False)

    return (
        "<!DOCTYPE html>"
        "<html><head><meta charset='utf-8'><title>Eval Report</title>"
        f"<style>{_CSS}</style></head>"
        "<body>"
        f"<script type='application/json' id='rv-initial'>{initial_review_json}</script>"
        "<h1>Eval Report</h1>"
        "<p><em>A passing score means &ldquo;didn&rsquo;t fail a deterministic "
        "check,&rdquo; not &ldquo;did the right thing.&rdquo;</em></p>"
        '<p><button onclick="downloadLog()" style="padding:4px 10px;cursor:pointer;">'
        "&#8659; Download review_log.json</button>"
        ' <small style="color:#888;">Click ✓/✗ on any row to mark it, then download to '
        "commit the review state.</small></p>"
        "<h2>Summary</h2>"
        + summary_table
        + "<h2>Results</h2>"
        + results_table
        + f"<script>{_REVIEW_JS}</script>"
        + f"<script>{_SORT_JS}</script>"
        "</body></html>\n"
    )
