"""Read-only corpus review for the documents skill.

The eval-suite ``honest`` verifier grades a *dry-run* result dict, so
``output_exists`` cannot pass for destructive tools (no file is written) and the
eval runner never materializes a documents artifact (``_extract_artifact`` is
ffmpeg-command-shaped). This reviewer side-steps that: it routes each corpus row
with the real model, then executes the plan *for real* against a sandbox copy of
the fixtures and grades ``success_criteria`` with the same ``honest()`` logic on
the real results. Output is a scannable per-row table for human validation — no
files in the repo are touched, nothing is written back to the corpus.

Usage in a notebook::

    from documents_corpus_review import review_corpus, render_review
    rows = review_corpus(ROOT, MODEL_CFG)        # runs every row through the model
    render_review(rows)                          # HTML pass/fail table
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default cache so a kernel restart doesn't force a full re-run.
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "artifacts" / "documents_review.json"

# documents eval helpers (fixtures + verifier) live under the skill's eval/ dir.
_SKILL_EVAL = Path(__file__).resolve().parents[2] / "eval"
if str(_SKILL_EVAL) not in sys.path:
    sys.path.insert(0, str(_SKILL_EVAL))


@dataclass
class _Out:
    """Minimal stand-in for evalsuite.AgentOutput that success()/cheap() accept."""

    outcome: str
    plan: dict[str, Any] | None
    execution_results: list[dict[str, Any]] = field(default_factory=list)
    utterance: str = ""
    # Real on-disk output(s) of the executed chain, so success()'s
    # _artifact_pdf_pages / output_exists checks grade the materialized file
    # (multi-step chains end on a tool whose result dict carries no `pages`).
    artifact_paths: list[Any] = field(default_factory=list)


def _first_tool(plan: dict[str, Any] | None) -> str | None:
    steps = (plan or {}).get("plan") or []
    return steps[0].get("tool") if steps else None


def save_review(rows: list[dict[str, Any]], path: Path | str = DEFAULT_CACHE) -> Path:
    """Persist review rows to JSON so they survive a kernel restart."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return path


def load_review(path: Path | str = DEFAULT_CACHE) -> list[dict[str, Any]] | None:
    """Load cached review rows, or None if no cache exists yet."""
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def review_corpus(
    root: Path | str,
    model_cfg: dict[str, Any],
    *,
    corpus_path: Path | str | None = None,
    limit: int | None = None,
    cache: Path | str | None = DEFAULT_CACHE,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """Route + really-execute every corpus row; return per-row review dicts.

    Each dict: id, utterance, expected_outcome, actual_outcome, expected_tool,
    actual_tool, score (0..1 or None), matched, failed.

    Results are cached to *cache* (JSON). On re-run, the cache is reused unless
    ``refresh=True`` — so a kernel restart re-renders instantly instead of
    re-running the model over every row. Pass ``cache=None`` to disable.
    """
    if cache is not None and not refresh:
        cached = load_review(cache)
        if cached is not None:
            return cached
    from fixtures import generate_documents_fixtures  # type: ignore
    from verifiers import success  # type: ignore

    from knaif import create_agent
    from knaif.evalsuite.corpus import load_corpus
    from knaif.orchestrator import InferenceOrchestrator
    from knaif.registry import retrieve_tools

    root = Path(root)
    corpus_path = Path(corpus_path) if corpus_path else root / "skills/documents/data/eval.jsonl"
    corpus = load_corpus(corpus_path)
    if limit:
        corpus = corpus[:limit]

    orc = InferenceOrchestrator(
        backend="llama_cpp",
        model_config=model_cfg,
        root=root,
        ollama_url="http://localhost:11434",
    )

    rows: list[dict[str, Any]] = []
    for row in corpus:
        # Fresh fixtures per row so destructive ops never contaminate each other.
        sandbox = Path(tempfile.mkdtemp())
        try:
            generate_documents_fixtures(sandbox)
            agent = create_agent(
                "documents",
                orchestrator=orc,
                sandbox=sandbox,
                root=sandbox,
                show_plan=False,
                require_approval=False,
            )
            utt = row.utterances[0]
            ro = retrieve_tools(utt, agent.registry)
            plan = agent.infer(utt, use_mock=False, registry_override=ro)
            actual_tool = _first_tool(plan)

            entry: dict[str, Any] = {
                "id": row.id,
                "utterance": utt,
                "expected_outcome": row.expected_outcome,
                "expected_tool": row.expected_tool,
                "actual_tool": actual_tool,
                "matched": [],
                "failed": [],
                "score": None,
            }

            if actual_tool in ("clarify", "reject"):
                entry["actual_outcome"] = actual_tool
            else:
                # Run the gate (may downgrade to clarify), then execute for real.
                results = agent.execute_plan(plan, utterance=utt, dry_run=False, confirmed=True)
                if results and results[0].get("tool") in ("clarify", "reject"):
                    entry["actual_outcome"] = results[0]["tool"]
                    entry["actual_tool"] = results[0]["tool"]
                else:
                    entry["actual_outcome"] = "plan"
                    # The chain's final output is the real artifact to grade
                    # `pages` / `output_exists` against (the last result dict of
                    # a chain ending on add_page_numbers/watermark has no pages).
                    # Single-file tools return `output`; split-style tools return
                    # an `outputs` list — accept either.
                    last = (results[-1].get("result") or {}) if results else {}
                    final_paths = (
                        [last["output"]] if last.get("output") else list(last.get("outputs") or [])
                    )
                    out = _Out("plan", plan, results, utt, artifact_paths=final_paths)
                    crit = dict(row.success_criteria or {})
                    if crit:
                        vr = success(out, crit, sandbox)
                        entry["score"] = vr.score
                        entry["matched"] = list(vr.matched)
                        entry["failed"] = list(vr.failed)
            entry["outcome_ok"] = entry["actual_outcome"] == row.expected_outcome
            rows.append(entry)
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {
                    "id": row.id,
                    "utterance": row.utterances[0],
                    "expected_outcome": row.expected_outcome,
                    "expected_tool": row.expected_tool,
                    "actual_tool": None,
                    "actual_outcome": "error",
                    "outcome_ok": False,
                    "score": 0.0,
                    "matched": [],
                    "failed": [f"exec_error: {exc}"],
                }
            )
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)
    if cache is not None:
        save_review(rows, cache)
    return rows


def _row_passes(r: dict[str, Any]) -> bool:
    tool_ok = r["actual_tool"] == r["expected_tool"] or (
        r["expected_outcome"] in ("clarify", "reject")
        and r["actual_outcome"] == r["expected_outcome"]
    )
    crit_ok = r.get("score") is None or r["score"] >= 1.0
    return bool(r["outcome_ok"] and tool_ok and crit_ok)


def render_review(rows: list[dict[str, Any]]):
    """Render the review rows as a color-coded HTML table for a notebook.

    Uses explicit text/background/border colors and a white container so it
    stays readable under dark IDE/Jupyter themes (which otherwise leave cell
    text light-on-light).
    """
    from IPython.display import HTML

    td = "padding:4px 9px;border:1px solid #c8c8c8;color:#1b1b1b;vertical-align:top"

    def cell(r: dict[str, Any]) -> str:
        ok = _row_passes(r)
        bg = "#cdebcf" if ok else "#f6cfcf"
        detail = ", ".join(r["failed"]) if r["failed"] else ", ".join(r["matched"])
        sc = "—" if r.get("score") is None else f"{r['score']:.2f}"
        mark = "✔" if ok else "✕"
        return (
            f"<tr style='background:{bg}'>"
            f"<td style='{td};text-align:center;font-weight:700'>{mark}</td>"
            f"<td style='{td};font-family:monospace'>{r['id']}</td>"
            f"<td style='{td}'>{r['utterance']}</td>"
            f"<td style='{td}'>{r['expected_tool']}</td>"
            f"<td style='{td};font-weight:700'>{r['actual_tool']}</td>"
            f"<td style='{td}'>{r['expected_outcome']}/{r['actual_outcome']}</td>"
            f"<td style='{td};text-align:right'>{sc}</td>"
            f"<td style='{td};font-size:11px;max-width:360px'>{detail}</td></tr>"
        )

    n_ok = sum(1 for r in rows if _row_passes(r))
    th = "padding:5px 9px;border:1px solid #555;color:#fff;text-align:left"
    head = (
        "<div style='background:#ffffff;color:#1b1b1b;padding:10px;"
        "font-family:sans-serif;display:inline-block'>"
        f"<p style='color:#1b1b1b;margin:0 0 8px'><b>{n_ok}/{len(rows)} rows fully pass</b> "
        "(outcome + tool + success_criteria). "
        "<span style='background:#cdebcf;padding:1px 6px'>green = pass</span> "
        "<span style='background:#f6cfcf;padding:1px 6px'>red = review</span></p>"
        "<table style='border-collapse:collapse;font-size:13px'>"
        f"<tr style='background:#444'><th style='{th}'></th><th style='{th}'>id</th>"
        f"<th style='{th}'>utterance</th><th style='{th}'>expected</th>"
        f"<th style='{th}'>actual</th><th style='{th}'>outcome</th>"
        f"<th style='{th}'>score</th><th style='{th}'>detail</th></tr>"
    )
    return HTML(head + "".join(cell(r) for r in rows) + "</table></div>")
