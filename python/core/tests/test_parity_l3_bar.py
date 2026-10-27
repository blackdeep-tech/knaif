"""L3 at the owner's bar: zero port bugs, plus bounded plan disagreement (release plan R0/R2).

The old bar was an equivalence rate of 1.0, unreachable by construction: the two runtimes link
different llama.cpp builds, and even three native backends do not agree 100%. It also lumped two
different findings into "mismatch":

* the runtimes chose DIFFERENT PLANS — model noise on a near-tie, bounded, not a port defect;
* the runtimes chose the SAME PLAN and rendered DIFFERENT COMMANDS — a porting defect.

Both CLIs now dump the plan they ran (`$KNAIF_DUMP_PLAN`), so command-mode rows can be split.
L3 fails on any port bug (and on any capability native has not built), and on plan disagreement
above a bound written before the run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "parity_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("parity_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["parity_check"] = module
    spec.loader.exec_module(module)
    return module


pc = _load()

PLAN = [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "container": "mkv"}}]
OTHER_PLAN = [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "container": "webm"}}]
CMD_A = "ffmpeg -y -i clip.mp4 -c copy clip_converted.mkv"
CMD_B = "ffmpeg -y -i clip.mp4 -c:v libx264 clip_converted.mkv"


def _dump(plan: list[dict]) -> str:
    return f"{pc.PLAN_DUMP_MARKER}{json.dumps({'plan': plan})}\n"


def _native(cmd: str, plan: list[dict]):
    return pc.parse_native(cmd + "\n", "ggml log\n" + _dump(plan))


def _python(cmd: str, plan: list[dict]):
    return pc.parse_python(f"{cmd}\n", _dump(plan))


def _row():
    return pc.Row(id="x_001", utterance="convert clip.mp4 to mkv", tags=[], is_chain=False)


def test_both_parsers_read_the_dumped_plan() -> None:
    assert _native(CMD_A, PLAN).dumped_plan == PLAN
    assert _python(CMD_A, PLAN).dumped_plan == PLAN


def test_same_plan_different_commands_is_a_port_bug() -> None:
    status, _ = pc.compare(
        _row(), _native(CMD_A, PLAN), _python(CMD_B, PLAN), strict=False, cwd="/w"
    )
    assert status == "port-bug"


def test_different_plans_are_plan_disagreement_not_a_port_bug() -> None:
    status, _ = pc.compare(
        _row(), _native(CMD_A, PLAN), _python(CMD_B, OTHER_PLAN), strict=False, cwd="/w"
    )
    assert status == "mismatch"


def test_equal_commands_are_a_match_whatever_the_plans() -> None:
    status, _ = pc.compare(
        _row(), _native(CMD_A, PLAN), _python(CMD_A, PLAN), strict=False, cwd="/w"
    )
    assert status == "match"


def test_without_a_dump_a_mismatch_cannot_be_called_a_port_bug() -> None:
    nat = pc.parse_native(CMD_A + "\n", "")
    py = pc.parse_python(f"{CMD_B}\n", "")
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "mismatch"


# ── reading each side's command line the way it was written (L3 2026-09-27) ────────────────

# The two renders of "crop clip.mp4 to a square" from the stopped R5c run. Both runtimes pass
# `iw\,ih` to ffmpeg (L4 executed the row correctly on native). Native prints a POSIX-quoted
# line (`shell_join` doubles a backslash inside quotes); Python printed the argv unquoted
# with absolute paths until 2026-10-23, and its old line must still read the same.
CROP = r"crop=trunc(min(iw\,ih*1/1)/2)*2:trunc(min(ih\,iw*1/1)/2)*2"
CROP_NATIVE = (
    'ffmpeg -y -i clip.mp4 -vf "crop=trunc(min(iw\\\\,ih*1/1)/2)*2:trunc(min(ih\\\\,iw*1/1)/2)*2"'
    " -c:a copy clip_resized.mp4"
)
CROP_PYTHON = CROP_NATIVE  # Python now prints native's line
CROP_PLAN = [
    {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "fit": "crop", "aspect": "1:1"}}
]


def test_an_ffmpeg_escape_survives_both_parsers() -> None:
    """The tokenizer forward-slashed every backslash to cope with Windows paths, so the filter's
    `\\,` became `/,` on Python's unquoted line and `//,` on native's quoted one: three rows of
    identical commands reported as port bugs."""
    nat = _native(CROP_NATIVE, CROP_PLAN)
    py = _python(CROP_PYTHON, CROP_PLAN)
    assert CROP in nat.commands[0]
    assert CROP in py.commands[0]
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="C:/w")
    assert status == "match"


def test_a_windows_path_is_still_forward_slashed() -> None:
    assert pc.to_argv(r"ffmpeg -i C:\w\sub\clip.mp4 out.mp4") == [
        "ffmpeg",
        "-i",
        "C:/w/sub/clip.mp4",
        "out.mp4",
    ]
    assert pc.to_argv('ffmpeg -i "C:\\\\w\\\\clip.mp4" out.mp4', quoted=True) == [
        "ffmpeg",
        "-i",
        "C:/w/clip.mp4",
        "out.mp4",
    ]


CHAIN_PLAN = [
    {"tool": "trim_video", "args": {"input": "clip.mp4", "start": "00:00:01", "end": "00:00:06"}},
    {"tool": "reverse_video", "args": {"inputs": ["clip_trimmed.mp4"]}},
]
TRIM = "ffmpeg -y -ss 00:00:01 -to 00:00:06 -i clip.mp4 -c:v libx264 clip_trimmed.mp4"
REVERSE = "ffmpeg -y -i clip_trimmed.mp4 -vf reverse clip_trimmed_reversed.mp4"


def _python_partial_chain(plan: list[dict]):
    """Python's dry-run of trim -> reverse: reverse stops at its preview/confirmation."""
    out = f"step 1 of 2:\n{TRIM}\nstep 2 of 2:\n"
    return pc.parse_python(out, "(nothing to execute)\n" + _dump(plan))


def test_a_chain_python_renders_only_in_part_is_not_comparable_when_the_plans_agree() -> None:
    """ffmpeg_140: Python rendered the trim and printed "(nothing to execute)" for the reverse
    (its dry-run stops at the preview confirmation); native rendered both. The same plan with
    one side's commands cut short is not a port bug."""
    nat = _native(TRIM + "\n" + REVERSE, CHAIN_PLAN)
    status, _ = pc.compare(_row(), nat, _python_partial_chain(CHAIN_PLAN), strict=False, cwd="/w")
    assert status == "not-comparable"


def test_a_partial_python_chain_with_a_different_plan_is_still_disagreement() -> None:
    other = [CHAIN_PLAN[0]]
    nat = _native(TRIM + "\n" + REVERSE, CHAIN_PLAN)
    status, _ = pc.compare(_row(), nat, _python_partial_chain(other), strict=False, cwd="/w")
    assert status == "mismatch"


def _counts(**over: int) -> dict[str, int]:
    base = {
        "match": 95,
        "mismatch": 0,
        "decline-divergence": 0,
        "not-comparable": 3,
        "native-not-implemented": 0,
        "port-bug": 0,
    }
    base.update(over)
    return base


def test_the_verdict_passes_bounded_disagreement_and_no_port_bugs() -> None:
    v = pc.l3_verdict(_counts(match=96, mismatch=3, **{"decline-divergence": 1}), 0.05)
    assert v["passed"] and v["port_bugs"] == 0 and v["plan_disagreement"] == 4
    assert v["plan_disagreement_rate"] == pytest.approx(0.04)


def test_one_port_bug_fails_whatever_the_rates() -> None:
    v = pc.l3_verdict(_counts(**{"port-bug": 1}), 0.5)
    assert not v["passed"]


def test_a_capability_native_has_not_built_fails() -> None:
    v = pc.l3_verdict(_counts(**{"native-not-implemented": 1}), 0.5)
    assert not v["passed"]


def test_disagreement_over_the_bound_fails() -> None:
    v = pc.l3_verdict(_counts(match=94, mismatch=6), 0.05)
    assert not v["passed"] and v["plan_disagreement_rate"] == pytest.approx(0.06)


def test_nothing_to_compare_does_not_pass() -> None:
    v = pc.l3_verdict(dict.fromkeys(_counts(), 0), 0.05)
    assert not v["passed"]


def test_evidence_runs_must_state_the_bound_before_running() -> None:
    """A bound chosen after seeing the result is not a bound."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--skill", "ffmpeg", "--label", "x"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "--max-plan-disagreement" in proc.stderr + proc.stdout


def test_the_python_cli_dumps_its_plan_like_native(tmp_path: Path) -> None:
    env = {**os.environ, "KNAIF_DUMP_PLAN": "1"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "knaif.app",
            "run",
            "ffmpeg",
            "convert",
            "clip.mp4",
            "to",
            "mkv",
            "--backend",
            "mock",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=REPO,
    )
    dumps = [ln for ln in proc.stderr.splitlines() if ln.startswith(pc.PLAN_DUMP_MARKER)]
    assert dumps, proc.stderr[-500:]
    assert "plan" in json.loads(dumps[0][len(pc.PLAN_DUMP_MARKER) :])


def test_the_marker_is_one_string_in_all_three_places() -> None:
    from knaif.app import PLAN_DUMP_MARKER as app_marker
    from knaif.evalsuite.native_lane import PLAN_DUMP_MARKER as lane_marker

    rust = (REPO / "apps" / "cli" / "src" / "main.rs").read_text(encoding="utf-8")
    assert f'const PLAN_DUMP_MARKER: &str = "{pc.PLAN_DUMP_MARKER}";' in rust
    assert app_marker == lane_marker == pc.PLAN_DUMP_MARKER


# ── the exact argv, as data (Codex audit of 62f7cc1, 2026-09-28) ───────────────────────────
# Display lines cannot carry every argv: Python joins with spaces and quotes nothing, so the
# corpus's `silent clip.mp4` (ffmpeg_288) splits in two, and any backslash rule mangles either a
# path or a filter escape. Both CLIs now dump each ffmpeg argv as JSON under $KNAIF_DUMP_PLAN,
# and the comparator reads that when it is there.


def _argv_dump(argv: list[str]) -> str:
    return f"{pc.ARGV_DUMP_MARKER}{json.dumps(argv)}\n"


def test_a_dumped_argv_is_taken_verbatim_over_the_display_line() -> None:
    argv = ["ffmpeg", "-y", "-i", "silent clip.mp4", "-vf", CROP, "silent clip_thumb.jpg"]
    nat = pc.parse_native("ffmpeg -y -i garbled\n", _dump(PLAN) + _argv_dump(argv))
    py = pc.parse_python("ffmpeg -y -i also garbled\n", _dump(PLAN) + _argv_dump(argv))
    assert nat.commands == py.commands
    assert "silent clip.mp4" in nat.commands[0] and CROP in nat.commands[0]
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "match"


def test_dumped_windows_paths_compare_equal_and_filters_are_untouched() -> None:
    win = ["ffmpeg", "-i", r"C:\w\[draft]\clip.mp4", "-vf", r"drawtext=text='Hi\!'", "out.mp4"]
    fwd = ["ffmpeg", "-i", "C:/w/[draft]/clip.mp4", "-vf", r"drawtext=text='Hi\!'", "out.mp4"]
    a = pc.parse_python("", _dump(PLAN) + _argv_dump(win))
    b = pc.parse_native("", _dump(PLAN) + _argv_dump(fwd))
    assert a.commands == b.commands
    assert r"drawtext=text='Hi\!'" in a.commands[0]


def test_a_hash_in_a_filename_is_not_a_comment() -> None:
    assert pc.to_argv("ffmpeg -i clip#1.mp4 -c copy out.mp4") == [
        "ffmpeg",
        "-i",
        "clip#1.mp4",
        "-c",
        "copy",
        "out.mp4",
    ]


def test_a_partial_chain_still_compares_the_steps_both_rendered() -> None:
    """Codex P1: "not comparable" must cover only the steps Python did not render. A trim that
    renders differently is a port bug even when the reverse after it rendered nothing."""
    other_trim = TRIM.replace("-c:v libx264", "-c:v libx265")
    nat = _native(other_trim + "\n" + REVERSE, CHAIN_PLAN)
    status, _ = pc.compare(_row(), nat, _python_partial_chain(CHAIN_PLAN), strict=False, cwd="/w")
    assert status == "port-bug"


def test_the_python_cli_dumps_each_argv(tmp_path: Path) -> None:
    env = {**os.environ, "KNAIF_DUMP_PLAN": "1"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "knaif.app",
            "run",
            "ffmpeg",
            "convert",
            "clip.mp4",
            "to",
            "mkv",
            "--backend",
            "mock",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=REPO,
    )
    dumps = [ln for ln in proc.stderr.splitlines() if ln.startswith(pc.ARGV_DUMP_MARKER)]
    assert dumps, proc.stderr[-500:]
    argv = json.loads(dumps[0][len(pc.ARGV_DUMP_MARKER) :])
    assert argv[0] == "ffmpeg" and all(isinstance(a, str) for a in argv)


def test_the_argv_marker_is_one_string_in_all_three_places() -> None:
    from knaif.app import ARGV_DUMP_MARKER as app_marker

    rust = (REPO / "apps" / "cli" / "src" / "main.rs").read_text(encoding="utf-8")
    assert f'const ARGV_DUMP_MARKER: &str = "{pc.ARGV_DUMP_MARKER}";' in rust
    assert app_marker == pc.ARGV_DUMP_MARKER


# ── same plan, different outcome (Codex audit of the R5c L3 run, 2026-09-28) ───────────────


def test_same_plan_but_python_asks_and_native_runs_is_a_port_bug() -> None:
    """R5c L3 counted these as model disagreement: both runtimes planned `reverse_video` on
    "mov", Python's NL clarify gate asked, native had no such gate and ran. The plans agreed,
    so the difference is the port's, whatever the two outcomes are."""
    mov = [{"tool": "reverse_video", "args": {"inputs": ["mov"]}}]
    nat = _native("ffmpeg -y -i mov -vf reverse mov_reversed.mp4", mov)
    py = pc.parse_python("clarify:Which mov did you mean?\n", _dump(mov))
    status, note = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "port-bug"
    assert "outcome" in note


def test_different_plans_with_different_outcomes_stay_disagreement() -> None:
    nat = _native(CMD_A, PLAN)
    py = pc.parse_python("clarify:Which file?\n", _dump(OTHER_PLAN))
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "mismatch"


def test_both_asking_the_same_plan_is_a_match() -> None:
    mov = [{"tool": "reverse_video", "args": {"inputs": ["mov"]}}]
    nat = pc.parse_native("clarify: Which mov did you mean?\n", _dump(mov))
    py = pc.parse_python("clarify:Which mov did you mean?\n", _dump(mov))
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "match"


# ── committed evidence carries no local path, even cut in half (R5c T7, 2026-09-28) ─────────


def test_a_raw_excerpt_cut_mid_path_leaks_no_prefix_of_the_checkout() -> None:
    r"""`raw` is kept to its first 800 characters. Cut before redaction, a checkout path that
    straddled the cut survived as `C:\Work\Kn…`, which the redactor (it matches whole paths)
    could no longer see: both 2026-09-28 ffmpeg reports carried four such prefixes."""
    root = str(pc.REPO_ROOT)
    text = "x" * (pc.RAW_EXCERPT_CHARS - 10) + rf'["ffmpeg", "-i", "{root}\clip.mp4"]'
    excerpt = pc.raw_excerpt(text)
    assert len(excerpt) <= pc.RAW_EXCERPT_CHARS
    assert root[:6].lower() not in excerpt.lower(), excerpt[-40:]


# ── rows with no command on either side compare their plans (Codex audit, 2026-09-28) ─────
#
# documents runs in-process and renders no argv, so most rows are `none` on both sides. They
# were counted as a match on the outcome alone: 133 of 143 4B documents "matches" never compared
# the two plans.

DOC_PLAN = [{"tool": "convert_document", "args": {"input": "report.docx", "format": "pdf"}}]
DOC_OTHER = [{"tool": "convert_document", "args": {"input": "report.docx", "format": "txt"}}]


def _none(plan: list[dict]):
    return pc.parse_native("would convert -> report.pdf\n", _dump(plan))


def _none_py(plan: list[dict]):
    return pc.parse_python("ok\n", _dump(plan))


def test_no_command_on_either_side_with_different_plans_is_disagreement() -> None:
    nat, py = _none(DOC_PLAN), _none_py(DOC_OTHER)
    assert nat.kind == py.kind == "none"
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "mismatch"


def test_no_command_on_either_side_with_the_same_plan_is_a_match() -> None:
    status, _ = pc.compare(_row(), _none(DOC_PLAN), _none_py(DOC_PLAN), strict=False, cwd="/w")
    assert status == "match"


def test_no_command_and_no_dump_stays_a_match_on_the_outcome() -> None:
    nat = pc.parse_native("would convert -> report.pdf\n", "")
    py = pc.parse_python("ok\n", "")
    status, _ = pc.compare(_row(), nat, py, strict=False, cwd="/w")
    assert status == "match"


# ── an exit status is an outcome (Codex follow-up review, 2026-09-28) ─────────────────────


def test_a_failed_run_with_no_other_outcome_is_an_error() -> None:
    nat = pc.parse_native("", "Error: input not found: report.docx\n" + _dump(DOC_PLAN), 1)
    assert nat.kind == "error"
    assert nat.dumped_plan == DOC_PLAN
    assert pc.parse_python("", "Traceback ...\nValueError: bad\n" + _dump(DOC_PLAN), 1).kind == (
        "error"
    )


def test_native_failing_after_the_dump_where_python_succeeds_is_a_port_bug() -> None:
    nat = pc.parse_native("", "Error: step failed\n" + _dump(DOC_PLAN), 1)
    status, _ = pc.compare(_row(), nat, _none_py(DOC_PLAN), strict=False, cwd="/w")
    assert status == "port-bug"


def test_a_clean_exit_keeps_its_parsed_outcome() -> None:
    assert pc.parse_native("would convert -> report.pdf\n", _dump(DOC_PLAN), 0).kind == "none"
    assert pc.parse_native("clarify: which file?\n", "", 1).kind == "clarify"


def test_an_utterance_that_starts_with_a_dash_reaches_the_cli_as_text() -> None:
    """`rm -rf /` (ffmpeg_053, a reject row) was passed word by word, so both CLIs read `-rf` as
    an option and failed on usage: the row tested nothing on either side."""
    assert pc.utterance_argv("rm -rf /") == ["--", "rm", "-rf", "/"]


# ── --only: a pre-registered sample of rows (1.2.1 sampled equivalence, Python stage) ───────────


def test_only_keeps_the_named_rows_in_corpus_order_and_refuses_unknown_ids() -> None:
    rows = [pc.Row(i, f"u{i}", [], False) for i in ("a", "b", "c")]
    assert [r.id for r in pc.select_rows(rows, ["c", "a"])] == ["a", "c"]
    with pytest.raises(SystemExit):
        pc.select_rows(rows, ["a", "zzz"])


def test_scratch_paths_of_two_runs_compare_equal(tmp_path: Path, monkeypatch) -> None:
    """A routed intermediate lives in each process's own `<scratch>/run-<pid>-<hex>/`, so the two
    runtimes can never print the same folder. Same plan, same file under the run folder: a match,
    not the 13 "port bugs" the 2026-10-11 chain run reported."""
    root = tmp_path / "knaif"
    monkeypatch.setenv("KNAIF_SCRATCH_ROOT", str(root))
    a, b = (root / "run-111-aaaa1111").as_posix(), (root / "run-222-bbbb2222").as_posix()
    nat = ["ffmpeg", "-y", "-i", "clip.mp4", "-an", f"{a}/clip_fast.mp4"]
    nat2 = ["ffmpeg", "-y", "-i", f"{a}/clip_fast.mp4", "-c", "copy", "clip_fast_silent.mp4"]
    py = ["ffmpeg", "-y", "-i", "/w/clip.mp4", "-an", f"{b}/clip_fast.mp4"]
    py2 = ["ffmpeg", "-y", "-i", f"{b}/clip_fast.mp4", "-c", "copy", "/w/clip_fast_silent.mp4"]
    n = pc.parse_native("", _dump(PLAN) + _argv_dump(nat) + _argv_dump(nat2))
    p = pc.parse_python("", _dump(PLAN) + _argv_dump(py) + _argv_dump(py2))
    status, _ = pc.compare(_row(), n, p, strict=False, cwd="/w")
    assert status == "match"


def test_a_scratch_path_is_not_the_same_file_in_the_users_folder(
    tmp_path: Path, monkeypatch
) -> None:
    """Routing is ported to native: one runtime writing the intermediate into the user's folder
    while the other routes it to scratch is a real port bug, and must stay one."""
    root = tmp_path / "knaif"
    monkeypatch.setenv("KNAIF_SCRATCH_ROOT", str(root))
    routed = ["ffmpeg", "-y", "-i", "clip.mp4", "-an", f"{(root / 'run-1-a').as_posix()}/x.mp4"]
    in_folder = ["ffmpeg", "-y", "-i", "clip.mp4", "-an", "x.mp4"]
    n = pc.parse_native("", _dump(PLAN) + _argv_dump(routed))
    p = pc.parse_python("", _dump(PLAN) + _argv_dump(in_folder))
    status, _ = pc.compare(_row(), n, p, strict=False, cwd="/w")
    assert status == "port-bug"


def test_a_url_compares_whole_not_by_its_file_name():
    """A plan arg `https://h/a.mov` is not the local `a.mov`: two runtimes that disagree on whether
    the input is the URL or a local file of the same name planned different things."""
    assert pc.canon_token("https://files.knaif.test/ffmpeg/a.mov") == (
        "https://files.knaif.test/ffmpeg/a.mov"
    )
    assert pc.canon_token("https://files.knaif.test/ffmpeg/a.mov") != pc.canon_token("a.mov")
    assert pc.canon_token("videos/a.mov") == "a.mov"  # a path still compares by its name


def test_a_corpus_other_than_eval_jsonl_can_be_compared(tmp_path):
    """URL rows live in `eval_url.jsonl`, apart from the snapshot's population."""
    corpus = tmp_path / "eval_url.jsonl"
    corpus.write_text(
        json.dumps({"id": "u1", "utterances": ["convert https://h.test/a.mov to mp4"], "tags": []})
        + "\n",
        encoding="utf-8",
    )
    rows = pc.load_rows("ffmpeg", None, corpus=corpus)
    assert [(r.id, r.utterance) for r in rows] == [("u1", "convert https://h.test/a.mov to mp4")]


def test_l3_on_the_release_subset_compares_its_rows() -> None:
    """Part I, I4: L3 measures the subset's rows (each row's first utterance, as L3 always did)."""
    subset = json.loads((REPO / "skills/ffmpeg/data/release_subset.json").read_text("utf-8"))
    ids, population = pc.subset_ids("ffmpeg")
    assert ids == [rid for rid, _ in subset["rows"]]
    assert population == {"kind": "subset", "corpus_sha256": subset["corpus_sha256"]}
    rows = pc.select_rows(pc.load_rows("ffmpeg", None), ids)
    assert [r.id for r in rows] == ids
