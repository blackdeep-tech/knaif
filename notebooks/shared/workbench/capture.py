"""Catch llama.cpp's own output, which never passes through Python's `sys.stderr`.

`Llama(verbose=True)` prints its load trace from C, straight to file descriptor 2. A
`contextlib.redirect_stderr` sees none of it, so the only way to read where the layers landed is
to redirect the descriptor itself.

This lives beside its one caller rather than in core: it is a notebook concern, and the same
parser it feeds (`native_lane.parse_tensor_placement`) is already shared.

See docs/plans/2026-09-21-skill-prompt-workbench.md (D2b).
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from collections.abc import Iterator


@contextlib.contextmanager
def capture_fd2() -> Iterator[list[str]]:
    """Redirect file descriptor 2 to a temp file for the duration of the block.

    Yields a one-element list that holds the captured text once the block exits. The original
    descriptor is always restored, including on an exception — losing a notebook's stderr would
    be a far worse bug than missing a placement reading.

    On a platform or process where `dup` is unavailable the block still runs and the capture is
    simply empty: an unanswered question, never a guess.
    """
    out: list[str] = []
    try:
        saved = os.dup(2)
    except (OSError, AttributeError):
        yield out
        return

    tmp = tempfile.TemporaryFile(mode="w+b")
    try:
        sys.stderr.flush()
        os.dup2(tmp.fileno(), 2)
        try:
            yield out
        finally:
            sys.stderr.flush()
            os.dup2(saved, 2)
            tmp.seek(0)
            out.append(tmp.read().decode("utf-8", errors="replace"))
    finally:
        os.close(saved)
        tmp.close()
