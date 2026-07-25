"""documents skill — Intent expanders (see handlers.py)."""

from __future__ import annotations

from typing import Any

from knaif.tool import Intent


class CompressPdfIntent(Intent):
    name = "compress_pdf"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        compress_quality = args.get("compress_quality", "balanced")
        run_args = {
            "input": args["input"],
            "compress_quality": compress_quality,
            "inspection": "$inspection",
        }
        if "output" in args:
            run_args["output"] = args["output"]
        return [
            {
                "tool": "inspect_document",
                "args": {"input": args["input"]},
                "output": "$inspection",
            },
            {"tool": "run_compress", "args": run_args, "output": "$compressed"},
            {
                "tool": "verify_output",
                "args": {"compression": "$compressed", "inspection": "$inspection"},
            },
        ]

    def summarize(self, args: dict[str, Any], **kw: Any) -> str:
        quality = args.get("compress_quality", "balanced")
        return f"compress {args.get('input', 'PDF')} with {quality} quality"


class OcrDocumentIntent(Intent):
    name = "ocr_document"

    def expand(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        run_args = {
            "input": args["input"],
            "inspection": "$inspection",
        }
        if "output" in args:
            run_args["output"] = args["output"]
        if "language" in args:
            run_args["language"] = args["language"]
        return [
            {
                "tool": "inspect_document",
                "args": {"input": args["input"]},
                "output": "$inspection",
            },
            {"tool": "run_ocr", "args": run_args},
        ]

    def summarize(self, args: dict[str, Any], **kw: Any) -> str:
        language = args.get("language", "eng")
        return f"make {args.get('input', 'document')} searchable with OCR language {language}"
