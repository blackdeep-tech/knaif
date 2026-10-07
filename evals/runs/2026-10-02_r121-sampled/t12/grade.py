"""1.2.1 release clean room verdict, on the host: the Sandbox's checks plus two file checks.

`ocr_text`: a PDF the OCR row produced contains "Scanned image text" (documents_077).
`b5_password`: the PDF locked in the Sandbox opens with `p\\ss` and not with `p/ss`.
The Smart App Control checks are required whenever the Sandbox reported enforcement.

    uv run python sandbox/r121/final/grade.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pypdf import PdfReader

OUT = Path(__file__).resolve().parent / "out"
REQUIRED = (
    "cleanroom_binary_is_new",
    "cleanroom_version_is_121",
    "cleanroom_skills_list",
    "zip_every_binary_signed",
    "install_120",
    "installed_120_is_published",
    "cli_running_holds_mutex",
    "upgrade_refused_while_running",
    "upgrade_installs",
    "upgrade_publisher",
    "upgrade_same_folder",
    "upgrade_no_folder_exists_prompt",
    "upgraded_binary_is_new",
    "upgraded_version_is_121",
    "upgrade_no_leftover_libraries",
    "installed_every_binary_signed",
    "ocr_row_runs",
    "ocr_no_pdfium_error",
    "b5_protect_runs",
)
SAC = ("sac_blocks_unsigned_120", "sac_blocks_installed_120", "sac_blocks_nothing_of_knaif")


def ocr_text() -> tuple[bool, str]:
    pdfs = sorted((OUT / "ocr").glob("*.pdf"))
    for pdf in pdfs:
        if any("Scanned image text" in (p.extract_text() or "") for p in PdfReader(pdf).pages):
            return True, f"{pdf.name} contains the expected text"
    return False, f"no produced PDF contains the expected text ({[p.name for p in pdfs]})"


def b5_password() -> tuple[bool, str]:
    pdfs = sorted((OUT / "b5").glob("*.pdf"))
    if len(pdfs) != 1:
        return False, f"expected one locked PDF, found {[p.name for p in pdfs]}"
    typed = PdfReader(pdfs[0]).decrypt("p\\ss")
    slashed = PdfReader(pdfs[0]).decrypt("p/ss")
    return bool(typed) and not slashed, f"opens with p\\ss: {bool(typed)}; with p/ss: {bool(slashed)}"


def main() -> int:
    path = OUT / "t12_results.json"
    if not path.is_file():
        print(f"no results: {path.name} missing (did the Sandbox script finish?)")
        return 1
    results = json.loads(path.read_text(encoding="utf-8-sig"))
    for key, check in (("ocr_text", ocr_text), ("b5_password", b5_password)):
        ok, detail = check()
        results[key] = {"pass": ok, "detail": detail}
    log = (OUT / "t12.log").read_text(encoding="utf-8-sig", errors="replace")
    enforcing = "Smart App Control at start: 1 " in log
    print(f"Smart App Control enforcing at start: {enforcing}")
    keys = (*REQUIRED, *(SAC if enforcing else ()), "ocr_text", "b5_password")
    for key in keys:
        r = results.get(key)
        mark = "MISSING" if r is None else ("PASS" if r["pass"] else "FAIL")
        print(f"{mark:7} {key}: {'' if r is None else r['detail']}")
    passed = all(results.get(k, {}).get("pass") for k in keys)
    print(f"{'PASS' if passed else 'FAIL'} ({sum(bool(results.get(k, {}).get('pass')) for k in keys)}/{len(keys)})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
