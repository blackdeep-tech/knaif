"""With no terminal, the Python CLI never waits for an answer.

The same convert run twice with stdin closed hung on `Replace …? [y/N]` for 34 minutes. On
Windows `isatty()` is True for the NUL device, so the CLI waited for a key nobody could press;
native (Rust's `IsTerminal`) asks Windows whether stdin is a console and stopped, naming
`--overwrite`.
"""

from __future__ import annotations

import os
import sys

from knaif import app


def test_the_null_device_is_not_a_terminal(monkeypatch) -> None:
    with open(os.devnull, encoding="utf-8") as null:
        monkeypatch.setattr(sys, "stdin", null)
        assert app._stdin_is_terminal() is False


def test_a_question_with_no_terminal_gets_no_answer(monkeypatch) -> None:
    with open(os.devnull, encoding="utf-8") as null:
        monkeypatch.setattr(sys, "stdin", null)
        monkeypatch.setattr(app, "_stdin_is_terminal", lambda: False)
        assert app._ask_yes_no("Replace clip.mp4?", False) is None
