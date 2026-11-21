from __future__ import annotations

import json
from pathlib import Path

from knaif.skill import Skill

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]


def test_documents_skill_exposes_artifact_runner():
    skill = Skill.load(DOCUMENTS_SKILL_DIR)

    assert callable(skill.artifact_runner)


def test_artifact_runner_executes_json_plan_against_fixture(tmp_path: Path):
    fixture = tmp_path / "sample.txt"
    fixture.write_text("Artifact Alpha\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    plan = {
        "plan": [
            {
                "tool": "convert_document",
                "args": {"input": "{fixture}", "to_format": "md", "output": "artifact.md"},
            }
        ]
    }
    skill = Skill.load(DOCUMENTS_SKILL_DIR)

    result = skill.artifact_runner(json.dumps(plan), fixture, out_dir)

    assert result == out_dir / "artifact.md"
    assert "Artifact Alpha" in result.read_text(encoding="utf-8")


def test_artifact_runner_leaves_a_known_folder_output_to_the_known_folders(
    tmp_path: Path, monkeypatch
):
    """`Documents/sample.md` names the user's Documents folder, not one inside the row folder:
    the replay keeps it relative so the pipeline's rewrite sends it where the known folders point
    (in the eval, the row's `<row>.known/`)."""
    from knaif.known_folders import ROOT_ENV

    known = tmp_path / "row.known"
    monkeypatch.setenv(ROOT_ENV, str(known))
    fixture = tmp_path / "sample.txt"
    fixture.write_text("Artifact Alpha\n", encoding="utf-8")
    out_dir = tmp_path / "row"
    plan = {
        "plan": [
            {
                "tool": "convert_document",
                "args": {"input": "{fixture}", "to_format": "md", "output": "Documents/sample.md"},
            }
        ]
    }
    skill = Skill.load(DOCUMENTS_SKILL_DIR)

    result = skill.artifact_runner(json.dumps(plan), fixture, out_dir)

    assert result == known / "Documents" / "sample.md"
    assert "Artifact Alpha" in result.read_text(encoding="utf-8")
    assert not (out_dir / "Documents").exists()
