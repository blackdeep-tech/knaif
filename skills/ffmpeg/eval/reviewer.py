"""FFmpeg Reviewer — renders an HTML card for a single eval row."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote

# Media extensions → HTML element type
_VIDEO_EXTS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".avi"})
_AUDIO_EXTS = frozenset({".mp3", ".wav", ".aac", ".ogg", ".flac"})
_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"})

# MIME types for <source type="..."> — tells the browser which codec stack to
# initialise so it doesn't skip audio when it can't probe the remote file.
_VIDEO_MIME: dict[str, str] = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
}
_AUDIO_MIME: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


class FfmpegReviewer:
    """Implements the Reviewer protocol for the ffmpeg skill."""

    def render_row(
        self,
        row: dict[str, Any],
        arm_outputs: dict[str, Any],
        reference: Path | None,
    ) -> str:
        row_id = row.get("id", "")
        utterances = row.get("utterances") or []
        tags = row.get("tags") or []

        utterances_html = self._render_utterances(utterances)
        tags_html = " ".join(f'<span class="tag">{_esc(t)}</span>' for t in tags)

        # Fixture slot (reserved key)
        fixture_path = arm_outputs.get("_fixture")
        fixture_html = ""
        if fixture_path is not None:
            fixture_html = (
                '<div class="arm-card">'
                "<h4>fixture (input)</h4>" + self._media_tag(fixture_path) + "</div>"
            )

        # Reference / baseline
        ref_html = self._render_reference(reference, arm_outputs.get("_reference_src"))

        # Arm outputs (skip _-prefixed metadata keys)
        arms_html = self._render_arms(arm_outputs)

        # ffprobe diff table
        scores: dict[str, tuple[list[str], list[str]]] = arm_outputs.get("_scores") or {}
        diff_table = self._render_scores_table(scores) if scores else ""

        # Review CLI command
        review_cmd = arm_outputs.get("_review_cmd", "")
        review_html = (
            f'<p class="review-cmd">Mark reviewed: <code>{_esc(review_cmd)}</code></p>'
            if review_cmd
            else ""
        )

        return (
            f'<div class="eval-card" id="row-{_esc(row_id)}">'
            f"<h3>{_esc(row_id)}</h3>"
            f"{utterances_html}"
            f'<p class="tags">{tags_html}</p>'
            f'<div class="arm-grid">{fixture_html}{ref_html}{arms_html}</div>'
            f"{diff_table}"
            f"{review_html}"
            f"</div>"
        )

    # ── private helpers ───────────────────────────────────────────────────────

    def _render_utterances(self, utterances: list[str]) -> str:
        if not utterances:
            return ""
        if len(utterances) == 1:
            return f'<p class="utterance">{_esc(utterances[0])}</p>'
        items = "".join(f"<li>{_esc(u)}</li>" for u in utterances)
        return f'<ul class="utterances">{items}</ul>'

    def _render_arms(self, arm_outputs: dict[str, Any]) -> str:
        src_overrides: dict[str, str] = arm_outputs.get("_src_overrides") or {}
        parts: list[str] = []
        for arm_name, path in arm_outputs.items():
            if arm_name.startswith("_"):
                continue
            src = src_overrides.get(arm_name)
            if path is not None:
                p = path if isinstance(path, Path) else Path(str(path))
                media = (
                    self._media_tag(p, src=src)
                    if p.exists()
                    else '<p class="missing">file missing</p>'
                )
            else:
                media = '<p class="missing">no output</p>'
            parts.append(f'<div class="arm-card"><h4>{_esc(arm_name)}</h4>{media}</div>')
        return "".join(parts)

    def _render_reference(self, reference: Path | None, src_override: str | None = None) -> str:
        if reference is None:
            return ""
        if reference.exists():
            return (
                '<div class="arm-card">'
                "<h4>reference (baseline)</h4>"
                + self._media_tag(reference, src=src_override)
                + "</div>"
            )
        return (
            f'<div class="arm-card">'
            f"<h4>reference (baseline)</h4>"
            f'<p class="missing">{_esc(reference.name)} (missing)</p>'
            f"</div>"
        )

    def _media_tag(self, path: Path, src: str | None = None) -> str:
        actual_src = _esc(src if src is not None else _path_fwd(str(path)))
        ext = path.suffix.lower()
        if ext in _VIDEO_EXTS:
            mime = _VIDEO_MIME.get(ext, "video/mp4")
            return f'<video controls><source src="{actual_src}" type="{mime}"></video>'
        if ext in _AUDIO_EXTS:
            mime = _AUDIO_MIME.get(ext, "audio/mpeg")
            return f'<audio controls><source src="{actual_src}" type="{mime}"></audio>'
        if ext in _IMAGE_EXTS:
            return f'<img class="media" src="{actual_src}" alt="{_esc(path.name)}">'
        return f'<a href="{actual_src}">download {_esc(path.name)}</a>'

    def _render_scores_table(self, scores: dict[str, tuple[list[str], list[str]]]) -> str:
        arm_names = [k for k in scores if not k.startswith("_")]
        if not arm_names:
            return ""

        # Build property → {arm: "pass"|"fail"} index
        props: dict[str, dict[str, str]] = {}
        for arm_name in arm_names:
            matched, failed = scores[arm_name]
            for prop in matched:
                props.setdefault(prop, {})[arm_name] = "pass"
            for prop in failed:
                props.setdefault(prop, {})[arm_name] = "fail"

        if not props:
            return ""

        header = (
            "<tr><th>Property</th>" + "".join(f"<th>{_esc(a)}</th>" for a in arm_names) + "</tr>"
        )
        rows_html = []
        for prop in sorted(props.keys()):
            cells = ""
            for arm_name in arm_names:
                status = props[prop].get(arm_name, "—")
                color = "green" if status == "pass" else ("#c00" if status == "fail" else "#999")
                cells += f'<td style="color:{color}">{_esc(status)}</td>'
            rows_html.append(f"<tr><td>{_esc(prop)}</td>{cells}</tr>")

        return (
            '<table class="ffprobe-diff">'
            f"<thead>{header}</thead>"
            f"<tbody>{''.join(rows_html)}</tbody>"
            "</table>"
        )


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _path_fwd(p: str) -> str:
    fwd = p.replace("\\", "/")
    return "/".join(_url_quote(seg, safe="") for seg in fwd.split("/"))


REVIEWER = FfmpegReviewer
