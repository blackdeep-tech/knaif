"""Corpus baseline reviewer widget.

Usage in a notebook::

    from baseline_reviewer import make_reviewer
    display(make_reviewer(pending, corpus, CORPUS, FIXTURE_DIR, SANDBOX))

The "Run" button executes the baseline command locally.  Input file lookup
uses the ``-i <file>`` convention so it currently works best for ffmpeg-style
commands; other skill types can still use Accept / Edit / Skip without it.
"""

from __future__ import annotations

import html as _html
import json as _json
import shutil
import subprocess
import threading as _threading
import traceback as _traceback
from pathlib import Path

import ipywidgets as widgets
from IPython.display import HTML as IHTML
from IPython.display import Audio, Image, Video, display

from knaif.evalsuite.chain import run_command_chain
from knaif.evalsuite.corpus import save_corpus

# ── media helpers ───────────────────────────────────────────────────────────────


def _esc(text: str) -> str:
    """HTML-escape text destined for a <pre>/HTML widget value."""
    return _html.escape(str(text))


def _ffprobe(path: Path) -> dict | None:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return _json.loads(proc.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, _json.JSONDecodeError):
        pass
    return None


def _check_criteria(criteria: dict, command: str, probe: dict | None) -> list[tuple]:
    if not criteria:
        return []
    streams = (probe or {}).get("streams", [])
    fmt = (probe or {}).get("format", {})
    v_st = [s for s in streams if s.get("codec_type") == "video"]
    a_st = [s for s in streams if s.get("codec_type") == "audio"]
    results = []
    for key, expected in criteria.items():
        if key == "container":
            actual = fmt.get("format_name", "")
            results.append((key, str(expected).lower() in actual.lower(), actual or "?"))
        elif key == "video_codec":
            actual = v_st[0].get("codec_name", "") if v_st else ""
            results.append((key, str(expected).lower() in actual.lower(), actual or "no video"))
        elif key == "audio_codec":
            actual = a_st[0].get("codec_name", "") if a_st else ""
            results.append((key, str(expected).lower() in actual.lower(), actual or "no audio"))
        elif key == "no_audio":
            passed = (not a_st) if expected else bool(a_st)
            results.append((key, passed, f"{len(a_st)} audio stream(s)"))
        elif key == "max_width":
            w = int(v_st[0].get("width", 0)) if v_st else 0
            results.append((key, 0 < w <= int(expected), f"width={w}"))
        elif key == "max_height":
            h = int(v_st[0].get("height", 0)) if v_st else 0
            results.append((key, 0 < h <= int(expected), f"height={h}"))
        elif key == "filters":
            for f in expected or []:
                found = f.lower() in command.lower()
                results.append(
                    (f"filter:{f}", found, "in command" if found else "missing from command")
                )
        else:
            results.append((key, None, f"unchecked — expected {expected!r}"))
    return results


def _criteria_html(results: list[tuple]) -> str:
    if not results:
        return "<small style='color:#6b7280'>No success_criteria defined</small>"
    rows_html = ""
    for label, passed, detail in results:
        icon, color = (
            ("✓", "green")
            if passed is True
            else ("✗", "#dc2626") if passed is False else ("?", "#6b7280")
        )
        rows_html += (
            f"<tr>"
            f"<td style='color:{color};font-weight:bold;padding:2px 8px'>{icon}</td>"
            f"<td style='padding:2px 8px'><code>{label}</code></td>"
            f"<td style='color:#6b7280;padding:2px 4px;font-size:12px'>{detail}</td>"
            f"</tr>"
        )
    return (
        f"<table style='border-collapse:collapse;font-size:13px;margin:4px 0'>{rows_html}</table>"
    )


def _preview_widget(path: Path):
    suffix = path.suffix.lower()
    try:
        if suffix in (".mp4", ".webm", ".ogg", ".mov", ".mkv", ".avi"):
            return Video(str(path), embed=True, width=560)
        if suffix in (".mp3", ".wav", ".aac", ".flac", ".m4a"):
            return Audio(str(path), embed=True)
        if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            return Image(str(path), width=480)
    except Exception:
        pass
    return None


# ── reviewer widget ─────────────────────────────────────────────────────────────


def make_reviewer(
    pending_rows: list,
    corpus: list,
    corpus_path,
    fixture_dir,
    sandbox,
    reviewer: str = "human",
) -> widgets.VBox:
    """Build and return the baseline corpus reviewer widget."""
    fixture_dir = Path(fixture_dir)
    sandbox = Path(sandbox)
    corpus_path = Path(corpus_path)

    W = widgets.Layout
    idx = [0]
    corpus_ref = [corpus]

    def _find_fixture_file(command: str) -> Path | None:
        toks = command.split()
        try:
            i_idx = toks.index("-i")
            name = Path(toks[i_idx + 1]).name
            p = fixture_dir / name
            return p if p.exists() else None
        except (ValueError, IndexError):
            return None

    # ── widgets ──────────────────────────────────────────────────────────────

    lbl_progress = widgets.HTML()
    lbl_id = widgets.HTML()
    lbl_utterance = widgets.HTML()
    lbl_meta = widgets.HTML()
    ta_command = widgets.Textarea(layout=W(width="100%", height="80px"))
    lbl_status = widgets.HTML()
    # Textual run log (result header, resolved argv, ffmpeg output, criteria). Set
    # via .value rather than display()'d into out_run because HTML display() from a
    # background thread is unreliable in VS Code's notebook renderer. out_run is
    # kept only for the media preview widgets, which do render from a thread.
    lbl_run_log = widgets.HTML()
    out_run = widgets.Output()

    btn_accept = widgets.Button(
        description="✓ Accept", button_style="success", layout=W(width="120px")
    )
    btn_edit = widgets.Button(description="✏ Edit", button_style="warning", layout=W(width="120px"))
    btn_skip = widgets.Button(description="→ Skip", button_style="", layout=W(width="120px"))
    btn_save = widgets.Button(
        description="💾 Save", button_style="primary", layout=W(width="120px", display="none")
    )
    btn_run = widgets.Button(description="▶ Run", button_style="info", layout=W(width="120px"))

    box = widgets.VBox(
        [
            lbl_progress,
            widgets.HTML("<hr style='margin:4px 0'>"),
            lbl_id,
            lbl_utterance,
            ta_command,
            lbl_meta,
            widgets.HBox(
                [btn_accept, btn_edit, btn_skip, btn_save, btn_run],
                layout=W(gap="6px", margin="6px 0"),
            ),
            lbl_status,
            lbl_run_log,
            out_run,
        ],
        layout=W(max_width="800px"),
    )

    # ── navigation ───────────────────────────────────────────────────────────

    def _load(i: int) -> None:
        out_run.clear_output()
        lbl_status.value = ""
        lbl_run_log.value = ""
        if i >= len(pending_rows):
            lbl_progress.value = f"<b>All {len(pending_rows)} rows reviewed.</b>"
            for w in (lbl_id, lbl_utterance, lbl_meta):
                w.value = ""
            ta_command.value = ""
            ta_command.disabled = True
            for b in (btn_accept, btn_edit, btn_skip, btn_save, btn_run):
                b.layout.display = "none"
            return

        row = pending_rows[i]
        corpus_pos = next((ci + 1 for ci, r in enumerate(corpus_ref[0]) if r.id == row.id), i + 1)
        lbl_progress.value = f"<small>Row {corpus_pos} of {len(corpus_ref[0])} ({i + 1} of {len(pending_rows)} pending)</small>"
        lbl_id.value = f"<b>{row.id}</b>"
        items = "".join(f"<li style='margin:1px 0'><i>{u}</i></li>" for u in row.utterances)
        lbl_utterance.value = (
            f"<ul style='margin:4px 0 4px 16px;padding:0;color:#374151'>{items}</ul>"
        )
        row_outputs = getattr(row, "outputs", None)
        if row_outputs:
            ta_command.value = "\n".join(o.get("command", "") for o in row_outputs)
        else:
            ta_command.value = (row.baseline or {}).get("command", "")
        ta_command.disabled = True
        btn_save.layout.display = "none"
        for b in (btn_accept, btn_edit, btn_skip, btn_run):
            b.layout.display = ""

        tool_s = (
            f"<code>{row.expected_tool}</code>"
            if row.expected_tool
            else "<span style='color:#6b7280'>—</span>"
        )
        fix_s = (
            f"<code>{row.fixture}</code>" if row.fixture else "<span style='color:#6b7280'>—</span>"
        )

        def _crit_codes(crit: dict) -> str:
            return " ".join(
                f"<code style='background:#f3f4f6;padding:1px 5px;border-radius:3px'>{k}={v!r}</code>"
                for k, v in (crit or {}).items()
            )

        if row_outputs:
            sc_s = "&nbsp;&nbsp;".join(
                f"<b>out{i}:</b> {_crit_codes(o.get('criteria')) or '—'}"
                for i, o in enumerate(row_outputs)
            )
        else:
            sc = row.success_criteria or {}
            sc_s = _crit_codes(sc) if sc else "<span style='color:#6b7280'>none</span>"
        lbl_meta.value = (
            f"<div style='font-size:12px;margin:4px 0;color:#374151'>"
            f"<b>tool:</b> {tool_s}&nbsp;&nbsp;"
            f"<b>fixture:</b> {fix_s}&nbsp;&nbsp;"
            f"<b>criteria:</b> {sc_s}</div>"
        )

    def _advance() -> None:
        idx[0] += 1
        _load(idx[0])

    # ── button handlers ──────────────────────────────────────────────────────

    def _on_accept(_):
        row = pending_rows[idx[0]]
        if getattr(row, "outputs", None):
            # Chain row: mark the outputs reviewed, not the single-command baseline.
            row.outputs_validated_by = reviewer
        else:
            row.baseline = {**(row.baseline or {}), "validated_by": reviewer}
        save_corpus(corpus_ref[0], corpus_path)
        lbl_status.value = f"<span style='color:green'>✓ {row.id} accepted</span>"
        _advance()

    def _on_edit(_):
        ta_command.disabled = False
        ta_command.focus()
        btn_save.layout.display = ""
        btn_accept.layout.display = btn_edit.layout.display = "none"

    def _on_save(_):
        row = pending_rows[idx[0]]
        row_outputs = getattr(row, "outputs", None)
        if row_outputs:
            lines = [ln.strip() for ln in ta_command.value.splitlines() if ln.strip()]
            if len(lines) != len(row_outputs):
                lbl_status.value = (
                    f"<span style='color:orange'>⚠ expected {len(row_outputs)} command(s), "
                    f"got {len(lines)} — not saved</span>"
                )
                return
            for o, cmd in zip(row_outputs, lines, strict=True):
                o["command"] = cmd
            row.outputs_validated_by = reviewer
            save_corpus(corpus_ref[0], corpus_path)
            lbl_status.value = f"<span style='color:green'>✓ {row.id} edited and accepted</span>"
            _advance()
            return

        new_cmd = ta_command.value.strip()
        if new_cmd:
            row.baseline = {**(row.baseline or {}), "command": new_cmd, "validated_by": reviewer}
            save_corpus(corpus_ref[0], corpus_path)
            lbl_status.value = f"<span style='color:green'>✓ {row.id} edited and accepted</span>"
        else:
            lbl_status.value = "<span style='color:orange'>⚠ Empty command — skipped</span>"
        _advance()

    def _on_skip(_):
        lbl_status.value = f"<span style='color:#888'>– {pending_rows[idx[0]].id} skipped</span>"
        _advance()

    def _run_multi(row, row_outputs):
        """Run a multi-output row's command chain and render each deliverable."""
        lines = [ln.strip() for ln in ta_command.value.splitlines() if ln.strip()]
        commands = (
            lines if len(lines) == len(row_outputs) else [o.get("command", "") for o in row_outputs]
        )

        out_dir = sandbox / "authoring_runs" / row.id
        if out_dir.exists():
            shutil.rmtree(out_dir)

        fix_file = None
        if getattr(row, "fixture", None):
            candidate = fixture_dir / row.fixture
            if candidate.exists():
                fix_file = candidate
        results = run_command_chain(commands, fixture_dir, out_dir, fixture_file=fix_file)
        all_ok = all(r["returncode"] == 0 for r in results) and len(results) == len(row_outputs)

        with out_run:
            for i, res in enumerate(results):
                crit = (row_outputs[i].get("criteria") if i < len(row_outputs) else {}) or {}
                ok = res["returncode"] == 0
                head = "✓" if ok else f"❌ exit {res['returncode']}"
                color = "green" if ok else "#dc2626"
                display(
                    IHTML(
                        f"<div style='font-weight:bold;margin:8px 0 2px;color:{color}'>"
                        f"out{i}: {head} — <code>{commands[i] if i < len(commands) else ''}</code></div>"
                    )
                )
                if res["stderr"].strip() and not ok:
                    display(
                        IHTML(
                            f"<pre style='font-size:11px;background:#fee2e2;padding:8px;"
                            f"border-radius:4px;white-space:pre-wrap;max-height:200px;"
                            f"overflow-y:auto'>{res['stderr'][-2000:]}</pre>"
                        )
                    )
                out_path = res["output"]
                if out_path.exists():
                    probe = _ffprobe(out_path)
                    display(IHTML(_criteria_html(_check_criteria(crit, commands[i], probe))))
                    kb = out_path.stat().st_size // 1024
                    display(
                        IHTML(
                            f"<div style='margin:6px 0 2px'><code style='font-weight:bold'>"
                            f"{out_path.name}</code> <small style='color:#6b7280'>({kb} KB)</small></div>"
                        )
                    )
                    pw = _preview_widget(out_path)
                    if pw is not None:
                        display(pw)

        produced = sum(1 for r in results if r["output"].exists())
        lbl_status.value = (
            f"<span style='color:#6b7280'>▶ done — {produced} file(s) in "
            f"authoring_runs/{row.id}/</span>"
            if all_ok
            else "<span style='color:#dc2626'>❌ chain failed</span>"
        )
        btn_run.disabled = False

    def _on_run(_):
        row = pending_rows[idx[0]]
        btn_run.disabled = True
        out_run.clear_output()
        lbl_run_log.value = ""
        lbl_status.value = "<span style='color:#6b7280'>⏳ Running…</span>"

        def _worker_impl():
            row_outputs = getattr(row, "outputs", None)
            if row_outputs:
                _run_multi(row, row_outputs)
                return

            command = ta_command.value.strip() or (row.baseline or {}).get("command", "")

            fixture_file = _find_fixture_file(command)
            if fixture_file is None and getattr(row, "fixture", None):
                candidate = fixture_dir / row.fixture
                if candidate.exists():
                    fixture_file = candidate
            if fixture_file is None:
                toks = command.split()
                try:
                    name = Path(toks[toks.index("-i") + 1]).name
                except (ValueError, IndexError):
                    name = "?"
                lbl_status.value = (
                    f"<span style='color:red'>❌ {name!r} not found in {fixture_dir} — "
                    f"run Step 1 to generate fixtures</span>"
                )
                btn_run.disabled = False
                return

            out_dir = sandbox / "authoring_runs" / row.id
            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

            toks = command.split()
            for _i, _tok in enumerate(toks):
                if _tok == "-i" and _i + 1 < len(toks):
                    _p = fixture_dir / Path(toks[_i + 1]).name
                    if _p.exists():
                        toks[_i + 1] = str(_p)
                    elif fixture_file is not None:
                        toks[_i + 1] = str(fixture_file)

            out_path = out_dir / Path(toks[-1]).name
            toks[-1] = str(out_path)

            try:
                proc = subprocess.run(toks, capture_output=True, text=True, timeout=120)
            except FileNotFoundError:
                lbl_status.value = "<span style='color:red'>❌ command not found on PATH</span>"
                btn_run.disabled = False
                return
            except subprocess.TimeoutExpired:
                lbl_status.value = "<span style='color:red'>❌ Timeout after 120s</span>"
                btn_run.disabled = False
                return

            output_files = sorted(f for f in out_dir.iterdir() if f.is_file())

            # Build the run log as a single HTML string and assign it to lbl_run_log.
            # Setting .value is a traitlet update, which renders reliably from a
            # background thread (unlike display() into an Output widget in VS Code).
            parts: list[str] = []
            if proc.returncode != 0:
                parts.append(
                    f"<div style='color:#dc2626;font-weight:bold;margin:4px 0'>"
                    f"❌ exit {proc.returncode}</div>"
                )
            else:
                parts.append(
                    "<div style='color:green;font-weight:bold;margin:4px 0'>✓ succeeded</div>"
                )

            # Exact argv handed to subprocess (no shell) — the tokens ffmpeg
            # actually received, so quoting/escaping issues are visible here.
            parts.append(
                "<div style='font-size:11px;color:#6b7280;margin:4px 0'>resolved argv:</div>"
                f"<pre style='font-size:11px;background:#f3f4f6;padding:8px;border-radius:4px;"
                f"white-space:pre-wrap;margin:4px 0'>{_esc(repr(toks))}</pre>"
            )

            if proc.stderr.strip():
                stderr_bg = "#fee2e2" if proc.returncode != 0 else "#f9fafb"
                parts.append(
                    f"<details open style='margin:4px 0'>"
                    f"<summary style='font-size:11px;color:#6b7280;cursor:pointer'>ffmpeg output</summary>"
                    f"<pre style='font-size:11px;background:{stderr_bg};padding:8px;border-radius:4px;"
                    f"white-space:pre-wrap;max-height:300px;overflow-y:auto;margin:4px 0'>"
                    f"{_esc(proc.stderr[-3000:])}</pre></details>"
                )

            if out_path.exists():
                probe = _ffprobe(out_path)
                parts.append(_criteria_html(_check_criteria(row.success_criteria, command, probe)))

            if output_files:
                parts.append(
                    f"<div style='margin:8px 0 4px;font-size:12px;color:#6b7280'>"
                    f"Output → <code>{out_dir}</code></div>"
                )
                for f in output_files:
                    kb = f.stat().st_size // 1024
                    parts.append(
                        f"<div style='margin:6px 0 2px'>"
                        f"<code style='font-weight:bold'>{f.name}</code>"
                        f" <small style='color:#6b7280'>({kb} KB)</small></div>"
                    )
            else:
                parts.append("<span style='color:orange'>⚠ No output files produced</span>")

            lbl_run_log.value = "".join(parts)

            # Media previews are real widgets and do render from a thread, so keep
            # them in out_run.
            if output_files:
                with out_run:
                    for f in output_files:
                        pw = _preview_widget(f)
                        if pw is not None:
                            display(pw)

            lbl_status.value = (
                f"<span style='color:#6b7280'>▶ done — {len(output_files)} file(s) in "
                f"authoring_runs/{row.id}/</span>"
                if proc.returncode == 0
                else ""
            )
            btn_run.disabled = False

        def _worker():
            try:
                _worker_impl()
            except Exception:  # noqa: BLE001 — surface any failure in the notebook
                tb = _traceback.format_exc()
                lbl_run_log.value = (
                    "<div style='color:#dc2626;font-weight:bold;margin:4px 0'>"
                    "❌ Python error while running</div>"
                    f"<pre style='font-size:11px;background:#fee2e2;padding:8px;"
                    f"border-radius:4px;white-space:pre-wrap;max-height:300px;"
                    f"overflow-y:auto;margin:4px 0'>{_esc(tb)}</pre>"
                )
                lbl_status.value = "<span style='color:red'>❌ error — see log above</span>"
                btn_run.disabled = False

        _threading.Thread(target=_worker, daemon=True).start()

    btn_accept.on_click(_on_accept)
    btn_edit.on_click(_on_edit)
    btn_save.on_click(_on_save)
    btn_skip.on_click(_on_skip)
    btn_run.on_click(_on_run)

    _load(0)
    return box
