"""documents skill — Step handlers (see handlers.py)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from knaif.handler_api import HandlerContext
from knaif.tool import Handler

from . import _deps
from ._deps import (
    DocumentsDependencyError,
    _require_pikepdf,
    _require_pillow_image,
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
    _output_dir,
    _output_path,
    _parse_page_range_specs,
    _parse_pages,
    _pdf_page_count,
    _profile,
    _rasterize_compress,
    _resolve,
    _snippet,
    _write_image_overlay_pdf,
    _write_ocr_image_pdf,
    _write_ocr_pdf,
    _write_text_overlay_pdf,
    _writing,
)


class InspectDocumentStep(Handler):
    name = "inspect_document"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        path = _input_path(args, ctx)
        # In a dry-run chain preview an upstream step's output does not exist yet;
        # return a stub instead of reading a missing file.
        if ctx.dry_run and not path.exists():
            return {
                "mode": "dry_run",
                "format": _format_for(path),
                "size_bytes": 0,
                "encrypted": False,
                "has_text_layer": False,
                "pages": 1,
            }
        suffix = path.suffix.lower()
        result: dict[str, Any] = {
            "format": _format_for(path),
            "size_bytes": path.stat().st_size,
            "encrypted": False,
            "has_text_layer": suffix in TEXT_SUFFIXES or suffix in OFFICE_SUFFIXES,
            "pages": 1,
        }
        if suffix == PDF_SUFFIX:
            try:
                from pypdf import PdfReader
            except ImportError as exc:  # pragma: no cover - covered when extra absent
                raise DocumentsDependencyError(
                    "Install the documents dep group (uv pip install --group documents) to inspect PDFs."
                ) from exc
            reader = PdfReader(str(path))
            result["encrypted"] = bool(reader.is_encrypted)
            result["pages"] = len(reader.pages) if not reader.is_encrypted else 0
            result["has_text_layer"] = (
                False
                if reader.is_encrypted
                else any((page.extract_text() or "").strip() for page in reader.pages)
            )
        elif suffix == ".pptx":
            result["pages"] = len(_extract_office_text(path))
        elif suffix == ".xlsx":
            result["pages"] = len(_extract_office_text(path))
        elif (
            suffix not in TEXT_SUFFIXES
            and suffix not in OFFICE_SUFFIXES
            and suffix not in IMAGE_SUFFIXES
        ):
            result["has_text_layer"] = False
        return result


class ExtractTextStep(Handler):
    name = "extract_text"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        path = _input_path(args, ctx)
        if ctx.dry_run and not path.exists():
            return {"mode": "dry_run", "pages": [], "text": ""}
        records = _extract_text_records(path, args)
        return {"pages": records, "text": "\n".join(record["text"] for record in records)}


class FindInDocumentStep(Handler):
    name = "find_in_document"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        path = _input_path(args, ctx)
        if ctx.dry_run and not path.exists():
            return {"mode": "dry_run", "matches": [], "count": 0}
        records = _extract_text_records(path, args)
        query = args["query"]
        flags = re.IGNORECASE if args.get("ignore_case", True) else 0
        matches: list[dict[str, Any]] = []
        for record in records:
            text = record["text"]
            iterator = (
                re.finditer(query, text, flags)
                if args.get("regex", False)
                else re.finditer(re.escape(query), text, flags)
            )
            for match in iterator:
                matches.append(
                    {
                        "page": record["page"],
                        "snippet": _snippet(text, match.start(), match.end()),
                        "span": [match.start(), match.end()],
                    }
                )
        return {"matches": matches, "count": len(matches)}


class MergePdfsStep(Handler):
    name = "merge_pdfs"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        pikepdf = _require_pikepdf()
        inputs = _input_paths(args, ctx)
        output = _resolve(args["output"], ctx)
        # A dry run counts only the inputs already there: a URL input is downloaded, and a
        # chain intermediate made, only in a real run.
        pages = sum(_pdf_page_count(path) for path in inputs if not ctx.dry_run or path.exists())

        if ctx.dry_run:
            return {
                "mode": "dry_run",
                "output": str(output),
                "pages": pages,
                "inputs": [str(path) for path in inputs],
            }

        merged = pikepdf.Pdf.new()
        for path in inputs:
            with pikepdf.Pdf.open(path) as source:
                merged.pages.extend(source.pages)
        with _writing(output, ctx) as target:
            merged.save(target)
        merged.close()
        return {"output": str(output), "pages": pages}


class SplitPdfStep(Handler):
    name = "split_pdf"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        pikepdf = _require_pikepdf()
        input_path = _input_path(args, ctx)
        total_pages = _pdf_page_count(input_path)
        specs = _parse_page_range_specs(args["ranges"], total_pages)

        # `output` names a single destination file; only meaningful when the
        # split produces exactly one file. For multi-range splits it cannot
        # apply, so fall back to the directory-per-file behaviour.
        if args.get("output") and len(specs) == 1:
            outputs = [_resolve(args["output"], ctx)]
        else:
            out_dir = _output_dir(args, ctx, input_path.parent)
            outputs = [
                _free_name(out_dir / f"{input_path.stem}-pages-{label.replace('-', '_')}.pdf", ctx)
                for label, _pages in specs
            ]
        if ctx.dry_run:
            return {
                "mode": "dry_run",
                "outputs": [str(path) for path in outputs],
                "count": len(outputs),
            }

        with pikepdf.Pdf.open(input_path) as source:
            for output, (_label, pages) in zip(outputs, specs, strict=True):
                split = pikepdf.Pdf.new()
                for page_number in pages:
                    split.pages.append(source.pages[page_number - 1])
                with _writing(output, ctx) as target:
                    split.save(target)
                split.close()
        return {"outputs": [str(path) for path in outputs], "count": len(outputs)}


class RotatePagesStep(Handler):
    name = "rotate_pages"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        pikepdf = _require_pikepdf()
        input_path = _input_path(args, ctx)
        output = _output_path(input_path, args, ctx, "-rotated.pdf")
        total_pages = _pdf_page_count(input_path)
        selected = _parse_pages(args.get("pages"), total_pages)
        degrees = int(args["degrees"])

        if ctx.dry_run:
            return {"mode": "dry_run", "output": str(output), "rotated_pages": selected}

        with pikepdf.Pdf.open(input_path) as pdf, _writing(output, ctx) as target:
            for page_number in selected:
                pdf.pages[page_number - 1].rotate(degrees, relative=True)
            pdf.save(target)
        return {"output": str(output), "rotated_pages": selected}


class RemovePagesStep(Handler):
    name = "remove_pages"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        pikepdf = _require_pikepdf()
        input_path = _input_path(args, ctx)
        output = _output_path(input_path, args, ctx, "-removed.pdf")
        total_pages = _pdf_page_count(input_path)
        remove = set(_parse_pages(args["pages"], total_pages))
        keep = [page for page in range(1, total_pages + 1) if page not in remove]
        if not keep:
            # As native: an empty document is never what was asked for, preview or run.
            raise ValueError("refusing to remove every page (would produce an empty document)")

        if ctx.dry_run:
            return {"mode": "dry_run", "output": str(output), "pages": len(keep)}

        with pikepdf.Pdf.open(input_path) as source, _writing(output, ctx) as target:
            result = pikepdf.Pdf.new()
            for page_number in keep:
                result.pages.append(source.pages[page_number - 1])
            result.save(target)
            result.close()
        return {"output": str(output), "pages": len(keep)}


class ReorderPagesStep(Handler):
    name = "reorder_pages"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        pikepdf = _require_pikepdf()
        input_path = _input_path(args, ctx)
        output = _output_path(input_path, args, ctx, "-reordered.pdf")
        total_pages = _pdf_page_count(input_path)
        raw_order = str(args["order"]).strip().lower()
        if raw_order in ("reverse", "reversed"):
            order = list(range(total_pages, 0, -1))
        else:
            # The model can't know the page count, so it may pad the sequence
            # with non-existent pages (e.g. "3,1,2,4,5,…" on a 3-page doc). Drop
            # out-of-range entries (reorder also legitimately accepts a subset),
            # but require at least one valid page so we never produce an empty doc.
            parsed = _parse_pages(args["order"], total_pages, strict=False)
            order = [p for p in parsed if 1 <= p <= total_pages]
            if not order:
                raise ValueError(
                    f"Reorder sequence {args['order']!r} has no valid pages (1-{total_pages})"
                )

        if ctx.dry_run:
            return {"mode": "dry_run", "output": str(output), "pages": len(order)}

        with pikepdf.Pdf.open(input_path) as source, _writing(output, ctx) as target:
            result = pikepdf.Pdf.new()
            for page_number in order:
                result.pages.append(source.pages[page_number - 1])
            result.save(target)
            result.close()
        return {"output": str(output), "pages": len(order)}


class WatermarkStep(Handler):
    name = "watermark"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        input_path = _input_path(args, ctx)
        output = _output_path(input_path, args, ctx, "-watermarked.pdf")
        text = args.get("text")
        image = args.get("image")

        if ctx.dry_run:
            return {"mode": "dry_run", "output": str(output)}

        if text:
            with _writing(output, ctx) as target:
                _write_text_overlay_pdf(
                    input_path=input_path,
                    output=target,
                    text_for_page=lambda _index: str(text),
                    position=args.get("position", "center"),
                    opacity=float(args.get("opacity", 0.35)),
                    font_size=42,
                )
        elif image:
            image_path = _resolve(image, ctx)
            with _writing(output, ctx) as target:
                _write_image_overlay_pdf(
                    input_path=input_path,
                    output=target,
                    image_path=image_path,
                    position=args.get("position", "center"),
                    opacity=float(args.get("opacity", 0.35)),
                )
        else:
            raise ValueError("watermark requires text or image")
        return {"output": str(output)}


class AddPageNumbersStep(Handler):
    name = "add_page_numbers"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        input_path = _input_path(args, ctx)
        output = _output_path(input_path, args, ctx, "-numbered.pdf")
        start_at = int(args.get("start_at", 1))

        if ctx.dry_run:
            return {"mode": "dry_run", "output": str(output)}

        with _writing(output, ctx) as target:
            _write_text_overlay_pdf(
                input_path=input_path,
                output=target,
                text_for_page=lambda index: str(start_at + index - 1),
                position=args.get("position", "bottom-center"),
                opacity=1.0,
                font_size=12,
            )
        return {"output": str(output)}


class ProtectPdfStep(Handler):
    name = "protect_pdf"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        pikepdf = _require_pikepdf()
        input_path = _input_path(args, ctx)
        output = _output_path(input_path, args, ctx, "-protected.pdf")

        if ctx.dry_run:
            return {"mode": "dry_run", "output": str(output)}

        with pikepdf.Pdf.open(input_path) as pdf, _writing(output, ctx) as target:
            pdf.save(
                target, encryption=pikepdf.Encryption(user=args["password"], owner=args["password"])
            )
        return {"output": str(output)}


class UnlockPdfStep(Handler):
    name = "unlock_pdf"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        pikepdf = _require_pikepdf()
        input_path = _input_path(args, ctx)
        output = _output_path(input_path, args, ctx, "-unlocked.pdf")

        if ctx.dry_run:
            return {"mode": "dry_run", "output": str(output)}

        with (
            pikepdf.Pdf.open(input_path, password=args["password"]) as pdf,
            _writing(output, ctx) as target,
        ):
            pdf.save(target)
        return {"output": str(output)}


class ConvertDocumentStep(Handler):
    name = "convert_document"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        input_path = _input_path(args, ctx)
        to_format = args["to_format"].lower()
        output_path = _output_path(input_path, args, ctx, f".{to_format}")

        if ctx.dry_run:
            return {
                "mode": "dry_run",
                "input": str(input_path),
                "output": str(output_path),
                "to_format": to_format,
            }

        if to_format in {"txt", "md"}:
            text = "\n".join(record["text"] for record in _extract_text_records(input_path))
            with _writing(output_path, ctx) as target:
                target.write_text(text, encoding="utf-8")
            return {"output": str(output_path)}

        if input_path.suffix.lower() in IMAGE_SUFFIXES and to_format == "pdf":
            Image = _require_pillow_image()
            with Image.open(input_path) as image, _writing(output_path, ctx) as target:
                image.convert("RGB").save(target, "PDF", resolution=100.0)
            return {"output": str(output_path)}

        if input_path.suffix.lower() in OFFICE_SUFFIXES and to_format == "pdf":
            soffice = _deps.detect_external_tools()["soffice"]
            if not soffice:
                raise DocumentsDependencyError("Install LibreOffice for Office-to-PDF conversion.")
            # Into a private directory, never the output's folder: soffice names its result
            # `<stem>.pdf` and writes it over any file of that name, so `sample.docx -> conv.pdf`
            # silently destroyed the user's own `sample.pdf` (found 2026-09-26).
            import tempfile

            with tempfile.TemporaryDirectory(prefix="knaif-soffice-") as private:
                ctx.exec(
                    "soffice",
                    [
                        soffice,
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        private,
                        str(input_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                produced = Path(private) / f"{input_path.stem}.pdf"
                with _writing(output_path, ctx) as target:
                    shutil.move(str(produced), str(target))
            return {"output": str(output_path)}

        raise NotImplementedError(
            f"Conversion to {to_format!r} is not implemented in this slice yet."
        )


class RunCompressStep(Handler):
    name = "run_compress"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        input_path = _input_path(args, ctx)
        output_path = _output_path(input_path, args, ctx, "-compressed.pdf")
        quality = args.get("compress_quality", "balanced")
        profile = _profile(ctx.skill_dir, quality)
        # Dry-run chain preview: an upstream output may not exist yet.
        original_size = input_path.stat().st_size if input_path.exists() else 0
        tools = _deps.detect_external_tools()
        use_gs = bool(tools["gs"] and quality in {"small", "balanced"})
        use_raster = bool(quality == "small" and not tools["gs"])
        method = (
            "ghostscript" if use_gs else "rasterize-pillow" if use_raster else "lossless-optimize"
        )
        text_preserved = not use_raster
        if ctx.dry_run:
            result = {
                "mode": "dry_run",
                "input": str(input_path),
                "output": str(output_path),
                "original_size": original_size,
                "method": method,
                "text_preserved": text_preserved,
            }
            if use_raster:
                result["warning"] = "Raster compression removes selectable text and vector content."
            return result

        with _writing(output_path, ctx) as target:
            if use_gs:
                _ghostscript_compress(
                    input_path,
                    target,
                    tools["gs"],
                    profile.get("ghostscript_pdfsettings", "/ebook"),
                    ctx,
                )
            elif use_raster:
                raster_profile = profile.get("rasterize_fallback") or {}
                _rasterize_compress(
                    input_path,
                    target,
                    dpi=int(raster_profile.get("dpi", 120)),
                    jpeg_quality=int(raster_profile.get("jpeg_quality", 60)),
                )
            else:
                _lossless_compress(input_path, target)
            new_size = target.stat().st_size if target.exists() else 0
            kept_original = bool(original_size) and new_size >= original_size
            if kept_original:
                # Not smaller is not a compression: every method grew the fixtures
                # (Ghostscript's overhead on a small text PDF, 2227 -> 3952 bytes). Keep the
                # input's own bytes, which also keeps its text where the raster method would
                # have flattened it. `method` still names the backend that ran;
                # `kept_original` says its result was not used.
                shutil.copyfile(input_path, target)
                new_size = original_size
            text_preserved, use_raster = True, False
        percent = ((original_size - new_size) / original_size * 100) if original_size else 0.0
        result = {
            "output": str(output_path),
            "original_size": original_size,
            "new_size": new_size,
            "percent": percent,
            "method": method,
            "text_preserved": text_preserved,
            "kept_original": kept_original,
        }
        if use_raster:
            result["warning"] = "Raster compression removes selectable text and vector content."
        return result


class RunOcrStep(Handler):
    name = "run_ocr"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        input_path = _input_path(args, ctx)
        output_path = _output_path(input_path, args, ctx, "-ocr.pdf")
        language = args.get("language", "eng")
        inspection = args.get("inspection") or {}
        pages = int(inspection.get("pages") or 1)
        suffix = input_path.suffix.lower()
        skipped = bool(suffix == PDF_SUFFIX and inspection.get("has_text_layer"))

        result = {
            "input": str(input_path),
            "output": str(output_path),
            "language": language,
            "method": "copy-existing-text-layer" if skipped else "tesseract-pypdfium2",
            "pages": pages,
            "skipped": skipped,
        }
        if ctx.dry_run:
            return {"mode": "dry_run", **result}

        if suffix not in (PDF_SUFFIX, *IMAGE_SUFFIXES):
            raise ValueError(f"OCR supports PDF and image inputs, not {suffix or input_path.name}.")
        with _writing(output_path, ctx) as target:
            if suffix == PDF_SUFFIX and skipped:
                shutil.copyfile(input_path, target)
            elif suffix == PDF_SUFFIX:
                result["pages"] = _write_ocr_pdf(input_path, target, language)
            else:
                result["pages"] = _write_ocr_image_pdf(input_path, target, language)
        return result


class VerifyOutputStep(Handler):
    name = "verify_output"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        compression = args["compression"]
        output = compression.get("output")
        exists = bool(output and Path(output).exists())
        original_pages = args["inspection"].get("pages")
        new_pages = _pdf_page_count(Path(output)) if exists else None
        return {
            "output": output,
            "exists": exists,
            "original_size": compression.get("original_size"),
            "new_size": compression.get("new_size"),
            "percent": compression.get("percent"),
            "method": compression.get("method"),
            "text_preserved": compression.get("text_preserved"),
            "warning": compression.get("warning"),
            "original_pages": original_pages,
            "new_pages": new_pages,
            "pages_preserved": bool(exists and original_pages == new_pages),
        }
