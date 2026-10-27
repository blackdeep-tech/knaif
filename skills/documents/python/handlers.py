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

from knaif.batch_chain import PlannedSet
from knaif.known_folders import named_folder
from knaif.skill_base import Skill
from knaif.ui import fmt_bytes

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
    _free_name,
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
    _piece_label,
    split_specs,
)

#: How many lines of a saved text the terminal shows.
PREVIEW_LINES = 5


def text_preview(text: str) -> list[str]:
    """The preview of a saved text: its first five non-empty lines, without trailing spaces, then
    "… N more lines" when there are more; "(no text)" when there is none.
    Rust mirror: ``knaif_skill_documents::text::text_preview``;
    contracts/parity/text_preview_cases.json."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ["(no text)"]
    shown, rest = lines[:PREVIEW_LINES], len(lines) - PREVIEW_LINES
    if rest > 0:
        shown.append(f"… {rest} more line{'s' if rest > 1 else ''}")
    return shown


def _read_answer(tool: Any, result: dict[str, Any]) -> str | None:
    """What a read found, in the native CLI's formats; None for a write or a dry-run stub."""
    if result.get("mode") == "dry_run":
        return None
    if tool == "inspect_document":
        line = (
            f"{result['format']}: {result['pages']} page(s), {result['size_bytes']} bytes, "
            f"encrypted={str(result['encrypted']).lower()}, "
            f"text_layer={str(result['has_text_layer']).lower()}"
        )
        if result["encrypted"]:
            # 0 pages reads as an empty file unless it says why.
            line += (
                "\nThis file is password-protected, so its pages cannot be counted. "
                "Unlock it first."
            )
        return line
    if tool == "extract_text":
        # The text is saved; the terminal shows a preview of it, never all of it.
        if "text" not in result:
            return None
        return "\n".join(text_preview(str(result["text"])))
    if tool == "find_in_document":
        matches = result["matches"]
        if not matches:
            return "No matches."
        return "\n".join(
            f"p{m['page']} [{m['span'][0]}..{m['span'][1]}]: {m['snippet']}" for m in matches
        )
    return None


#: How each write is named before it runs, in native's words (`run::write_outputs`): the CLIs
#: print `would <summary> →` and the files (one display for both CLIs).
_WRITE_SUMMARIES = {
    "rotate_pages": "rotate pages",
    "remove_pages": "remove pages",
    "reorder_pages": "reorder pages",
    "protect_pdf": "password-protect",
    "unlock_pdf": "unlock",
    "watermark": "watermark",
    "add_page_numbers": "add page numbers",
    "ocr_document": "OCR → searchable PDF",
    "extract_text": "save the text",
}


def write_summary(tool: Any, args: dict[str, Any], result: dict[str, Any]) -> str:
    """What a write does, as native's documents runtime names it."""
    if tool == "merge_pdfs":
        return f"merge {len(result.get('inputs') or args.get('inputs') or [])} file(s)"
    if tool in ("split_pdf", "split_pdf_every"):
        outputs = result.get("outputs") or []
        return f"split into {len(outputs)} file(s)" if outputs else "split"
    if tool == "convert_document":
        return f"convert to {args.get('to_format')}"
    if tool == "compress_pdf":
        method = {"rasterize-pillow": "rasterize"}.get(result.get("method"), result.get("method"))
        if result.get("text_preserved") is False:
            method = f"{method} — removes selectable text"
        return f"compress ({method})"
    return _WRITE_SUMMARIES.get(str(tool), str(tool))


def _written(result: dict[str, Any]) -> list[str]:
    """The files a write result names: its `outputs`, else its `output`."""
    outputs = result.get("outputs")
    if isinstance(outputs, list) and outputs:
        return [str(o.get("output") if isinstance(o, dict) else o) for o in outputs]
    return [str(result["output"])] if result.get("output") else []


def _read_detail(tool: Any, result: dict[str, Any]) -> list[str] | None:
    """A read's answer as native's terminal view words it (``print_read_result``): one line
    per fact, under the step."""
    if tool == "inspect_document":
        kind = str(result["format"]).upper()
        size = fmt_bytes(int(result["size_bytes"]))
        if result["encrypted"]:
            return [
                f"{kind} · {size} · password-protected, so its pages cannot be counted. "
                "Unlock it first."
            ]
        pages = "1 page" if result["pages"] == 1 else f"{result['pages']} pages"
        text = "has a text layer" if result["has_text_layer"] else "no text layer"
        return [f"{kind} · {pages} · {size} · {text}"]
    if tool == "find_in_document":
        matches = result["matches"]
        return [f"page {m['page']}: {m['snippet']}" for m in matches] or ["No matches."]
    return None


class DocumentsSkill(Skill):
    def planned_outputs(self, tool: str, args: dict[str, Any], ctx: Any) -> Any:
        """The page files a split will write, when it writes more than one, before it runs.

        Their names are the step's own (`<stem>-pages-<range>.pdf` next to the input, a free name
        when one is taken, or the `%s` set name core gave it), relative to where paths resolve.
        An input that cannot be read is no set: the step itself reports it.
        """
        if tool not in ("split_pdf", "split_pdf_every"):
            return None
        try:
            input_path = _input_path(args, ctx)
            specs = split_specs(args, _pdf_page_count(input_path))
        except Exception:  # noqa: BLE001 - missing, locked or no pages: the step reports it
            return None
        if len(specs) < 2:
            return None
        base = Path(ctx.sandbox if ctx.sandbox is not None else ctx.root).resolve()
        source = str(args.get("input") or "").replace("\\", "/")
        folder = source.rsplit("/", 1)[0] + "/" if "/" in source else ""
        output = args.get("output")
        if isinstance(output, str) and "%s" in output:
            name = output
            files = [output % _piece_label(label) for label, _ in specs]
        else:
            name = f"{folder}{input_path.stem}-pages-%s.pdf"
            files = []
            for label, _ in specs:
                free = _free_name(
                    input_path.parent / (name.rsplit("/", 1)[-1] % _piece_label(label)), ctx
                )
                try:
                    files.append(free.resolve().relative_to(base).as_posix())
                except ValueError:
                    files.append(str(free))
        return PlannedSet(name, files)

    def format_results(
        self, results: list[dict[str, Any]], *, dry_run: bool
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for item in results:
            result = item.get("result", {})
            answer = _read_answer(item.get("tool"), result)
            if "error" in result:
                items.append({"kind": "error", "message": str(result["error"])})
            elif dry_run and answer is None and _written(result):
                # `would <summary> →` and the files, as native previews a write.
                items.append(
                    {
                        "kind": "preview",
                        "message": write_summary(item.get("tool"), item.get("args") or {}, result),
                        "outputs": _written(result),
                    }
                )
            elif not dry_run and _written(result):
                # What it does, then each file it wrote, as native shows a write;
                # extract_text's preview follows its file.
                items.append(
                    {
                        "kind": "wrote",
                        "message": write_summary(item.get("tool"), item.get("args") or {}, result),
                        "outputs": _written(result),
                        "seconds": float(item.get("duration_ms") or 0) / 1000,
                    }
                )
                if answer is not None:
                    items.append({"kind": "text", "message": answer})
            elif answer is not None:
                # The tree words a read its own way (native `print_read_result`).
                items.append(
                    {
                        "kind": "text",
                        "message": answer,
                        "detail": _read_detail(item.get("tool"), result),
                    }
                )
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
            # "Documents/merged.pdf" names the user's Documents folder: left to the pipeline's
            # known-folder rewrite, which the eval points beside the row (`<row>.known`).
            if (
                isinstance(output, str)
                and not Path(output).is_absolute()
                and named_folder(output) is None
            ):
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
