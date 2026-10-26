"""One display for both CLIs (owner, 2026-10-23): what `run` prints, end to end.

`contracts/parity/cli_output_cases.json`. holds a canned model output per case and what native's
`knaif run` printed for it (apps/cli/tests/cli_output_parity.rs records them); `knaif-cli run`
must print the same, byte for byte, in the same empty folder with the output piped.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from knaif.app import cli

CASES = json.loads(
    (Path(__file__).parents[3] / "contracts" / "parity" / "cli_output_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


#: Every timing (`1.842 s`, the batch summary's `0.1s`) as `<t> s`, as the Rust test masks.
_TIMING = re.compile(r"(?<!\d)\d+\.\d+ ?s(?![^\W_])")


def _masked(text: str) -> str:
    # Line ends as `\n`: a generated text fixture has the platform's.
    return _TIMING.sub("<t> s", text.replace("\r\n", "\n"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_cli_output(case: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in case.get("files", []):
        (tmp_path / name).write_bytes(b"")
    fixtures = Path(__file__).parents[3] / "sandbox" / "fixtures" / case["skill"]
    for name in case.get("fixtures", []):
        if not (fixtures / name).is_file():
            pytest.skip(f"fixture {name} missing (regenerate the {case['skill']} fixtures)")
        shutil.copy(fixtures / name, tmp_path / name)
    if case.get("fixtures") and "--dry-run" not in case["flags"] and case["skill"] == "ffmpeg":
        pytest.skip("runs ffmpeg: skills/ffmpeg/python/tests/test_cli_output_executing.py")
    env = {
        "KNAIF_LLM_BACKEND": "mock",
        "KNAIF_LLM_MOCK_RESPONSE": json.dumps(case["plan"]),
        "KNAIF_DUMP_PLAN": None,
        "KNAIF_DEBUG": None,
        "KNAIF_VIEW": "rich" if case.get("view") == "rich" else None,
        "NO_COLOR": "1" if case.get("view") == "rich" else None,
    }
    argv = ["run", case["skill"], *case["flags"], case["utterance"]]
    result = CliRunner().invoke(cli, argv, env=env)
    assert _masked(result.stdout) == case["stdout"]
    if "stderr_prefix" in case:
        assert result.stderr.startswith(case["stderr_prefix"]), result.stderr
    else:
        assert _masked(result.stderr) == case["stderr"]
    assert (result.exit_code == 0) == case["ok"], result.output
    if "files_after" in case:
        assert sorted(f.name for f in tmp_path.iterdir()) == case["files_after"]
