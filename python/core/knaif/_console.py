"""Windows console encoding guard for the CLI entry points.

Python on Windows picks stdio encodings from the active code page, which is
cp1252 on a default install. Every knaif CLI prints non-ASCII — box-drawing
borders in eval tables, `→` in plan previews, `✓`/`✗` verdicts — and cp1252
cannot encode any of them, so the process dies with:

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'

`PYTHONUTF8=1` fixes it, but that puts the burden on every caller (justfile
recipes, CI, a user's own shell). Reconfiguring the streams at startup fixes it
once, for however the CLI is invoked.

`errors="replace"` is a deliberate belt-and-braces: on the rare console that
still cannot render a glyph we want a `?`, never a traceback that loses the
result the user was waiting for.
"""

from __future__ import annotations

import io
import sys


def enable_utf8_console() -> None:
    """Force sys.stdout/sys.stderr to UTF-8. No-op off Windows."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        # `reconfigure` is TextIOWrapper-only. Anything else here is a substitute stream
        # someone else owns (pytest capture, a StringIO in a test) — leave it alone.
        if not isinstance(stream, io.TextIOWrapper):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass  # detached buffer — nothing to reconfigure
