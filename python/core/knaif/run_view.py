"""What ``knaif-cli run`` shows: native ``knaif run``'s two views (owner, 2026-10-23).

**Two views, one rule** (``apps/cli/src/ui.rs``). When stdout *and* stderr are terminals the run
is drawn as a tree (:mod:`knaif.ui`'s renderers); anything else — a pipe, a redirect, the eval
lane — gets native's plain lines. ``KNAIF_VIEW=rich|plain`` overrides the choice, ``NO_COLOR``
drops the colors. Both CLIs are held to ``contracts/parity/cli_output_cases.json``.

Domain-agnostic: what a step did comes from the skill's ``format_results`` items; this module
only places them.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

import click

from . import ui
from .batch_chain import expand_inputs
from .scratch import display

# ── which view ────────────────────────────────────────────────────────────────────────────────


def _msys_pty_name(name: str) -> bool:
    """Whether a pipe of this name is an MSYS or Cygwin pty (Git Bash, mintty): its file name
    starts ``msys-`` or ``cygwin-`` and holds ``-pty``. Rust's `IsTerminal` (`msys_tty_on`)."""
    base = name.rsplit("\\", 1)[-1]
    return base.startswith(("msys-", "cygwin-")) and "-pty" in base


def _is_terminal(stream: Any) -> bool:
    """Whether *stream* is a terminal, as native (Rust's `IsTerminal`) decides. On Windows a
    console, not just a character device: the NUL device answers ``isatty()`` too;
    or an MSYS pty, which is a named pipe: Git Bash's terminal (2026-10-24).
    Through `knaif-cli`'s stderr guard (`app._console_safe_stderr`): the terminal is the stream
    behind it."""
    stream = getattr(stream, "knaif_real", stream)
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return False
    return _fd_is_terminal(fd)


def _fd_is_terminal(fd: int) -> bool:
    """Whether file descriptor *fd* is a terminal (see `_is_terminal`)."""
    if sys.platform != "win32":
        return os.isatty(fd)
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
        if kernel32.GetConsoleMode(handle, ctypes.byref(wintypes.DWORD())):
            return True
        kernel32.GetFileType.argtypes = [wintypes.HANDLE]
        kernel32.GetFileType.restype = wintypes.DWORD
        if kernel32.GetFileType(handle) != 3:  # FILE_TYPE_PIPE
            return False

        class FileNameInfo(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]

        info = FileNameInfo()
        kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            2,
            ctypes.byref(info),
            ctypes.sizeof(info),  # 2: FileNameInfo
        ):
            return False
        return _msys_pty_name(info.name[: info.length // 2])
    except (OSError, AttributeError, ValueError):
        return False


def _enable_vt() -> bool:
    """Turn on ANSI processing for the console's stdout and stderr; whether both took it. Off a
    console (a pipe) there is nothing to turn on, so colors stay off, as native's."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        ok = True
        for which in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(which)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ok = False
                continue
            if not mode.value & 0x0004 and not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                ok = False
        return ok
    except (OSError, AttributeError):
        return False


def choose_style() -> ui.Style | None:
    """``Style`` when this run draws the tree, ``None`` for the plain view (native ``ui::init``)."""
    forced = os.environ.get("KNAIF_VIEW")
    if forced == "plain":
        return None
    if forced != "rich":
        terminal = _is_terminal(sys.stdout) and _is_terminal(sys.stderr)
        if not terminal or os.environ.get("TERM") == "dumb":
            return None
    return ui.Style(color=_enable_vt() and not os.environ.get("NO_COLOR"))


class QuietPlanning:
    """While the model loads and plans: the process's stderr (fd 2) pointed at the null device,
    so llama.cpp and the GPU libraries print nothing, in either view (native turns their log
    off), and in the tree a ``⠋ Planning… 1.234 s`` line on a private copy of stderr (native
    ``Spinner`` + ``StderrGuard``). Off (a no-op) when *active* is false: ``--verbose``, a debug
    dump."""

    def __init__(self, style: ui.Style | None, *, active: bool) -> None:
        self.style = style
        # Quiet in the plain view too, without the spinner: native turns llama.cpp's log off in
        # every view (`send_logs_to_tracing`), and the plain view let `llama_context: …` through.
        self.active = active
        self._saved: int | None = None
        self._stop = False
        self._thread: Any = None

    def __enter__(self) -> QuietPlanning:
        if not self.active:
            return self
        import threading

        try:
            sys.stderr.flush()
            self._saved = os.dup(2)
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, 2)
            os.close(null)
        except OSError:
            self._saved = None
            return self
        style, out = self.style, self._saved
        if style is None:
            return self

        def spin() -> None:
            started, frame = time.perf_counter(), 0
            while not self._stop:
                line = (
                    f"\r {style.paint(ui.Tone.ACCENT, ui.FRAMES[frame % len(ui.FRAMES)])} "
                    f"Planning… {style.paint(ui.Tone.DIM, ui.fmt_secs(time.perf_counter() - started))}"
                )
                os.write(out, line.encode("utf-8"))
                frame += 1
                time.sleep(0.1)
            os.write(out, b"\r\x1b[2K")

        self._thread = threading.Thread(target=spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._saved is None:
            return
        self._stop = True
        if self._thread is not None:
            self._thread.join()
        os.dup2(self._saved, 2)
        os.close(self._saved)
        self._saved = None


# ── what a person reads ───────────────────────────────────────────────────────────────────────


def shown(path: str, base: Path) -> str:
    """A path as native prints it: a run scratch path is ``<work>/…``, one under *base* (the
    working folder) relative to it, any other as given with ``/`` (a known folder's
    output). Never the user's home through an absolute path."""
    text = display(path)
    if text.startswith("<work>"):
        return text
    p = Path(path)
    if not p.is_absolute():
        # `/`-separated, as native prints it: a path Python built (`out\\silent.mp4` on Windows)
        # read with two separators.
        return p.as_posix()
    try:
        return p.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def shown_argv(argv: Sequence[str], base: Path) -> str:
    """A command line as native prints it: each path relative (``/``-separated, the plan's own
    spelling) or ``<work>/…``, quoted as its ``shell_join``."""
    tokens = []
    for token in argv:
        text = shown(token, base)
        if text != token and not text.startswith("<work>"):
            text = Path(text).as_posix()
        tokens.append(text)
    return ui.shell_join(tokens)


def downloaded_name(path: str) -> str:
    """A downloaded file as a person should read it: its name and where it came from, never the
    run scratch it sits in (owner, 2026-10-23; native ``downloaded_name``)."""
    return f"{Path(path).name} (downloaded)"


def step_detail(
    args: Mapping[str, Any],
    base: Path,
    kinds: Mapping[str, str] | None,
    downloads: Collection[str] = (),
) -> str:
    """What a step works on, for the plan's rows: ``clip.mp4 → out.mp4``. A glob or folder among
    the inputs shows the files it finds (of the skill's kinds); when none finds a file the
    plan's own words stay (native ``step_detail``). A downloaded file is named by
    :func:`downloaded_name`."""

    def names(value: Any) -> list[str]:
        if isinstance(value, str):
            return [downloaded_name(value) if value in downloads else display(value)]
        if isinstance(value, list):
            return [n for v in value for n in names(v)]
        return []

    def brief(found: list[str]) -> str | None:
        if not found:
            return None
        return found[0] if len(found) == 1 else f"{found[0]} (+{len(found) - 1} more)"

    written = next(
        (n for k in ("inputs", "input", "files", "pdf", "paths") if (n := names(args.get(k)))),
        [],
    )
    found: list[str] = []
    if written:
        expanded = expand_inputs(written, base, kinds)
        for name in written:
            for file in expanded.get(name, [name]):
                file = file[2:] if file.startswith("./") else file
                if file not in found:
                    found.append(file)
    inputs = brief(found or written)
    output = brief(names(args.get("output")))
    if inputs and output:
        return f"{inputs} → {output}"
    if inputs:
        return inputs
    return f"→ {output}" if output else ""


def chain_failure_context(idx: int, total: int) -> str:
    """Which step failed, what had already run and what did not (native
    ``chain_failure_context``): a bare cause after a chain leaves the user guessing what reached
    their disk."""
    ordinal = idx + 1
    if total == 1:
        return "the step failed"
    completed = (
        "nothing had run yet"
        if idx == 0
        else "step 1 had already completed" if idx == 1 else f"steps 1-{idx} had already completed"
    )
    left = total - ordinal
    skipped = (
        "it was the last step"
        if left == 0
        else (
            f"step {total} was not run"
            if left == 1
            else f"steps {ordinal + 1}-{total} were not run"
        )
    )
    return f"step {ordinal} of {total} failed; {completed}, and {skipped}"


def _control(step: Mapping[str, Any]) -> tuple[str, str]:
    """``(kind, message)`` of a question, a refusal or nothing to do."""
    tool = step.get("tool")
    if tool == "done":
        return "nothing", ""
    args = step.get("args") or {}
    res = step.get("result") or {}
    if tool == "clarify":
        return "clarify", str(args.get("question") or res.get("question") or "(no question)")
    return "reject", str(args.get("reason") or res.get("reason") or "(no reason)")


# ── one run ───────────────────────────────────────────────────────────────────────────────────


class RunView:
    """The lines of one ``run``, in native's plain view or its tree (*style*)."""

    def __init__(
        self,
        skill: str,
        style: ui.Style | None,
        *,
        base: Path,
        file_kinds: Mapping[str, str] | None = None,
        silent: bool = False,
    ) -> None:
        self.skill = skill
        self.style = style
        self.base = base
        self.file_kinds = file_kinds
        self.silent = silent
        self._start = time.perf_counter()
        self._waited = 0.0
        self._header = False
        self._closed = False
        self._written = 0
        self._declined: tuple[int, int] | None = None
        #: The run's downloaded files (their scratch paths), named in the plan's rows.
        self.downloads: set[str] = set()
        #: ``run --stdout``: a result's text goes to stdout alone, so the view does not repeat it.
        self.text_to_stdout = False
        #: A batch's failed count and size for the closing line (`` · 1 failed · 7.6 MB``): the
        #: tree says them there, not on a summary line of their own (owner, 2026-10-25).
        self.close_extra = ""

    # the clock: wall time, waits for the user left out
    def total(self) -> float:
        return time.perf_counter() - self._start - self._waited

    def waited(self, seconds: float) -> None:
        self._waited += seconds

    def _out(self, text: str, *, err: bool = False) -> None:
        if not self.silent:
            click.echo(text, err=err)

    def header(self, model: str | None = None) -> None:
        """The tree's first line, once: ``knaif · <skill> · <model>``."""
        if self.style is not None and not self._header:
            self._header = True
            self._out(ui.render_header(self.style, self.skill, model))

    def planning(self, load_seconds: float | None, infer_seconds: float | None) -> None:
        """How long planning took, when a model ran (the mock measures nothing)."""
        if self.style is not None and infer_seconds is not None:
            self._out(ui.render_planning(self.style, load_seconds, infer_seconds))

    def plan(self, steps: Sequence[Mapping[str, Any]], notes: Sequence[str]) -> None:
        """The plan's rows and notes (the tree only; the plain view shows no plan)."""
        if self.style is None:
            return
        rows = [
            ui.PlanRow(
                str(s.get("tool", "?")),
                step_detail(s.get("args") or {}, self.base, self.file_kinds, self.downloads),
            )
            for s in steps
        ]
        self._out(ui.render_plan(self.style, rows, notes))

    def step(self, index: int, total: int, step: Mapping[str, Any]) -> None:
        """A step's head: ``Step i/N · tool``; plain, ``step i of N:`` for a chain only."""
        if self.style is not None:
            self._out(ui.render_step_head(self.style, index, total, str(step.get("tool", "?"))))
        elif total > 1:
            self._out(f"step {index} of {total}:")

    def item(self, item: Mapping[str, Any], *, dry_run: bool) -> int:
        """One result item; 1 for an error, else 0.

        ``command``: the command line (a dry run) or ``running: …`` on stderr; ``preview``:
        ``would <summary> →`` and its files; ``output``: ``✓ <file>``; ``text``: as is;
        ``note``/``info``: a note; ``error``: ``✗ …``."""
        kind = item.get("kind", "info")
        message = str(item.get("message", ""))
        style = self.style
        if kind == "command":
            line = shown_argv(item["argv"], self.base) if item.get("argv") else display(message)
            if style is not None:
                self._out(ui.render_command(style, line))
            elif dry_run:
                self._out(line)
            else:
                self._out(f"running: {line}", err=True)
        elif kind == "preview":
            outputs = [str(o) for o in item.get("outputs") or []]
            if style is not None:
                self._out(ui.render_detail(style, f"would {message}"))
                for out in outputs:
                    self._out(ui.render_detail(style, f"→ {shown(out, self.base)}"))
                    if Path(out).exists():
                        warn = style.paint(ui.Tone.WARN, "⚠ that file already exists")
                        self._out(ui.render_detail(style, warn))
            else:
                self._out(f"would {message} →")
                for out in outputs:
                    self._out(f"  {shown(out, self.base)}")
        elif kind == "output":
            self._written += 1
            if style is not None:
                self._out(ui.render_ok(style, shown(message, self.base), item.get("seconds")))
            else:
                self._out(f"✓ {shown(message, self.base)}")
            self._size_change(item)
        elif kind == "kept":
            # A compress that could not make its file smaller: not a success, not a failure.
            written = bool(item.get("written"))
            self._written += written
            what = ui.kept_what(shown(message, self.base), written)
            self._out(ui.render_kept(style, what) if style is not None else f"⚠ {what}")
            self._size_change(item)
        elif kind == "wrote":
            # A skill's write: what it did, then each file (native's documents step).
            outputs = [shown(str(o), self.base) for o in item.get("outputs") or []]
            self._written += len(outputs)
            if style is not None:
                self._out(ui.render_detail(style, message))
                for out in outputs:
                    self._out(ui.render_detail(style, f"→ {out}"))
                for out in outputs:
                    self._out(ui.render_ok(style, out, item.get("seconds")))
            else:
                self._out(message)
                for out in outputs:
                    self._out(f"✓ wrote {out}")
        elif kind == "text" and self.text_to_stdout:
            pass
        elif kind == "text":
            if style is not None:
                for line in item.get("detail") or message.splitlines() or [""]:
                    self._out(ui.render_detail(style, line))
            else:
                self._out(message)
        elif kind == "error":
            if style is not None:
                self._out(ui.render_fail(style, display(message), None))
            else:
                self._out(f"✗ {display(message)}", err=True)
            return 1
        elif style is not None:
            self._out(ui.render_detail(style, display(message)))
        else:
            self._out(display(message), err=True)
        return 0

    def _size_change(self, item: Mapping[str, Any]) -> None:
        """What a compress did to the size, under its result (``before``/``after`` in bytes)."""
        if item.get("before") is None or item.get("after") is None:
            return
        text = ui.size_change(int(item["before"]), int(item["after"]))
        self._out(ui.render_under(self.style, text) if self.style is not None else f"  {text}")

    def exists(self, path: str) -> None:
        """``⚠ <file> already exists`` under the step that would replace it, before the question
        (the tree only, as native)."""
        if self.style is not None:
            warn = self.style.paint(ui.Tone.WARN, f"⚠ {path} already exists")
            self._out(ui.render_detail(self.style, warn))

    def control(self, step: Mapping[str, Any]) -> None:
        """A question, a refusal or nothing to do: the run's last line."""
        kind, message = _control(step)
        if self.style is not None:
            self.header()
            self._out(ui.render_closing(self.style, kind, message, self.total()))
            self._closed = True
        elif kind == "nothing":
            self._out("Nothing to do.")
        else:
            self._out(f"{kind}: {message}")

    def declined(self, step: int = 1, steps: int = 1) -> None:
        """The user said no: the step's closing line (tree) or native's plain line."""
        if self.style is not None:
            self._out(ui.render_declined(self.style))
            self._declined = (step, steps)
        else:
            self._out("Aborted (no changes made).")

    def finish(self, *, dry_run: bool) -> None:
        """The tree's last line (``Done``, or where a declined run stopped) and the empty line
        every command in the terminal view ends with."""
        if self.style is None:
            return
        if self._header and not self._closed:
            summary = (
                "dry run, nothing executed"
                if dry_run
                else ui.files_written(self._written) + self.close_extra
            )
            if self._declined is not None:
                step, steps = self._declined
                last = ui.render_declined_close(self.style, step, steps, summary, self.total())
            else:
                last = ui.render_done(self.style, summary, self.total())
            self._out(last)
        self._out("")

    def stopped(self, message: str, cause: str | None = None) -> None:
        """A run that stopped on an error: where (*message*) and why (*cause*), as native's error
        chain prints them (``Error: …`` / ``Caused by:``, stderr) or the tree's last line."""
        message = display(message)
        if self.style is not None and self._header:
            detail = [display(cause)] if cause else []
            self._out(ui.render_stopped(self.style, message, detail, self.total()))
            self._out("")
        elif cause:
            click.echo(f"Error: {message}\n\nCaused by:\n    {display(cause)}", err=True)
        else:
            click.echo(f"Error: {message}", err=True)
