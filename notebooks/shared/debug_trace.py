"""Full pipeline debug trace renderer for skill notebooks.

Import in the notebook with:
    from debug_trace import show_debug
"""

from __future__ import annotations

import html as _html
import json as _json

from IPython.display import HTML, display


def show_debug(tester) -> None:
    """Render the full pipeline trace from the last tester.run().

    Sections (in order):
      ① User input
      ② System prompt sent to LLM
      ③ User message sent to LLM
      ④ Raw LLM token stream (plan accumulator)
      ⑤ LLM thinking / chain-of-thought (if any)
      ⑥ Parsed intent plan (LLM JSON → Python dict)
      ⑦ Execution steps — each expanded handler with args-in and result-out
      ⑧ Timing summary
      ⑨ Error (if the run raised an exception)
    """
    d = getattr(tester, "_debug_state", {})
    if not d:
        display(
            HTML(
                "<i style='color:#888'>No debug data yet — run a query first, "
                "then re-run this cell.</i>"
            )
        )
        return

    # ── helpers ────────────────────────────────────────────────────────────────

    def section(icon_title, body_html, header_bg="#1e3a5f", body_bg="#f0f4ff"):
        return (
            f"<div style='margin:10px 0;border:1px solid {header_bg};"
            f"border-radius:5px;overflow:hidden'>"
            f"<div style='background:{header_bg};color:#fff;padding:6px 12px;"
            f"font-weight:bold;font-size:0.93em'>{icon_title}</div>"
            f"<div style='padding:10px 12px;background:{body_bg};color:#111;"
            f"font-size:0.87em'>{body_html}</div>"
            f"</div>"
        )

    def code(text, max_chars=None):
        if not text:
            return "<i style='color:#888'>(empty)</i>"
        s = str(text)
        if max_chars and len(s) > max_chars:
            s = s[:max_chars] + f"\n… ({len(str(text)) - max_chars:,} more chars)"
        return (
            f"<pre style='margin:0;overflow-x:auto;white-space:pre-wrap;"
            f"word-break:break-all;background:#fff;color:#111;border:1px solid #ddd;"
            f"padding:8px;border-radius:3px'>{_html.escape(s)}</pre>"
        )

    def json_block(obj):
        return code(_json.dumps(obj, indent=2, default=str))

    parts = []

    # ── ① User input ───────────────────────────────────────────────────────────
    parts.append(
        section(
            "① User input",
            f"<b style='font-size:1.05em;color:#111'>{_html.escape(d.get('user_input', ''))}</b>",
            "#1e3a5f",
            "#f0f4ff",
        )
    )

    # ── ② System prompt ────────────────────────────────────────────────────────
    sys_msg = d.get("system_msg")
    if sys_msg:
        char_count = len(sys_msg)
        parts.append(
            section(
                f"② System prompt sent to LLM &nbsp;<small>({char_count:,} chars)</small>",
                code(sys_msg),
                "#374151",
                "#f9fafb",
            )
        )
    else:
        parts.append(
            section(
                "② System prompt sent to LLM",
                "<i style='color:#555'>Not captured — agent.build_prompt() may not have been called yet.</i>",
                "#374151",
                "#f9fafb",
            )
        )

    # ── ③ User message ─────────────────────────────────────────────────────────
    usr_msg = d.get("user_msg")
    parts.append(
        section(
            "③ User message sent to LLM",
            code(usr_msg) if usr_msg else "<i style='color:#555'>Not captured.</i>",
            "#374151",
            "#f9fafb",
        )
    )

    # ── ④ Raw LLM token stream ─────────────────────────────────────────────────
    raw = d.get("raw_llm_output", "")
    parts.append(
        section(
            f"④ Raw LLM output (plan token stream) &nbsp;<small>({len(raw):,} chars)</small>",
            (
                code(raw)
                if raw
                else "<i style='color:#555'>Empty — mock inference or no model loaded.</i>"
            ),
            "#065f46",
            "#f0fdf4",
        )
    )

    # ── ⑤ LLM thinking ─────────────────────────────────────────────────────────
    thinking = d.get("thinking", "")
    if thinking:
        parts.append(
            section(
                f"⑤ LLM thinking / chain-of-thought &nbsp;<small>({len(thinking):,} chars)</small>",
                code(thinking),
                "#4b5563",
                "#fafafa",
            )
        )
    else:
        parts.append(
            section(
                "⑤ LLM thinking / chain-of-thought",
                "<i style='color:#555'>None — thinking disabled or model does not emit &lt;think&gt; blocks.</i>",
                "#4b5563",
                "#fafafa",
            )
        )

    # ── ⑥ Parsed intent plan ───────────────────────────────────────────────────
    parsed = d.get("parsed_plan")
    parse_err = d.get("llm_parse_error")
    plan_body = ""
    if parse_err:
        plan_body += (
            f"<div style='color:#b91c1c;margin-bottom:6px'>"
            f"⚠ Parse error: {_html.escape(str(parse_err))}</div>"
        )
    plan_body += json_block(parsed) if parsed else "<i style='color:#555'>None</i>"
    parts.append(
        section(
            "⑥ Parsed intent plan &nbsp;<small>(LLM JSON → Python dict, before expansion)</small>",
            plan_body,
            "#1d4ed8",
            "#eff6ff",
        )
    )

    # ── ⑦ Execution steps ──────────────────────────────────────────────────────
    results = d.get("execution_results", [])
    if results:
        step_html = ""
        for i, r in enumerate(results, 1):
            tool = r.get("tool", "?")
            args = r.get("args", {})
            result = r.get("result", {})
            out_var = r.get("output")
            ms = r.get("duration_ms")
            t_badge = (
                f"<span style='float:right;color:#444;font-size:0.85em'>{ms:.1f} ms</span>"
                if ms is not None
                else ""
            )
            out_ann = (
                f"&nbsp;<span style='color:#5b21b6;font-size:0.85em'>→ {_html.escape(str(out_var))}</span>"
                if out_var
                else ""
            )
            args_html = json_block(args) if args else "<i style='color:#555'>none</i>"
            step_html += (
                f"<details style='margin:4px 0'>"
                f"<summary style='cursor:pointer;padding:5px 8px;background:#d1d5db;"
                f"color:#111;border-radius:4px;list-style:none'>"
                f"▶ &nbsp;<b>Step {i}: {_html.escape(tool)}</b>{out_ann}{t_badge}"
                f"</summary>"
                f"<div style='padding:8px 12px;background:#fff;color:#111;"
                f"border:1px solid #d1d5db;border-top:none;border-radius:0 0 4px 4px'>"
                f"<b style='display:block;margin-bottom:4px'>↘ Args / Input:</b>"
                f"{args_html}"
                f"<b style='display:block;margin:8px 0 4px'>↗ Result / Output:</b>"
                f"{json_block(result)}"
                f"</div>"
                f"</details>"
            )
        parts.append(
            section(
                f"⑦ Execution steps &nbsp;<small>({len(results)} step(s), expanded workflow)</small>",
                step_html,
                "#7c3aed",
                "#faf5ff",
            )
        )
    else:
        parts.append(
            section(
                "⑦ Execution steps",
                "<i style='color:#555'>No steps executed (plan was empty, declined, or cancelled).</i>",
                "#7c3aed",
                "#faf5ff",
            )
        )

    # ── ⑧ Timing summary ───────────────────────────────────────────────────────
    timing = d.get("timing", {})
    _labels = {
        "llm_ms": "LLM inference",
        "exec_ms": "Workflow execution",
        "total_ms": "Total end-to-end",
    }
    if timing:
        rows = "".join(
            f"<tr>"
            f"<td style='padding:3px 12px 3px 0;color:#1a1a1a'>{_labels.get(k, k)}</td>"
            f"<td style='padding:3px 0;font-weight:bold;color:#111;text-align:right'>{v:,.0f} ms</td>"
            f"</tr>"
            for k, v in timing.items()
        )
        timing_html = f"<table style='border-collapse:collapse;color:#111'>{rows}</table>"
    else:
        timing_html = "<i style='color:#555'>No timing data.</i>"
    parts.append(section("⑧ Timing summary", timing_html, "#92400e", "#fffbeb"))

    # ── ⑨ Error (if any) ───────────────────────────────────────────────────────
    error = d.get("error")
    if error:
        parts.append(
            section(
                "⑨ Error / traceback",
                f"<pre style='color:#b91c1c;margin:0;white-space:pre-wrap'>"
                f"{_html.escape(str(error))}</pre>",
                "#b91c1c",
                "#fef2f2",
            )
        )

    display(
        HTML(
            "<div style='font-family:system-ui,sans-serif;max-width:960px;color:#111'>"
            "<h3 style='margin:0 0 6px;color:#111'>🔍 Full Pipeline Debug Trace</h3>"
            "<hr style='margin:0 0 8px'>" + "".join(parts) + "</div>"
        )
    )
