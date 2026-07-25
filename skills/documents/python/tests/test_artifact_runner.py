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
