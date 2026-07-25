"""Unit tests for `evalsuite run --all-skills` orchestration.

The per-skill evaluation core (`cmd_run`) and skill enumeration (`list_skills`)
are patched so these tests exercise the sweep's validation, stale/skip handling,
and matrix building without running real corpora.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from knaif.evalsuite import cli


def _args(**overrides) -> argparse.Namespace:
    base = {
        "skill": None,
        "all_skills": True,
        "save": None,
        "corpus": None,
        "fixture_dir": None,
        "snapshot": False,
        "verifier": "cheap",
        "config": "eval_backends.yaml",
        "backends": None,
        "limit": None,
        "sandbox": None,
        "verbose": False,
        "no_retrieval": False,
        "keep_artifacts": False,
        "label": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


_SCOREBOARD = {
    "verifier": "cheap",
    "total": 2,
    "outcome_accuracy": 1.0,
    "avg_knaif_score": 0.9,
    "intent_metrics": {"tool_accuracy": 1.0, "schema_validity": 1.0},
}


def test_all_skills_requires_save():
    with pytest.raises(SystemExit):
        cli.cmd_run_all_skills(_args(save=None))


def test_all_skills_rejects_per_skill_options(tmp_path: Path):
    with pytest.raises(SystemExit):
        cli.cmd_run_all_skills(_args(save=str(tmp_path), corpus="x.jsonl"))
    with pytest.raises(SystemExit):
        cli.cmd_run_all_skills(_args(save=str(tmp_path), fixture_dir="f"))
    with pytest.raises(SystemExit):
        cli.cmd_run_all_skills(_args(save=str(tmp_path), snapshot=True))


def test_all_skills_skips_missing_corpus(tmp_path: Path, monkeypatch):
    # A skill with no in-skill corpus is skipped, not fatal, and cmd_run is never
    # invoked for it.
    monkeypatch.setattr(cli, "list_skills", lambda *a, **k: ["ghost"])

    def _boom(args):
        raise AssertionError("cmd_run should not run for a corpus-less skill")

    monkeypatch.setattr(cli, "cmd_run", _boom)

    cli.cmd_run_all_skills(_args(save=str(tmp_path)))

    matrix = json.loads((tmp_path / "matrix.json").read_text(encoding="utf-8"))
    assert matrix["skipped"] == ["ghost"]
    assert matrix["skills"] == {}


def test_all_skills_builds_matrix(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "list_skills", lambda *a, **k: ["alpha", "beta"])
    # Pretend both skills have a corpus.
    monkeypatch.setattr(cli, "_corpus_path", lambda skill: tmp_path / f"{skill}.jsonl")
    (tmp_path / "alpha.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "beta.jsonl").write_text("{}\n", encoding="utf-8")

    seen: list[str] = []

    def _fake_run(args):
        seen.append(args.skill)
        return {"mock": _SCOREBOARD}

    monkeypatch.setattr(cli, "cmd_run", _fake_run)

    cli.cmd_run_all_skills(_args(save=str(tmp_path)))

    assert seen == ["alpha", "beta"]

    matrix = json.loads((tmp_path / "matrix.json").read_text(encoding="utf-8"))
    assert matrix["verifier"] == "cheap"
    assert matrix["skipped"] == []
    row = matrix["skills"]["alpha"]["mock"]
    assert row["outcome_accuracy"] == 1.0
    assert row["avg_knaif_score"] == 0.9
    assert row["tool_accuracy"] == 1.0
    assert row["schema_validity"] == 1.0

    md = (tmp_path / "matrix.md").read_text(encoding="utf-8")
    assert "alpha" in md and "beta" in md
    assert "mock" in md

    # Build identity is embedded so trend can reconstruct per-skill history.
    meta = matrix["meta"]
    assert meta["label"] == tmp_path.name  # defaults to the save-folder name
    assert meta["backends"] == ["mock"]
    assert meta["date"]  # ISO timestamp present
    assert "git_sha" in meta


def test_run_all_skills_label_override(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "list_skills", lambda *a, **k: ["alpha"])
    monkeypatch.setattr(cli, "_corpus_path", lambda skill: tmp_path / f"{skill}.jsonl")
    (tmp_path / "alpha.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "cmd_run", lambda args: {"mock": _SCOREBOARD})

    cli.cmd_run_all_skills(_args(save=str(tmp_path), label="ft-2026q2"))

    matrix = json.loads((tmp_path / "matrix.json").read_text(encoding="utf-8"))
    assert matrix["meta"]["label"] == "ft-2026q2"


def test_run_all_skills_without_save_exits_via_cli(monkeypatch):
    """The argparse wiring routes --all-skills to the sweep, which requires --save."""
    import sys

    monkeypatch.setattr(sys, "argv", ["knaif.evalsuite", "run", "--all-skills"])
    with pytest.raises(SystemExit):
        cli.main()


# ── Phase 2: aggregate regression gate ────────────────────────────────────────


def _snapshot(outcome=0.9, knaif=0.9, tool=0.9, schema=1.0, verifier="cheap") -> dict:
    return {
        "verifier": verifier,
        "total": 10,
        "outcome_accuracy": outcome,
        "avg_knaif_score": knaif,
        "intent_metrics": {"tool_accuracy": tool, "schema_validity": schema},
    }


def _reg_args(current_run, **overrides) -> argparse.Namespace:
    base = {"current_run": str(current_run) if current_run else None, "threshold": 0.02}
    base.update(overrides)
    return argparse.Namespace(**base)


def _write_scoreboard(run_dir: Path, skill: str, backend: str, sb: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{skill}_{backend}_{sb['verifier']}.json").write_text(
        json.dumps(sb), encoding="utf-8"
    )


def _patch_snapshots(monkeypatch, tmp_path: Path, snaps: dict[str, dict | None]):
    """Map each skill to a snapshot file (or None = no baseline) and enumerate them."""
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for skill, snap in snaps.items():
        p = snap_dir / f"{skill}.json"
        if snap is not None:
            p.write_text(json.dumps(snap), encoding="utf-8")
        paths[skill] = p
    monkeypatch.setattr(cli, "list_skills", lambda *a, **k: list(snaps))
    monkeypatch.setattr(cli, "_snapshot_path", lambda skill: paths[skill])


def test_regression_all_skills_requires_current_run():
    with pytest.raises(SystemExit):
        cli.cmd_regression_all_skills(_reg_args(None))


def test_regression_all_skills_clean_passes(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _patch_snapshots(monkeypatch, tmp_path, {"alpha": _snapshot(), "beta": _snapshot()})
    _write_scoreboard(run_dir, "alpha", "mock", _snapshot())
    _write_scoreboard(run_dir, "beta", "mock", _snapshot())

    # No regression → returns without raising.
    cli.cmd_regression_all_skills(_reg_args(run_dir))


def test_regression_all_skills_injected_regression_fails(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _patch_snapshots(monkeypatch, tmp_path, {"alpha": _snapshot(), "beta": _snapshot()})
    _write_scoreboard(run_dir, "alpha", "mock", _snapshot())
    # beta's current outcome drops well below threshold → must fail the aggregate.
    _write_scoreboard(run_dir, "beta", "mock", _snapshot(outcome=0.5))

    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression_all_skills(_reg_args(run_dir))
    assert exc.value.code != 0


def test_regression_all_skills_not_self_compare(tmp_path, monkeypatch, capsys):
    """Proves the gate diffs against the *current* file, not the snapshot vs itself:
    a worse current must be detected as a regression."""
    run_dir = tmp_path / "run"
    _patch_snapshots(monkeypatch, tmp_path, {"alpha": _snapshot(outcome=0.95)})
    _write_scoreboard(run_dir, "alpha", "mock", _snapshot(outcome=0.40))

    with pytest.raises(SystemExit):
        cli.cmd_regression_all_skills(_reg_args(run_dir))


def test_regression_all_skills_skips_mismatched_verifier(tmp_path, monkeypatch, capsys):
    """A `success` baseline can't be gated by a `cheap`-only sweep: skip, don't fail."""
    run_dir = tmp_path / "run"
    _patch_snapshots(monkeypatch, tmp_path, {"docs": _snapshot(verifier="success")})
    # The run only produced cheap scoreboards (for some other skill).
    _write_scoreboard(run_dir, "other", "mock", _snapshot(verifier="cheap"))

    cli.cmd_regression_all_skills(_reg_args(run_dir))  # exit 0
    out = capsys.readouterr().out.lower()
    assert "skipped" in out and "success" in out


def test_regression_all_skills_missing_current_same_verifier_fails(tmp_path, monkeypatch):
    """If the sweep ran the snapshot's verifier but is missing this skill's file,
    that's a real coverage gap → fail (not a silent pass)."""
    run_dir = tmp_path / "run"
    _patch_snapshots(monkeypatch, tmp_path, {"alpha": _snapshot(), "beta": _snapshot()})
    _write_scoreboard(run_dir, "alpha", "mock", _snapshot())  # beta's cheap file missing

    with pytest.raises(SystemExit) as exc:
        cli.cmd_regression_all_skills(_reg_args(run_dir))
    assert exc.value.code != 0


def test_regression_all_skills_no_baseline_reported_not_failing(tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _patch_snapshots(monkeypatch, tmp_path, {"newskill": None})
    # No current file either; still must not be a silent pass — reported as no baseline.

    cli.cmd_regression_all_skills(_reg_args(run_dir))  # exit 0
    out = capsys.readouterr().out
    assert "no baseline" in out.lower()
    assert "newskill" in out


# ── Phase 3: skill-aware history (trend) ──────────────────────────────────────


def _write_matrix(run_dir: Path, label: str, date: str, skills: dict, verifier="cheap"):
    run_dir.mkdir(parents=True, exist_ok=True)
    matrix = {
        "verifier": verifier,
        "meta": {"label": label, "date": date, "git_sha": "deadbeef", "backends": ["mock"]},
        "skills": skills,
        "skipped": [],
    }
    (run_dir / "matrix.json").write_text(json.dumps(matrix), encoding="utf-8")


def _cell(outcome):
    return {
        "outcome_accuracy": outcome,
        "avg_knaif_score": 1.0,
        "tool_accuracy": 1.0,
        "schema_validity": 1.0,
        "total": 10,
    }


def _trend_args(results_dir, skill, **overrides):
    base = {"results_dir": str(results_dir), "skill": skill, "last": None, "backend": None}
    base.update(overrides)
    return argparse.Namespace(**base)


def test_trend_reads_matrix_history_in_date_order(tmp_path, capsys):
    runs = tmp_path / "runs"
    _write_matrix(runs / "r2", "build-b", "2026-06-02", {"alpha": {"mock": _cell(0.92)}})
    _write_matrix(runs / "r1", "build-a", "2026-06-01", {"alpha": {"mock": _cell(0.80)}})

    cli.cmd_trend(_trend_args(tmp_path, "alpha"))
    out = capsys.readouterr().out
    # Oldest build first.
    assert out.index("build-a") < out.index("build-b")
    assert "0.800" in out and "0.920" in out


def test_trend_filters_to_requested_skill(tmp_path, capsys):
    runs = tmp_path / "runs"
    _write_matrix(runs / "r1", "has-alpha", "2026-06-01", {"alpha": {"mock": _cell(0.9)}})
    _write_matrix(runs / "r2", "beta-only", "2026-06-02", {"beta": {"mock": _cell(0.9)}})

    cli.cmd_trend(_trend_args(tmp_path, "alpha"))
    out = capsys.readouterr().out
    assert "has-alpha" in out
    assert "beta-only" not in out


def test_trend_last_n_limits_builds(tmp_path, capsys):
    runs = tmp_path / "runs"
    _write_matrix(runs / "r1", "oldest", "2026-06-01", {"alpha": {"mock": _cell(0.1)}})
    _write_matrix(runs / "r2", "middle", "2026-06-02", {"alpha": {"mock": _cell(0.2)}})
    _write_matrix(runs / "r3", "newest", "2026-06-03", {"alpha": {"mock": _cell(0.3)}})

    cli.cmd_trend(_trend_args(tmp_path, "alpha", last=2))
    out = capsys.readouterr().out
    assert "oldest" not in out
    assert "middle" in out and "newest" in out
