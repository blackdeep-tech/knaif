"""Guard `scripts/l4_rows.py` — the committed per-row extract of an L4 board, and the comparison
the macOS evals read it with (D14/D17 in the 2026-08-02 macOS support plan)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "l4_rows.py"


def _load():
    spec = importlib.util.spec_from_file_location("l4_rows", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rows = _load()


def _row(rid: str, idx: int, plan: list, outcome: bool = True, score=1.0) -> dict:
    return {
        "id": rid,
        "utterance_idx": idx,
        "outcome_correct": outcome,
        "knaif_score": score,
        "plan": {"plan": plan},
        "utterance": "a user's words, never copied",
    }


def _board(path: Path, rows_: list[dict]) -> Path:
    path.write_text(json.dumps({"rows": rows_, "config": {"bin": "C:/Users/alice/knaif.exe"}}))
    return path


COMPRESS = [{"tool": "compress", "args": {"input": "a.mp4", "crf": 28}}]
COMPRESS_OTHER_FILE = [{"tool": "compress", "args": {"input": "b.mp4", "crf": "28"}}]
RESIZE = [{"tool": "resize", "args": {"input": "a.mp4", "height": 720}}]


def test_extract_keeps_grades_and_fingerprints_and_nothing_else(tmp_path: Path) -> None:
    board = _board(tmp_path / "b.json", [_row("ffmpeg_1", 0, COMPRESS), _row("ffmpeg_2", 1, [])])
    out = rows.extract(board, label="windows/4b/cuda/ffmpeg", source="evals/runs/x/b.json")
    text = json.dumps(out)
    assert "utterance" not in text.replace("utterance_idx", "")
    assert "alice" not in text and "a.mp4" not in text
    assert out["label"] == "windows/4b/cuda/ffmpeg"
    assert out["source"] == "evals/runs/x/b.json"
    assert len(out["board_sha256"]) == 64
    first = out["rows"][0]
    assert first[:3] == ["ffmpeg_1", 0, 1]
    assert all(len(h) == 12 for h in first[3:])


def test_the_file_argument_never_changes_a_fingerprint(tmp_path: Path) -> None:
    # Core rewrites file arguments after the model answers; a decision flip is about the model.
    a = rows.fingerprints(COMPRESS)
    b = rows.fingerprints(COMPRESS_OTHER_FILE)
    assert a == b
    assert rows.fingerprints(RESIZE) != a


def test_correct_needs_the_outcome_and_a_full_artifact_score() -> None:
    assert rows.correct(_row("x", 0, [], True, 1.0))
    assert rows.correct(_row("x", 0, [], True, None))
    assert not rows.correct(_row("x", 0, [], True, 0.5))
    assert not rows.correct(_row("x", 0, [], False, 1.0))


def test_compare_splits_flips_by_who_was_right(tmp_path: Path) -> None:
    ref_board = _board(
        tmp_path / "ref.json",
        [
            _row("r1", 0, COMPRESS),
            _row("r2", 0, COMPRESS),
            _row("r3", 0, COMPRESS, outcome=False),
            _row("r4", 0, COMPRESS),
        ],
    )
    ref = rows.extract(ref_board, label="windows", source="s")
    mac = _board(
        tmp_path / "mac.json",
        [
            _row("r1", 0, COMPRESS),  # same plan
            _row("r2", 0, RESIZE, outcome=False),  # flip, only the reference right
            _row("r3", 0, RESIZE),  # flip, only the Mac right
            _row("r4", 0, RESIZE),  # flip, both right
            _row("r5", 0, RESIZE),  # not in the reference
        ],
    )
    report = rows.compare(ref, mac)
    assert report["shared"] == 4
    decision = report["decision"]
    assert decision["flips"] == 3
    assert decision["only_reference"] == ["r2#0"]
    assert decision["only_other"] == ["r3#0"]
    assert decision["both_correct"] == ["r4#0"]
    assert report["grades_differ"] == ["r2#0", "r3#0"]


def test_cli_extract_then_compare(tmp_path: Path, capsys) -> None:
    board = _board(tmp_path / "b.json", [_row("r1", 0, COMPRESS)])
    out = tmp_path / "ref.json"
    assert rows.main(["extract", str(board), "--label", "l", "--source", "s", "-o", str(out)]) == 0
    assert rows.main(["compare", str(out), str(board)]) == 0
    assert '"flips": 0' in capsys.readouterr().out


def test_the_committed_extracts_are_well_formed() -> None:
    folder = REPO / "evals" / "parity" / "1.2.0-l4-rows"
    files = sorted(folder.glob("*.json"))
    assert files, "the 1.2.0 reference extract is committed (D17)"
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["source"].startswith("evals/runs/"), path.name
        assert doc["rows"] and all(len(r) == 5 for r in doc["rows"]), path.name
