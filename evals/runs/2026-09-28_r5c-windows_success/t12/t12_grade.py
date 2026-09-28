"""T12 verdict, on the host: the Sandbox's checks (t12_results.json) plus the OCR text check.

Written with the rules (t12_stage.sh), before the run. `ocr_text` passes when a PDF the OCR row
produced has a text layer containing "Scanned image text", documents_077's success criterion.
Prints every check and `T12 PASS` / `T12 FAIL`; exits 0 only on PASS.

    uv run python evals/runs/2026-09-28_r5c-windows_success/t12/t12_grade.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pypdf import PdfReader

OUT = Path(__file__).resolve().parents[4] / "sandbox" / "r5c" / "t12" / "out"
EXPECTED = "Scanned image text"
REQUIRED = (
    "cleanroom_version",
    "cleanroom_skills_list",
    "install_110",
    "installed_110_runs",
    "cli_running_holds_mutex",
    "upgrade_refused_while_running",
    "upgrade_installs",
    "upgrade_same_folder",
    "upgrade_no_folder_exists_prompt",
    "upgraded_binary_runs",
    "upgrade_no_leftover_libraries",
    "ocr_row_runs",
    "ocr_no_pdfium_error",
)


def ocr_text() -> tuple[bool, str]:
    pdfs = sorted((OUT / "ocr").glob("*.pdf"))
    for pdf in pdfs:
        # As skills/documents/eval/verifiers.py grades it: pypdf text, per page, case-sensitive.
        if any(EXPECTED in (page.extract_text() or "") for page in PdfReader(pdf).pages):
            return True, f"{pdf.name} contains {EXPECTED!r}"
    return False, f"no produced PDF contains {EXPECTED!r} (checked {[p.name for p in pdfs]})"


def main() -> int:
    path = OUT / "t12_results.json"
    if not path.is_file():
        print(f"no results: {path.name} missing (did the Sandbox script finish?)")
        return 1
    results = json.loads(path.read_text(encoding="utf-8-sig"))
    ok, detail = ocr_text()
    results["ocr_text"] = {"pass": ok, "detail": detail}
    for key in (*REQUIRED, "ocr_text"):
        r = results.get(key)
        mark = "MISSING" if r is None else ("PASS" if r["pass"] else "FAIL")
        print(f"{mark:7} {key}: {'' if r is None else r['detail']}")
    passed = all(results.get(k, {}).get("pass") for k in (*REQUIRED, "ocr_text"))
    print("T12 PASS" if passed else "T12 FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
