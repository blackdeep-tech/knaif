"""The native fingerprint split for the result store (plan 2026-10-14-faster-release-evidence, A3,
T2, as amended by the owner 2026-10-19).

A stored plan is the model's raw reply, keyed by the exact prompt text plus the **inference**
fingerprint (the code and settings between that prompt and the reply), the model and the backend.
A stored outcome is keyed by the plan plus the skill's **execution** fingerprint: everything that
turns a raw reply into files, which with the reply replayed through the mock is all the native
code except the inference stack. Execution is per skill, not per tool (owner, 2026-10-19).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from knaif.evalsuite.fingerprints import (
    execution_fingerprint,
    inference_fingerprint,
    unclassified_native_files,
)

LOCK = """[[package]]
name = "llama-cpp-2"
version = "0.1.100"

[[package]]
name = "lopdf"
version = "0.34.0"
"""

FILES = {
    "native/crates/knaif-core/src/prompt.rs": "prompt",
    "native/crates/knaif-core/src/planner.rs": "planner",
    "native/crates/knaif-llm/src/llama.rs": "llama",
    "native/crates/knaif-models/src/store.rs": "store",
    "native/crates/knaif-skill-api/src/sandbox.rs": "sandbox",
    "apps/cli/src/main.rs": "main",
    "Cargo.toml": '[workspace.package]\nversion = "1.3.0"',
    "contracts/runtime/generation.yaml": "temperature: 0",
    "contracts/runtime/core_tools.yaml": "tools: []",
    "Cargo.lock": LOCK,
    "skills/a/skill.yaml": "name: a",
    "skills/a/tools.yaml": "tools: []",
    "skills/a/prompt.yaml": "system_header: a",
    "skills/a/profiles/x.yaml": "crf: 23",
    "skills/a/native/src/engine.rs": "engine a",
    "skills/a/data/eval.jsonl": "{}",
    "skills/b/skill.yaml": "name: b",
    "skills/b/native/src/run.rs": "run b",
}


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    for rel, text in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _prints(root: Path) -> dict[str, str]:
    return {
        "inference": inference_fingerprint(root),
        "exec:a": execution_fingerprint("a", root),
        "exec:b": execution_fingerprint("b", root),
    }


def _moved(root: Path, rel: str, text: str) -> set[str]:
    before = _prints(root)
    (root / rel).write_text(text, encoding="utf-8")
    after = _prints(root)
    return {k for k in before if before[k] != after[k]}


def test_the_inference_code_moves_inference_only(tree: Path) -> None:
    assert _moved(tree, "native/crates/knaif-llm/src/llama.rs", "x") == {"inference"}


def test_model_and_backend_resolution_moves_inference(tree: Path) -> None:
    assert _moved(tree, "native/crates/knaif-models/src/store.rs", "x") == {"inference"}


def test_generation_settings_move_inference(tree: Path) -> None:
    assert "inference" in _moved(tree, "contracts/runtime/generation.yaml", "temperature: 1")


def test_a_llama_cpp_bump_moves_inference(tree: Path) -> None:
    assert "inference" in _moved(tree, "Cargo.lock", LOCK.replace("0.1.100", "0.1.101"))


def test_another_dependency_bump_keeps_inference(tree: Path) -> None:
    moved = _moved(tree, "Cargo.lock", LOCK.replace("0.34.0", "0.35.0"))
    assert moved == {"exec:a", "exec:b"}


def test_a_version_bump_keeps_inference(tree: Path) -> None:
    """What made a file-level planning key useless: the version lives in a planning file."""
    moved = _moved(tree, "Cargo.toml", '[workspace.package]\nversion = "1.4.0"')
    assert "inference" not in moved


def test_prompt_code_moves_execution_not_inference(tree: Path) -> None:
    """The prompt text is in the plan key itself; its code only counts where it runs again."""
    moved = _moved(tree, "native/crates/knaif-core/src/prompt.rs", "x")
    assert moved == {"exec:a", "exec:b"}


def test_validation_code_moves_every_skill_s_execution(tree: Path) -> None:
    moved = _moved(tree, "native/crates/knaif-core/src/planner.rs", "x")
    assert moved == {"exec:a", "exec:b"}


def test_one_skill_s_native_code_moves_only_its_execution(tree: Path) -> None:
    assert _moved(tree, "skills/a/native/src/engine.rs", "x") == {"exec:a"}


def test_one_skill_s_bundle_moves_only_its_execution(tree: Path) -> None:
    assert _moved(tree, "skills/a/profiles/x.yaml", "crf: 28") == {"exec:a"}
    assert _moved(tree, "skills/a/tools.yaml", "tools: [x]") == {"exec:a"}


def test_corpus_data_moves_nothing(tree: Path) -> None:
    assert _moved(tree, "skills/a/data/eval.jsonl", '{"id": 1}') == set()


def test_the_acceptance_bar_moves_nothing(tree: Path) -> None:
    """It grades runs; no run reads it (found writing the hard2 floor, 2026-10-18)."""
    assert _moved(tree, "skills/a/acceptance.yaml", "slices: {}") == set()


def test_line_endings_move_nothing(tree: Path) -> None:
    path = tree / "skills/a/native/src/engine.rs"
    path.write_bytes(b"line one\nline two\n")
    before = _prints(tree)
    path.write_bytes(b"line one\r\nline two\r\n")
    assert _prints(tree) == before


def test_every_native_source_file_in_the_repository_is_classified() -> None:
    """A new native file must land in inference or execution, or it would move neither."""
    assert unclassified_native_files(Path(".")) == []


def test_the_cli_prints_both_fingerprints(tree: Path, monkeypatch, capsys) -> None:
    from knaif.evalsuite import cli

    monkeypatch.chdir(tree)
    cli.cmd_fingerprints(argparse.Namespace(skill=["a", "b"]))
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "inference": inference_fingerprint(tree),
        "execution": {s: execution_fingerprint(s, tree) for s in ("a", "b")},
    }
