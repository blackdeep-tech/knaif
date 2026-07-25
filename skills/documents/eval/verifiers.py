"""Documents eval verifiers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knaif.evalsuite.protocols import VerifyResult


def _first_tool(output: Any) -> str | None:
    steps = (getattr(output, "plan", None) or {}).get("plan") or []
    return steps[0].get("tool") if steps else None


def _result_dicts(output: Any) -> list[dict[str, Any]]:
    results = []
    for item in getattr(output, "execution_results", []) or []:
        result = item.get("result") if isinstance(item, dict) else None
        if isinstance(result, dict):
            results.append(result)
    return results


def _last_result(output: Any) -> dict[str, Any]:
    results = _result_dicts(output)
    return results[-1] if results else {}


def _page_count(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return len(value)
    return None


def _final_artifact(output: Any) -> Path | None:
    paths = [Path(p) for p in (getattr(output, "artifact_paths", None) or [])]
    if not paths and getattr(output, "artifact_path", None):
        paths = [Path(output.artifact_path)]
    return paths[-1] if paths else None


def _artifact_pdf_pages(output: Any) -> int | None:
    """Page count of the materialized final PDF artifact, or None.

    Multi-step chains end on a tool (add_page_numbers/watermark) whose dry-run
    result dict carries no ``pages`` field, so dict-based grading reads None. The
    real chain output is on disk — count it there instead.
    """
    final = _final_artifact(output)
    if final is None or final.suffix.lower() != ".pdf" or not final.exists():
        return None
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(final)).pages)
    except Exception:  # noqa: BLE001
        return None


def _score(matched: list[str], failed: list[str], *, kind: str) -> VerifyResult:
    total = len(matched) + len(failed)
    score = len(matched) / total if total else 1.0
    return VerifyResult(score=score, matched=matched, failed=failed, verifier_kind=kind)


def cheap(output: Any, criteria: dict[str, Any], sandbox: Path) -> VerifyResult:
    """Plan-only verifier for fast routing checks."""

    expected_tool = criteria.get("expected_tool")
    actual_tool = _first_tool(output)
    if not expected_tool:
        return _score(["no_expected_tool"], [], kind="plan")
    if actual_tool == expected_tool:
        return _score([f"tool:{actual_tool}"], [], kind="plan")
    return _score([], [f"tool: expected {expected_tool}, got {actual_tool}"], kind="plan")


def honest(output: Any, criteria: dict[str, Any], sandbox: Path) -> VerifyResult:
    """Grade executed document results against cheap deterministic criteria."""

    if getattr(output, "outcome", "plan") != "plan":
        return VerifyResult(score=0.0, failed=["not_a_plan"], verifier_kind="output")

    matched: list[str] = []
    failed: list[str] = []
    expected_tool = criteria.get("expected_tool")
    if expected_tool:
        actual_tool = _first_tool(output)
        if actual_tool == expected_tool:
            matched.append(f"tool:{actual_tool}")
        else:
            failed.append(f"tool: expected {expected_tool}, got {actual_tool}")

    result = _last_result(output)

    if "format" in criteria:
        actual = result.get("format")
        expected = criteria["format"]
        if actual == expected:
            matched.append(f"format={expected}")
        else:
            failed.append(f"format: expected {expected}, got {actual}")

    if "pages" in criteria:
        actual_pages = _page_count(result.get("pages"))
        expected_pages = criteria["pages"]
        if actual_pages == expected_pages:
            matched.append(f"pages={expected_pages}")
        else:
            failed.append(f"pages: expected {expected_pages}, got {actual_pages}")

    if "encrypted" in criteria:
        actual = result.get("encrypted")
        expected = criteria["encrypted"]
        if actual is expected:
            matched.append(f"encrypted={expected}")
        else:
            failed.append(f"encrypted: expected {expected}, got {actual}")

    if "has_text_layer" in criteria:
        actual = result.get("has_text_layer")
        expected = criteria["has_text_layer"]
        if actual is expected:
            matched.append(f"has_text_layer={expected}")
        else:
            failed.append(f"has_text_layer: expected {expected}, got {actual}")

    if "matches_count" in criteria:
        actual = result.get("count")
        expected = criteria["matches_count"]
        if actual == expected:
            matched.append(f"matches_count={expected}")
        else:
            failed.append(f"matches_count: expected {expected}, got {actual}")

    if "method" in criteria:
        actual = result.get("method")
        expected = criteria["method"]
        if actual == expected:
            matched.append(f"method={expected}")
        else:
            failed.append(f"method: expected {expected}, got {actual}")

    if "text_preserved" in criteria:
        actual = result.get("text_preserved")
        expected = criteria["text_preserved"]
        if actual is expected:
            matched.append(f"text_preserved={expected}")
        else:
            failed.append(f"text_preserved: expected {expected}, got {actual}")

    text_contains = criteria.get("text_contains")
    if text_contains:
        text = str(result.get("text", ""))
        if text_contains in text:
            matched.append(f"text_contains:{text_contains}")
        else:
            failed.append(f"text_missing:{text_contains}")

    if criteria.get("output_exists"):
        output_path = result.get("output")
        output_paths = result.get("outputs") if isinstance(result.get("outputs"), list) else []
        paths = [Path(output_path)] if output_path else [Path(path) for path in output_paths]
        existing = [path for path in paths if path.exists()]
        if paths and len(existing) == len(paths):
            matched.append(f"output_exists:{existing[0]}")
        else:
            missing = next((path for path in paths if not path.exists()), None)
            failed.append(f"output_missing:{missing}")

    return _score(matched, failed, kind="output")


def success(output: Any, criteria: dict[str, Any], sandbox: Path) -> VerifyResult:
    """Real-execution verifier: grade the materialized artifact + result fields.

    Unlike ``honest`` (which inspects the dry-run result dict and therefore can't
    confirm ``output_exists`` for destructive tools), this grades the real file
    produced by the runner's ``execute=True`` / ``run_artifact`` path via
    ``output.artifact_path``. Read-only field checks (format/pages/text/etc.)
    reuse ``honest`` on the same result dict.
    """
    if getattr(output, "outcome", "plan") != "plan":
        return VerifyResult(score=0.0, failed=["not_a_plan"], verifier_kind="output")

    # Grade ``pages`` against the real materialized PDF when one exists (the
    # truth for multi-step chains); otherwise leave it to the dict-based check.
    artifact_pages = _artifact_pdf_pages(output) if "pages" in criteria else None
    delegated = {
        k: v
        for k, v in criteria.items()
        if k != "output_exists" and not (k == "pages" and artifact_pages is not None)
    }
    base = honest(output, delegated, sandbox)
    matched = list(base.matched)
    failed = list(base.failed)

    if "pages" in criteria and artifact_pages is not None:
        if artifact_pages == criteria["pages"]:
            matched.append(f"pages={criteria['pages']}")
        else:
            failed.append(f"pages: expected {criteria['pages']}, got {artifact_pages}")

    if criteria.get("output_exists"):
        paths = [Path(p) for p in (getattr(output, "artifact_paths", None) or [])]
        if not paths and getattr(output, "artifact_path", None):
            paths = [Path(output.artifact_path)]
        existing = [p for p in paths if p.exists()]
        if paths and len(existing) == len(paths):
            matched.append(f"artifact_exists:{existing[0].name}")
        else:
            failed.append("artifact_missing")

    return _score(matched, failed, kind="output")


VERIFIERS = {
    "cheap": cheap,
    "honest": honest,
    "success": success,
}

VERIFIER_PREFLIGHT: dict[str, Any] = {}
