"""The terminal view of ``knaif-cli run``: the same tree, symbols and timings as native ``knaif run``.

**One display for both CLIs** (owner, 2026-10-23). Native ``apps/cli/src/ui.rs`` is the
reference; these are its pure renderers, ported line for line and pinned byte for byte by
``contracts/parity/ui_render_cases.json``. Durations are seconds (``None`` when nothing was
measured); a :class:`Style` decides only whether ANSI colors are added.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

# ── Style ─────────────────────────────────────────────────────────────────────────────────────


class Tone(Enum):
    """What a piece of text means; the style decides how that looks."""

    OK = auto()
    FAIL = auto()
    WARN = auto()
    DIM = auto()
    ACCENT = auto()
    BOLD = auto()
    BRAND = auto()  # the wordmark's coral
    DARK = auto()  # the wordmark's dark letters: the terminal's foreground, in bold


_ANSI = {
    Tone.OK: "32",
    Tone.FAIL: "31",
    Tone.WARN: "33",
    Tone.DIM: "90",
    Tone.ACCENT: "36",
    Tone.BOLD: "1",
    Tone.DARK: "1",
    Tone.BRAND: "38;2;255;90;95",
}


@dataclass(frozen=True)
class Style:
    """Whether to emit ANSI colors. The tree and its symbols are drawn either way."""

    color: bool

    def paint(self, tone: Tone, text: str) -> str:
        if not self.color:
            return text
        return f"\x1b[{_ANSI[tone]}m{text}\x1b[0m"


# ── Pure renderers ────────────────────────────────────────────────────────────────────────────


def _round(x: float) -> int:
    """Rust's ``f64::round``: halves away from zero (Python's ``round`` goes to even)."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def fmt_secs(seconds: float) -> str:
    """``1.842 s``: three decimals, always seconds."""
    return f"{seconds:.3f} s"


#: Width of the label column before the leader dots reach the timing.
LEADER_WIDTH = 40


def leader(style: Style, label: str, value: str) -> str:
    """``label ········ value``, the dots filling to a fixed column so timings line up."""
    return _leader_painted(style, label, len(label), value)


def _leader_painted(style: Style, label: str, shown_len: int, value: str) -> str:
    dots = "·" * max(LEADER_WIDTH - shown_len, 2)
    return f"{label} {style.paint(Tone.DIM, dots)} {value}"


def model_label(stem: str) -> str:
    """A model file's name without its quantization suffix: ``knaif-qwen3-4b-v2-q4_k_m`` ->
    ``knaif-qwen3-4b-v2``. What a person recognizes, not what is on disk."""
    lower = stem.lower()
    cut = None
    at = lower.find("-q")
    while at != -1:
        if lower[at + 2 : at + 3].isdigit() and lower[at + 2 : at + 3].isascii():
            cut = at
        at = lower.find("-q", at + 2)
    if cut is None:
        f16 = lower.rfind("-f16")
        cut = f16 if f16 != -1 else None
    return stem if cut is None else stem[:cut]


def render_header(style: Style, skill: str, model: str | None) -> str:
    parts = [style.paint(Tone.BOLD, "knaif"), skill]
    if model:
        parts.append(model)
    return f" {style.paint(Tone.DIM, ' · ').join(parts)}\n {style.paint(Tone.DIM, '│')}"


def render_planning(style: Style, load: float | None, infer: float | None) -> str:
    total = (load or 0.0) + (infer or 0.0)
    out = f" {style.paint(Tone.DIM, '├─')} {leader(style, 'Planning', fmt_secs(total))}"
    if load is not None and infer is not None:
        split = f"model load {fmt_secs(load)} · inference {fmt_secs(infer)}"
        out += f"\n {style.paint(Tone.DIM, '│')}    {style.paint(Tone.DIM, split)}"
    return out + f"\n {style.paint(Tone.DIM, '│')}"


@dataclass(frozen=True)
class PlanRow:
    """One row of the plan summary: the tool and what it works on."""

    tool: str
    detail: str


def render_plan(style: Style, rows: Sequence[PlanRow], notes: Sequence[str]) -> str:
    """The plan summary: its steps, then *notes* (a batch's "the same for N more files" and what
    the plan leaves)."""
    bar = style.paint(Tone.DIM, "│")
    noun = "step" if len(rows) == 1 else "steps"
    out = f" {style.paint(Tone.DIM, '├─')} {style.paint(Tone.BOLD, f'Plan · {len(rows)} {noun}')}"
    width = max((len(r.tool) for r in rows), default=0)
    for i, row in enumerate(rows):
        branch = "└─" if i + 1 == len(rows) else "├─"
        out += (
            f"\n {bar}   {style.paint(Tone.DIM, branch)} {i + 1}  {row.tool.ljust(width)}  "
            f"{style.paint(Tone.DIM, row.detail)}"
        )
    for note in notes:
        out += f"\n {bar}   {style.paint(Tone.DIM, note)}"
    return out + f"\n {bar}"


def render_step_head(style: Style, idx: int, total: int, tool: str) -> str:
    return (
        f" {style.paint(Tone.DIM, '├─')} {style.paint(Tone.BOLD, f'Step {idx}/{total} · {tool}')}"
    )


def render_detail(style: Style, text: str) -> str:
    """A line inside a step, under the vertical rail: the command, a summary, a note."""
    return f" {style.paint(Tone.DIM, '│')}   {text}"


def render_command(style: Style, command: str) -> str:
    """The command a step runs, as a shell line."""
    return render_detail(style, f"{style.paint(Tone.DIM, '$')} {command}")


def render_ok(style: Style, what: str, took: float | None) -> str:
    body = f"{style.paint(Tone.OK, '✓')} {what}"
    line = body if took is None else _leader_painted(style, body, 2 + len(what), fmt_secs(took))
    return f" {style.paint(Tone.DIM, '│')}   {style.paint(Tone.DIM, '└─')} {line}"


def render_fail(style: Style, what: str, cause: str | None) -> str:
    out = (
        f" {style.paint(Tone.DIM, '│')}   {style.paint(Tone.DIM, '└─')} "
        f"{style.paint(Tone.FAIL, '✗')} {what}"
    )
    if cause is not None:
        out += f"\n {style.paint(Tone.DIM, '│')}      {cause}"
    return out


def render_closing(style: Style, kind: str, message: str, total: float | None) -> str:
    """How a run that stopped at a question (``clarify``), a refusal (``reject``) or with
    nothing to do (``nothing``) ends."""
    mark, label = {
        "clarify": (style.paint(Tone.WARN, "?"), "I need more detail:"),
        "reject": (style.paint(Tone.FAIL, "⊘"), "I won't do that:"),
        "nothing": (style.paint(Tone.OK, "✓"), "Nothing to do."),
    }[kind]
    text = label if kind == "nothing" else f"{label} {message}"
    tail = "" if total is None else f"  {style.paint(Tone.DIM, f'({fmt_secs(total)})')}"
    return f" {style.paint(Tone.DIM, '└─')} {mark} {text}{tail}"


def render_done(style: Style, summary: str, total: float) -> str:
    """The last line of a run that completed: what it did and how long it took, waits
    excluded."""
    return (
        f" {style.paint(Tone.DIM, '│')}\n {style.paint(Tone.DIM, '└─')} "
        f"{style.paint(Tone.OK, 'Done')} · {summary} "
        f"{style.paint(Tone.DIM, f'· total {fmt_secs(total)}')}"
    )


def render_declined(style: Style) -> str:
    """A step whose confirmation the user answered no: the step's own closing line."""
    return (
        f" {style.paint(Tone.DIM, '│')}   {style.paint(Tone.DIM, '└─')} "
        f"{style.paint(Tone.WARN, '⊘')} Declined · no changes made"
    )


def render_declined_close(style: Style, step: int, steps: int, summary: str, total: float) -> str:
    """The last line of a run the user stopped at a confirmation. Not ``Done``: the request was
    not carried out, and a chain says how far it got."""
    at = f"Stopped at step {step} of {steps}" if steps > 1 else "Stopped"
    return (
        f" {style.paint(Tone.DIM, '│')}\n {style.paint(Tone.DIM, '└─')} "
        f"{style.paint(Tone.WARN, at)} · you declined · {summary} "
        f"{style.paint(Tone.DIM, f'· total {fmt_secs(total)}')}"
    )


def render_stopped(style: Style, message: str, detail: Sequence[str], total: float) -> str:
    """The last line of a run that stopped on an error."""
    out = (
        f" {style.paint(Tone.DIM, '│')}\n {style.paint(Tone.DIM, '└─')} "
        f"{style.paint(Tone.FAIL, '✗')} {message} "
        f"{style.paint(Tone.DIM, f'· total {fmt_secs(total)}')}"
    )
    for line in detail:
        out += f"\n      {style.paint(Tone.DIM, line)}"
    return out


def _wrap(text: str, width: int) -> list[str]:
    """Word-wrap *text* to *width* columns."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = ""
        line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    return lines


def render_box(style: Style, tone: Tone, title: str, text: str) -> str:
    """A framed notice: a warning or a tip that is about the machine, not the request."""
    inner = 74
    head_fill = "─" * max(inner - (len(title) + 3), 0)
    if style.color:
        # The bold title carries color codes inside the edge color, so repaint the edge after it.
        out = (
            f" {style.paint(tone, '╭─')} {style.paint(Tone.BOLD, title)} "
            f"{style.paint(tone, f'{head_fill}╮')}"
        )
    else:
        out = f" ╭─ {title} {head_fill}╮"
    for line in _wrap(text, inner - 2):
        pad = " " * (inner - 2 - len(line))
        out += f"\n {style.paint(tone, '│')} {line}{pad} {style.paint(tone, '│')}"
    return out + f"\n {style.paint(tone, '╰' + '─' * inner + '╯')}"


def fmt_clock(seconds: float) -> str:
    """``0:05``, ``12:34``, ``1:02:03``: a clock reading for media time."""
    total = _round(max(seconds, 0.0))
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h}:{m:02}:{s:02}" if h > 0 else f"{m}:{s:02}"


FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def render_progress(
    style: Style,
    frame: int,
    elapsed: float,
    done_seconds: float,
    total_seconds: float | None,
    speed: float | None,
) -> str:
    """The live line while ffmpeg runs: a spinner, media time done (of the total when known,
    with a bar and percentage), the encoding speed and the wall time."""
    spin = style.paint(Tone.ACCENT, FRAMES[frame % len(FRAMES)])
    parts = [f"{spin} {fmt_clock(done_seconds)}"]
    if total_seconds is not None and total_seconds > 0:
        ratio = min(max(done_seconds / total_seconds, 0.0), 1.0)
        filled = _round(ratio * 20)
        bar = "█" * filled + "░" * (20 - filled)
        parts[0] = (
            f"{spin} {fmt_clock(done_seconds)} / {fmt_clock(total_seconds)}  "
            f"{style.paint(Tone.ACCENT, bar)}  {ratio * 100:.0f}%"
        )
    if speed is not None:
        parts.append(f"{speed:.1f}x")
    parts.append(fmt_secs(elapsed))
    joined = style.paint(Tone.DIM, " · ").join(parts)
    return f" {style.paint(Tone.DIM, '│')}   {style.paint(Tone.DIM, '└─')} {joined}"


def fmt_bytes(n: int) -> str:
    """``812 B``, ``3.2 KB``, ``12.5 MB``: one decimal above a kilobyte."""
    kb = 1024.0
    if n < kb:
        return f"{n} B"
    if n < kb * kb:
        return f"{n / kb:.1f} KB"
    if n < kb * kb * kb:
        return f"{n / (kb * kb):.1f} MB"
    return f"{n / (kb * kb * kb):.1f} GB"


def files_written(n: int) -> str:
    if n == 0:
        return "no files written"
    return "1 file written" if n == 1 else f"{n} files written"


def shell_join(argv: Sequence[str]) -> str:
    """A command line as native prints it (``apps/cli`` ``shell_join``): a word that is empty or
    holds whitespace, a quote or a backslash is double-quoted, its backslashes and quotes
    escaped."""

    def quote(word: str) -> str:
        if not word or any(c.isspace() or c in "\"'\\" for c in word):
            return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return word

    return " ".join(quote(w) for w in argv)
