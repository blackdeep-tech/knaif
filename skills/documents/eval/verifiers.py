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


#: Criteria graded on the produced FILE rather than on the step's result dict (release plan R3a).
#: 87 of 132 plan rows were graded only on the tool and that a file existed, so `documents_036`
#: ("rotate sample.pdf 90 degrees") scored 1.0 with page 1 of 3 rotated.
ARTIFACT_KEYS = (
    "rotation",
    "rotation_one_of",
    "page_texts",
    "every_page_contains",
    "page_numbers_from",
    "artifact_encrypted",
    "decrypts_with",
    "artifact_text_contains",
    "artifact_format",
    "no_larger_than",
)


def _page_texts(reader: Any) -> list[str]:
    return [(page.extract_text() or "") for page in reader.pages]


def _artifact_checks(
    final: Path | None,
    criteria: dict[str, Any],
    sandbox: Path,
    matched: list[str],
    failed: list[str],
) -> None:
    """Grade the transformation on the real artifact. Every requested check fails when there is
    no artifact to open — a missing file cannot have rotated its pages."""
    asked = [k for k in ARTIFACT_KEYS if k in criteria]
    if not asked:
        return
    if final is None or not final.exists():
        failed.extend(f"{k}: no artifact" for k in asked)
        return

    if "no_larger_than" in criteria:
        # Beside the artifact first: the eval runs each row in its own directory, where the
        # row's fixture was copied, so `sandbox` is not necessarily where the input lives.
        name = criteria["no_larger_than"]
        source = next(
            (p for p in (final.parent / name, Path(sandbox) / name) if p.exists()),
            final.parent / name,
        )
        if source.exists() and final.stat().st_size <= source.stat().st_size:
            matched.append(f"no_larger_than:{source.name}")
        else:
            size = source.stat().st_size if source.exists() else None
            failed.append(f"no_larger_than: {final.stat().st_size} bytes vs {size}")

    fmt = criteria.get("artifact_format")
    if fmt:
        head = final.read_bytes()[:5]
        is_pdf = head.startswith(b"%PDF")
        ok = is_pdf if fmt == "pdf" else (not is_pdf and final.suffix.lower() == f".{fmt}")
        (matched if ok else failed).append(
            f"artifact_format={fmt}"
            if ok
            else f"artifact_format: expected {fmt}, got {final.suffix or head!r}"
        )

    pdf_keys = [k for k in asked if k not in ("no_larger_than", "artifact_format")]
    if final.suffix.lower() != ".pdf":
        # A text artifact: only a content check applies.
        wanted = criteria.get("artifact_text_contains")
        if wanted:
            text = final.read_text(encoding="utf-8", errors="replace")
            (matched if wanted in text else failed).append(
                f"artifact_text_contains:{wanted}"
                if wanted in text
                else f"artifact_text_missing:{wanted}"
            )
        failed.extend(
            f"{k}: artifact is not a PDF" for k in pdf_keys if k != "artifact_text_contains"
        )
        return
    if not pdf_keys:
        return

    from pypdf import PdfReader

    try:
        reader = PdfReader(str(final))
    except Exception as exc:  # noqa: BLE001
        failed.extend(f"{k}: unreadable PDF ({exc})" for k in pdf_keys)
        return

    if "artifact_encrypted" in criteria:
        want = bool(criteria["artifact_encrypted"])
        (matched if reader.is_encrypted == want else failed).append(
            f"artifact_encrypted={want}"
            if reader.is_encrypted == want
            else f"artifact_encrypted: expected {want}, got {reader.is_encrypted}"
        )
    if reader.is_encrypted:
        password = criteria.get("decrypts_with")
        opened = bool(password) and bool(reader.decrypt(password))
        if "decrypts_with" in criteria:
            (matched if opened else failed).append(
                f"decrypts_with:{password}"
                if opened
                else f"decrypts_with: {password!r} does not open it"
            )
        if not opened:
            content = [k for k in pdf_keys if k not in ("artifact_encrypted", "decrypts_with")]
            failed.extend(f"{k}: cannot read an encrypted artifact" for k in content)
            return
    elif "decrypts_with" in criteria:
        failed.append("decrypts_with: the artifact is not encrypted")

    rotations = [int(page.rotation or 0) % 360 for page in reader.pages]
    if "rotation" in criteria:
        want = criteria["rotation"]
        expected = (
            [int(want) % 360] * len(rotations)
            if isinstance(want, int)
            else [int(r) % 360 for r in want]
        )
        ok = rotations == expected
        (matched if ok else failed).append(
            f"rotation={want}" if ok else f"rotation: expected {expected}, got {rotations}"
        )
    if "rotation_one_of" in criteria:
        allowed = {int(r) % 360 for r in criteria["rotation_one_of"]}
        ok = len(set(rotations)) == 1 and rotations[0] in allowed
        (matched if ok else failed).append(
            f"rotation_one_of={sorted(allowed)}"
            if ok
            else f"rotation_one_of: expected one of {sorted(allowed)} on every page, got {rotations}"
        )

    texts = (
        _page_texts(reader)
        if any(
            k in criteria
            for k in (
                "page_texts",
                "every_page_contains",
                "page_numbers_from",
                "artifact_text_contains",
            )
        )
        else []
    )
    if "page_texts" in criteria:
        want = list(criteria["page_texts"])
        ok = len(texts) == len(want) and all(
            marker in text for marker, text in zip(want, texts, strict=True)
        )
        got = [t.strip().splitlines()[0] if t.strip() else "" for t in texts]
        (matched if ok else failed).append(
            f"page_texts={want}" if ok else f"page_texts: expected {want}, got {got}"
        )
    if "every_page_contains" in criteria:
        mark = criteria["every_page_contains"]
        missing = [i + 1 for i, text in enumerate(texts) if mark not in text]
        ok = bool(texts) and not missing
        (matched if ok else failed).append(
            f"every_page_contains:{mark}"
            if ok
            else f"every_page_contains: {mark!r} missing on page(s) {missing}"
        )
    if "page_numbers_from" in criteria:
        start = int(criteria["page_numbers_from"])
        wrong = [i + 1 for i, text in enumerate(texts) if str(start + i) not in text.split()]
        ok = bool(texts) and not wrong
        (matched if ok else failed).append(
            f"page_numbers_from={start}"
            if ok
            else f"page_numbers_from: page(s) {wrong} lack their number counting from {start}"
        )
    wanted = criteria.get("artifact_text_contains")
    if wanted:
        ok = any(wanted in text for text in texts)
        (matched if ok else failed).append(
            f"artifact_text_contains:{wanted}" if ok else f"artifact_text_missing:{wanted}"
        )


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

    # "the first 2 pages" must not return page 3: containing the right text is not enough.
    text_excludes = criteria.get("text_excludes")
    if text_excludes:
        text = str(result.get("text", ""))
        if text_excludes in text:
            failed.append(f"text_should_exclude:{text_excludes}")
        else:
            matched.append(f"text_excludes:{text_excludes}")

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
        if k != "output_exists"
        and k not in ARTIFACT_KEYS
        and not (k == "pages" and artifact_pages is not None)
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

    # What the transformation did, read off the final artifact (release plan R3a).
    _artifact_checks(_final_artifact(output), criteria, sandbox, matched, failed)

    return _score(matched, failed, kind="output")


VERIFIERS = {
    "cheap": cheap,
    "honest": honest,
    "success": success,
}

VERIFIER_PREFLIGHT: dict[str, Any] = {}
