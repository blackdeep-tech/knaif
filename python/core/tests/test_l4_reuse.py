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
    assert out["composed_from"]["base"]["path"] == "cuda.json"
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
    assert board["composed_from"]["base"]["path"].endswith("cuda.json")
    assert len(board["composed_from"]["base"]["file_sha256"]) == 64, "the file composed from"
    assert len(board["composed_from"]["replacements"]["file_sha256"]) == 64


# ── hardening (Codex pre-freeze audit, 2026-09-28) ────────────────────────────────────────


def test_composition_refuses_an_empty_replacement() -> None:
    base = _board([_scored("a", 0, True, 1.0)])
    with pytest.raises(ValueError, match="no rows"):
        cmp.compose_cell(base, _board([]), sources={})


def test_composition_refuses_a_duplicate_key_on_either_side() -> None:
    base = _board([_scored("a", 0, True, 1.0), _scored("a", 1, True, 1.0)])
    twice = _board([_scored("a", 1, True, 1.0), _scored("a", 1, False, 0.0)])
    with pytest.raises(ValueError, match="duplicate"):
        cmp.compose_cell(base, twice, sources={})
    dup_base = _board([_scored("a", 0, True, 1.0), _scored("a", 0, False, 0.0)])
    with pytest.raises(ValueError, match="duplicate"):
        cmp.compose_cell(dup_base, _board([_scored("a", 0, True, 1.0)]), sources={})


def test_composition_refuses_a_row_without_an_utterance_index() -> None:
    """A missing index used to read as 0, silently replacing another utterance's row."""
    base = _board([_scored("a", 0, True, 1.0)])
    row = _scored("a", 0, True, 1.0)
    del row["utterance_idx"]
    with pytest.raises(ValueError, match="utterance_idx"):
        cmp.compose_cell(base, _board([row]), sources={})


def test_a_composed_cell_carries_both_sources_fingerprints() -> None:
    base = _board([_scored("a", 0, True, 1.0)], git_sha="g-cuda", binary_sha256="bin-cuda")
    repl = _board(
        [_scored("a", 0, True, 1.0)],
        git_sha="g-cpu",
        binary_sha256="bin-cpu",
        compute_backend="CPU",
    )
    out = cmp.compose_cell(
        base,
        repl,
        sources={
            "base": {"path": "cuda.json", "file_sha256": "f1"},
            "replacements": {"path": "cpu.json", "file_sha256": "f2"},
            "note": "rule",
        },
    )
    src = out["composed_from"]
    assert src["base"]["path"] == "cuda.json" and src["base"]["file_sha256"] == "f1"
    assert src["base"]["git_sha"] == "g-cuda" and src["base"]["binary_sha256"] == "bin-cuda"
    assert src["base"]["compute_backend"] == "CUDA0"
    assert src["replacements"]["git_sha"] == "g-cpu"
    assert src["replacements"]["binary_sha256"] == "bin-cpu"
    assert src["note"] == "rule"


def test_a_composed_cell_keeps_the_policy_its_rows_were_graded_under() -> None:
    """Re-aggregating must not restamp today's policy onto rows graded under an older one."""
    base = _board([_scored("a", 0, True, 1.0)], scoring_policy=1)
    repl = _board([_scored("a", 0, True, 1.0)], scoring_policy=1)
    assert cmp.compose_cell(base, repl, sources={})["scoring_policy"] == 1


def test_a_composed_cell_reports_no_latency() -> None:
    """Its rows ran on two backends; a mean over both describes neither."""
    base = _board([_scored("a", 0, True, 1.0), _scored("a", 1, True, 1.0)])
    repl = _board([_scored("a", 1, True, 1.0)], compute_backend="CPU")
    out = cmp.compose_cell(base, repl, sources={})
    assert out["time_to_artifact_ms"] is None
    assert all(tag["time_to_artifact_ms"] is None for tag in out["by_tag"].values())


def test_the_rerun_set_is_every_full_plan_difference_and_every_unplanned_row() -> None:
    def planned(rid: str, idx: int, plan: list) -> dict:
        row = _scored(rid, idx, True, 1.0)
        row["plan"] = {"plan": plan}
        return row

    trim = {"tool": "trim_video", "args": {"input": "clip.mp4", "end": "5"}}
    base = _board(
        [
            planned("same", 0, [trim]),
            planned("file", 0, [trim]),
            planned("prose", 0, [{"tool": "clarify", "args": {"question": "Which file?"}}]),
            planned("tool", 0, [trim]),
            planned("new", 0, [trim]),
        ]
    )
    plans = {
        ("same", 0): [trim],
        ("file", 0): [{"tool": "trim_video", "args": {"input": "other.mp4", "end": "5"}}],
        ("prose", 0): [{"tool": "clarify", "args": {"question": "Which video file?"}}],
        ("tool", 0): [{"tool": "strip_audio", "args": {"inputs": ["clip.mp4"]}}],
    }
    rerun = cmp.rerun_set(base, plans)
    assert rerun.flipped == [("file", 0), ("tool", 0)], "file args count; clarify wording does not"
    assert rerun.unplanned == [("new", 0)]
    assert rerun.keys == [("file", 0), ("new", 0), ("tool", 0)]


def test_the_gate_names_a_composed_cell(tmp_path: Path) -> None:
    from knaif.evalsuite.gate import evaluate_skill, record_layers
    from knaif.evalsuite.matrix import cell_key

    from .test_acceptance_matrix import MODEL, _contracts_and_l3, _matrix
    from .test_gate import make_tree

    tree = make_tree(tmp_path)
    _matrix(tree)
    _contracts_and_l3(tree)
    cpu = cell_key(MODEL, "windows-x64", "cpu")
    record_layers(
        "demo",
        tree,
        {"L4": {"cell": cpu, "summary": "ACCEPTED", "passed": True, "composed": True}},
    )
    record_layers(
        "demo",
        tree,
        {"L4": {"cell": cell_key(MODEL, "windows-x64", "cuda"), "summary": "run", "passed": True}},
    )
    l4 = next(s for s in evaluate_skill("demo", tree, "supported").layers if s.layer == "L4")
    assert l4.state == "valid"
    assert "composed" in l4.detail and cpu in l4.detail


def test_the_printed_scoreboard_says_composed() -> None:
    import io

    from knaif.evalsuite.report import print_scoreboard

    base = _board([_scored("a", 0, True, 1.0)])
    out = cmp.compose_cell(
        base,
        _board([_scored("a", 0, True, 1.0)], compute_backend="CPU"),
        sources={"base": "cuda.json", "replacements": "cpu.json"},
    )
    buf = io.StringIO()
    print_scoreboard(out, file=buf)
    assert "COMPOSED" in buf.getvalue()


def test_rerun_set_writes_an_only_file_the_native_lane_reads(tmp_path: Path, capsys) -> None:
    import json

    from knaif.evalsuite.cli import build_parser, cmd_rerun_set, load_only

    row = _scored("a", 0, True, 1.0)
    row["plan"] = {"plan": [{"tool": "strip_audio", "args": {"inputs": ["clip.mp4"]}}]}
    base = tmp_path / "cuda.json"
    base.write_text(json.dumps(_board([row, _scored("b", 1, True, 1.0)])), encoding="utf-8")
    plans = tmp_path / "cpu.jsonl"
    plans.write_text(
        json.dumps({"id": "a", "utterance_idx": 0, "plan": [{"tool": "clarify", "args": {}}]})
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "only.json"
    args = build_parser().parse_args(
        ["rerun-set", "--base", str(base), "--plans", str(plans), "--out", str(out)]
    )
    cmd_rerun_set(args)

    assert load_only(str(out)) == {("a", 0), ("b", 1)}
    printed = capsys.readouterr().out
    assert "1 planned differently" in printed and "1 without a reused plan" in printed
