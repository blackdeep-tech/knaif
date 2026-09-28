"""Reuse of L4 evidence where it reliably holds (release plan R5c, owner 2026-09-27).

A CPU cell may rest on a measured cell plus the rows a CPU run changed, and a Linux CPU cell on the
Windows one plus a sample that shows the two plan alike. Both are *composed*: scored by the same
aggregation as a measured run, and recorded as composed with their sources, never as if a full run
had happened.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from knaif.evalsuite import compose as cmp
from knaif.evalsuite import native_lane
from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.scoring import aggregate_scored_rows

# ── running a pre-drawn sample, indices kept ──────────────────────────────────────────────


def _row(rid: str, n: int) -> CorpusRow:
    return CorpusRow(
        id=rid,
        utterances=[f"{rid} utterance {i}" for i in range(n)],
        expected_outcome="plan",
        tags=["t"],
    )


def test_only_runs_the_listed_utterances_and_keeps_their_indices(tmp_path: Path, monkeypatch):
    ran: list[str] = []

    def fake_run(argv, **kwargs):
        ran.append(" ".join(argv[-3:]))  # the lane passes the utterance word by word
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(native_lane.subprocess, "run", fake_run)
    lane = native_lane.LaneConfig(name="l", binary=tmp_path / "k.exe", model_path=tmp_path / "m")
    fixtures = tmp_path / "fx"
    fixtures.mkdir()

    outputs = native_lane.run_native_corpus(
        lane,
        "ffmpeg",
        [_row("a", 3), _row("b", 2)],
        fixture_dir=fixtures,
        sandbox=tmp_path / "sb",
        only={("a", 2), ("b", 0)},
    )

    assert [(o.id, o.utterance_idx) for o in outputs] == [("a", 2), ("b", 0)]
    assert ran == ["a utterance 2", "b utterance 0"]


# ── one aggregation for measured and composed boards ──────────────────────────────────────


def _scored(rid: str, idx: int, ok: bool, knaif: float | None, tags=("t",)) -> dict:
    return {
        "id": rid,
        "utterance": f"{rid} {idx}",
        "utterance_idx": idx,
        "expected_outcome": "plan",
        "actual_outcome": "plan" if ok else "clarify",
        "outcome_correct": ok,
        "tags": list(tags),
        "latency_ms": 100.0,
        "error": None,
        "artifact": None,
        "plan": {"plan": []},
        "knaif_score": knaif,
        "knaif_matched": [],
        "knaif_failed": [],
        "verifier_kind": None,
        "baseline_score": None,
    }


def test_the_aggregation_counts_outcome_knaif_and_slices() -> None:
    rows = [_scored("a", 0, True, 1.0), _scored("a", 1, False, None), _scored("b", 0, True, 0.5)]
    board = aggregate_scored_rows(rows, [], "success")
    assert board["total"] == 3
    assert board["outcome_accuracy"] == pytest.approx(2 / 3)
    assert board["avg_knaif_score"] == pytest.approx(0.75)
    assert board["by_tag"]["t"]["total"] == 3
    assert board["verifier"] == "success"


# ── composing a cell ──────────────────────────────────────────────────────────────────────


def _board(rows: list[dict], **meta) -> dict:
    board = aggregate_scored_rows(rows, [], "success")
    board.update(
        {
            "lane": "r5c-win-4b",
            "lane_kind": "native_cli",
            "backend_public_name": "knaif-qwen3-4b-v2",
            "binary_sha256": "bin",
            "model_sha256": "mod",
            "packaged_layout": True,
            "compute_backend": "CUDA0",
            "os": "windows-x64",
        }
    )
    board.update(meta)
    return board


def test_a_composed_cell_takes_the_replacement_rows_and_rescores() -> None:
    base = _board([_scored("a", 0, True, 1.0), _scored("a", 1, False, 0.0)])
    repl = _board([_scored("a", 1, True, 1.0)], compute_backend="CPU")

    out = cmp.compose_cell(base, repl, sources={"base": "cuda.json", "replacements": "cpu.json"})

    assert out["outcome_accuracy"] == 1.0
    assert out["total"] == 2
    assert out["composed"] is True
    assert out["composed_from"]["replaced_rows"] == [["a", 1]]
    assert out["composed_from"]["base"] == "cuda.json"
    assert out["compute_backend"] == "CPU", "the cell is the replacement's backend"


def test_a_composed_cell_can_be_filed_under_another_os() -> None:
    base = _board([_scored("a", 0, True, 1.0)], compute_backend="CPU")
    sample = _board(
        [_scored("a", 0, True, 1.0)],
        compute_backend="CPU",
        os="linux-x64",
        binary_sha256="linux-bin",
    )

    out = cmp.compose_cell(base, sample, sources={"base": "win.json", "replacements": "lin.json"})

    assert out["os"] == "linux-x64"
    assert out["binary_sha256"] == "linux-bin", "the binary the cell claims is the one sampled"


def test_composition_refuses_a_different_model_or_a_row_the_base_lacks() -> None:
    base = _board([_scored("a", 0, True, 1.0)])
    with pytest.raises(ValueError, match="model"):
        cmp.compose_cell(
            base, _board([_scored("a", 0, True, 1.0)], model_sha256="other"), sources={}
        )
    with pytest.raises(ValueError, match="not in the base"):
        cmp.compose_cell(base, _board([_scored("z", 0, True, 1.0)]), sources={})


def test_the_l4_record_says_composed() -> None:
    from knaif.evalsuite.cli import l4_record_entry

    board = _board([_scored("a", 0, True, 1.0)])
    board["composed"] = True
    board["composed_from"] = {"base": "cuda.json", "replacements": "cpu.json"}
    entry = l4_record_entry(
        board, Path("c.json"), summary="ACCEPTED", passed=True, cell="x", evidence={}
    )
    assert entry["composed"] is True
    assert entry["composed_from"]["base"] == "cuda.json"
    measured = l4_record_entry(
        _board([_scored("a", 0, True, 1.0)]),
        Path("m.json"),
        summary="ACCEPTED",
        passed=True,
        cell="x",
        evidence={},
    )
    assert "composed" not in measured


# ── command line ──────────────────────────────────────────────────────────────────────────


def test_native_takes_a_list_of_utterances_to_run(tmp_path: Path) -> None:
    import json

    from knaif.evalsuite.cli import build_parser, load_only

    only = tmp_path / "only.json"
    only.write_text(json.dumps([["ffmpeg_001", 2], ["ffmpeg_140", 0]]), encoding="utf-8")
    args = build_parser().parse_args(
        ["native", "--skill", "ffmpeg", "--lane", "l", "--only", str(only)]
    )
    assert load_only(args.only) == {("ffmpeg_001", 2), ("ffmpeg_140", 0)}
    assert load_only(None) is None


def test_compose_writes_a_composed_board(tmp_path: Path) -> None:
    import json

    from knaif.evalsuite.cli import build_parser, cmd_compose

    base = tmp_path / "cuda.json"
    repl = tmp_path / "cpu.json"
    out = tmp_path / "composed.json"
    base.write_text(json.dumps(_board([_scored("a", 0, True, 1.0), _scored("a", 1, False, 0.0)])))
    repl.write_text(json.dumps(_board([_scored("a", 1, True, 1.0)], compute_backend="CPU")))

    args = build_parser().parse_args(
        [
            "compose",
            "--base",
            str(base),
            "--replace",
            str(repl),
            "--out",
            str(out),
            "--note",
            "4B CPU cell: CUDA cell + the rows the CPU planned differently",
        ]
    )
    cmd_compose(args)

    board = json.loads(out.read_text(encoding="utf-8"))
    assert board["composed"] is True and board["outcome_accuracy"] == 1.0
    assert board["composed_from"]["note"].startswith("4B CPU cell")
    assert board["composed_from"]["base"].endswith("cuda.json")
