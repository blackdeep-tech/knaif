"""Documents skill — entry point and ``DocumentsSkill`` assembly.

Package layout: _deps (dependency shims / tool detection), _engine (pure
document engine), steps (Step handlers), intents (Intent expanders).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from knaif.skill_base import Skill

from . import _deps, _engine
from ._deps import (
    DocumentsDependencyError,
    _require_pikepdf,
    _require_pillow_image,
    _require_pypdf,
    _require_pypdfium2,
    _require_pytesseract,
    _require_tesseract,
    detect_external_tools,
)
from ._engine import (
    IMAGE_SUFFIXES,
    OFFICE_SUFFIXES,
    PDF_SUFFIX,
    TEXT_SUFFIXES,
    _extract_office_text,
    _extract_text_records,
    _format_for,
    _ghostscript_compress,
    _input_path,
    _input_paths,
    _lossless_compress,
    _make_image_overlay,
    _make_text_overlay,
    _ocr_image_text_records,
    _ocr_pdf_text_records,
    _output_dir,
    _output_path,
    _page_records_for_text,
    _parse_page_range_specs,
    _parse_pages,
    _pdf_page_count,
    _position_xy,
    _profile,
    _rasterize_compress,
    _read_text_file,
    _render_pdf_page_image,
    _replace_fixture_placeholder,
    _resolve_endpoint,
    _snippet,
    _write_image_overlay_pdf,
    _write_ocr_image_pdf,
    _write_ocr_pdf,
    _write_text_overlay_pdf,
)
from .intents import (
    CompressPdfIntent,
    OcrDocumentIntent,
)
from .steps import (
    AddPageNumbersStep,
    ConvertDocumentStep,
    ExtractTextStep,
    FindInDocumentStep,
    InspectDocumentStep,
    MergePdfsStep,
    ProtectPdfStep,
    RemovePagesStep,
    ReorderPagesStep,
    RotatePagesStep,
    RunCompressStep,
    RunOcrStep,
    SplitPdfStep,
    UnlockPdfStep,
    VerifyOutputStep,
    WatermarkStep,
)


def _read_answer(tool: Any, result: dict[str, Any]) -> str | None:
    """What a read found, in the native CLI's formats; None for a write or a dry-run stub."""
    if result.get("mode") == "dry_run":
        return None
    if tool == "inspect_document":
        line = (
            f"{result['format']}: {result['pages']} page(s), {result['size_bytes']} bytes, "
            f"encrypted={result['encrypted']}, text_layer={result['has_text_layer']}"
        )
        if result["encrypted"]:
            # 0 pages reads as an empty file unless it says why.
            line += (
                "\nThis file is password-protected, so its pages cannot be counted. "
                "Unlock it first."
            )
        return line
    if tool == "extract_text":
        return "\n".join(
            f"--- page {r['page']} ---\n{str(r['text']).rstrip()}" for r in result["pages"]
        )
    if tool == "find_in_document":
        matches = result["matches"]
        if not matches:
            return "No matches."
        return "\n".join(
            f"p{m['page']} [{m['span'][0]}..{m['span'][1]}]: {m['snippet']}" for m in matches
        )
    return None


class DocumentsSkill(Skill):
    def format_results(
        self, results: list[dict[str, Any]], *, dry_run: bool
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for item in results:
            result = item.get("result", {})
            answer = _read_answer(item.get("tool"), result)
            if "error" in result:
                items.append({"kind": "error", "message": str(result["error"])})
            elif answer is not None:
                items.append({"kind": "text", "message": answer})
            elif "output" in result and result["output"]:
                items.append({"kind": "output", "message": str(result["output"])})
            else:
                items.append({"kind": "info", "message": item.get("tool", "documents")})
            if result.get("warning"):
                items.append({"kind": "info", "message": str(result["warning"])})
        return items

    def run_artifact(self, cmd: Any, fixture: Any, out_dir: Any) -> Path | None:
        payload = json.loads(cmd) if isinstance(cmd, str) else cmd
        if not isinstance(payload, dict):
            return None

        fixture_path = Path(fixture).resolve()
        output_dir = Path(out_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        payload = _replace_fixture_placeholder(payload, fixture_path)

        # Copy each input the plan references from the fixture dir into the
        # working dir, so literal filenames ("sample.pdf", and multi-input
        # merges) resolve when the plan runs rooted at output_dir. Produced
        # files then land in output_dir rather than polluting the fixture dir.
        fixture_dir = fixture_path.parent
        for step in payload.get("plan", []):
            args = step.get("args")
            if not isinstance(args, dict):
                continue
            refs: list[str] = []
            for key in ("input", "inputs", "image"):
                val = args.get(key)
                if isinstance(val, str):
                    refs.append(val)
                elif isinstance(val, list):
                    refs.extend(v for v in val if isinstance(v, str))
            for ref in refs:
                src = fixture_dir / Path(ref).name
                dest = output_dir / Path(ref).name
                if src.exists() and not dest.exists():
                    shutil.copy2(src, dest)
            output = args.get("output")
            if isinstance(output, str) and not Path(output).is_absolute():
                args["output"] = str(output_dir / output)

        from knaif.agent import CommandAgent

        agent = CommandAgent.from_skill(
            # Bundle root — this module is in the bundle's `python/` package; skill.yaml
            # sits one level up at the bundle top.
            Path(__file__).resolve().parent.parent,
            sandbox=None,
            root=output_dir,
        )
        # The eval harness serves URL-input rows from its local server and sets
        # KNAIF_NET_TEST_HOST; this replay is its execution, so it approves the downloads.
        agent.allow_download = bool(os.environ.get("KNAIF_NET_TEST_HOST"))
        results = agent.execute_plan(payload, dry_run=False, confirmed=True)
        for item in reversed(results):
            result = item.get("result") or {}
            if isinstance(result, dict):
                output = result.get("output")
                if output and Path(output).exists():
                    return Path(output)
                outputs = result.get("outputs")
                if isinstance(outputs, list):
                    for path in reversed(outputs):
                        if Path(path).exists():
                            return Path(path)
        return None


__all__ = [
    "_deps",
    "_engine",
    "AddPageNumbersStep",
    "CompressPdfIntent",
    "ConvertDocumentStep",
    "DocumentsDependencyError",
    "ExtractTextStep",
    "FindInDocumentStep",
    "IMAGE_SUFFIXES",
    "InspectDocumentStep",
    "MergePdfsStep",
    "OFFICE_SUFFIXES",
    "OcrDocumentIntent",
    "PDF_SUFFIX",
    "ProtectPdfStep",
    "RemovePagesStep",
    "ReorderPagesStep",
    "RotatePagesStep",
    "RunCompressStep",
    "RunOcrStep",
    "SplitPdfStep",
    "TEXT_SUFFIXES",
    "UnlockPdfStep",
    "VerifyOutputStep",
    "WatermarkStep",
    "_extract_office_text",
    "_extract_text_records",
    "_format_for",
    "_ghostscript_compress",
    "_input_path",
    "_input_paths",
    "_lossless_compress",
    "_make_image_overlay",
    "_make_text_overlay",
    "_ocr_image_text_records",
    "_ocr_pdf_text_records",
    "_output_dir",
    "_output_path",
    "_page_records_for_text",
    "_parse_page_range_specs",
    "_parse_pages",
    "_pdf_page_count",
    "_position_xy",
    "_profile",
    "_rasterize_compress",
    "_read_text_file",
    "_render_pdf_page_image",
    "_replace_fixture_placeholder",
    "_require_pikepdf",
    "_require_pillow_image",
    "_require_pypdf",
    "_require_pypdfium2",
    "_require_pytesseract",
    "_require_tesseract",
    "_resolve_endpoint",
    "_snippet",
    "_write_image_overlay_pdf",
    "_write_ocr_image_pdf",
    "_write_ocr_pdf",
    "_write_text_overlay_pdf",
    "detect_external_tools",
    "DocumentsSkill",
]
