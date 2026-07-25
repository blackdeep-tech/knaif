"""Execute a sequence of reference/produced ffmpeg commands as a dependent chain.

Shared by the eval runner (grading a model's multi-intent plan) and the notebook
baseline reviewer (validating a multi-output row's reference commands). Kept free
of UI / notebook dependencies so it can run inside the eval runtime.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def run_command_chain(
    commands: list[str],
    fixture_dir: Path | str,
    out_dir: Path | str,
    *,
    timeout: int = 120,
    fixture_file: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Run ``commands`` in sequence inside ``out_dir``.

    Each command's ``-i <name>`` is rewritten to ``out_dir/<name>`` when a prior
    command already produced that file there, otherwise to ``fixture_dir/<name>``
    (resolution is by basename, so absolute paths in the command are fine). Each
    command's output (last token) is rewritten into ``out_dir``. The chain halts
    on the first non-zero exit, since later commands depend on earlier outputs.

    ``fixture_file`` is used as a fallback when a ``-i`` argument resolves to
    neither a prior output nor an existing fixture — handles placeholder names
    like ``current_file`` in AI-seeded baseline commands.

    Returns one dict per executed command:
    ``{command, returncode, stderr, output}``.
    """
    fixture_dir = Path(fixture_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if fixture_file is not None:
        fixture_file = Path(fixture_file)
    results: list[dict[str, Any]] = []

    for command in commands:
        toks = command.split()
        for i, tok in enumerate(toks):
            if tok == "-i" and i + 1 < len(toks):
                name = Path(toks[i + 1]).name
                prior = out_dir / name
                in_fixture = fixture_dir / name
                if prior.exists():
                    toks[i + 1] = str(prior)
                elif in_fixture.exists():
                    toks[i + 1] = str(in_fixture)
                elif fixture_file is not None:
                    toks[i + 1] = str(fixture_file)
                else:
                    toks[i + 1] = str(in_fixture)
        output = out_dir / Path(toks[-1]).name
        toks[-1] = str(output)

        try:
            proc = subprocess.run(toks, capture_output=True, text=True, timeout=timeout)
            returncode, stderr = proc.returncode, proc.stderr
        except FileNotFoundError:
            returncode, stderr = 127, "command not found on PATH"
        except subprocess.TimeoutExpired:
            returncode, stderr = 124, f"timeout after {timeout}s"

        results.append(
            {"command": command, "returncode": returncode, "stderr": stderr, "output": output}
        )
        if returncode != 0:
            break

    return results
