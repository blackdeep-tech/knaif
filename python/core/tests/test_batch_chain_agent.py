"""The batch chain lift inside the agent: globs expanded on disk, then the chain run per file.

The lift itself is pinned by contracts/parity/batch_chain_cases.json
(test_batch_chain_parity.py). These tests cover what the agent adds around it: which files a
glob or folder expands to (natural order, the skill's file kinds), where in the pipeline the lift
runs (before scratch routing, so the keep rule sees ordinary per-file chains), and the run-order
switch.
"""

from __future__ import annotations

import copy
import sys
import textwrap
from pathlib import Path

import pytest

from knaif import CommandAgent
from knaif.batch_chain import expand_inputs, natural_key

# ── expansion ────────────────────────────────────────────────────────────────────────────────


def _touch(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x")


def test_natural_order_counts_numbers_as_numbers() -> None:
    names = ["clip10.mov", "Clip2.mov", "clip1.mov", "clip.mov", "clip02.mov"]
    assert sorted(names, key=natural_key) == [
        "clip.mov",
        "clip1.mov",
        "Clip2.mov",
        "clip02.mov",
        "clip10.mov",
    ]


def test_a_glob_expands_in_natural_order_with_the_models_spelling(tmp_path: Path) -> None:
    _touch(tmp_path / "videos", "clip10.mov", "clip2.mov", "clip1.mov", "notes.txt")
    got = expand_inputs(["videos/*.mov", "poster.png"], tmp_path)
    assert got == {"videos/*.mov": ["videos/clip1.mov", "videos/clip2.mov", "videos/clip10.mov"]}


def test_a_folder_expands_to_its_files_of_the_skills_kinds(tmp_path: Path) -> None:
    _touch(tmp_path / "clips", "b.mp4", "a.mov", "notes.txt")
    (tmp_path / "clips" / "sub").mkdir()
    kinds = {"mp4": "video", "mov": "video"}
    assert expand_inputs(["clips/"], tmp_path, kinds) == {"clips/": ["clips/a.mov", "clips/b.mp4"]}
    assert expand_inputs(["*"], tmp_path / "clips", kinds) == {"*": ["a.mov", "b.mp4"]}


def test_a_glob_without_matches_expands_to_nothing(tmp_path: Path) -> None:
    assert expand_inputs(["*.webm"], tmp_path) == {"*.webm": []}


# ── the agent ────────────────────────────────────────────────────────────────────────────────

_CODE = """
from pathlib import Path

from knaif.tool import Handler

SEEN = []


def _write(args, ctx):
    out = args.get("output")
    if out and not ctx.dry_run:
        path = Path(out) if Path(out).is_absolute() else Path(ctx.sandbox) / out
        path.write_bytes(b"x" * 100)
        return {"output": str(path)}
    return {"output": out}


class Convert(Handler):
    name = "convert"

    def handle(self, args, ctx):
        SEEN.append(("convert", dict(args, work_dir=ctx.work_dir, dry_run=ctx.dry_run)))
        if any("broken" in i for i in args["inputs"]):
            raise ValueError(f"cannot read {Path(args['inputs'][0]).name}\\nmore detail")
        return _write(args, ctx)


class Shrink(Handler):
    name = "shrink"

    def handle(self, args, ctx):
        SEEN.append(("shrink", dict(args, work_dir=ctx.work_dir, dry_run=ctx.dry_run)))
        if any("bad" in i for i in args["inputs"]):
            return {"returncode": 1, "stderr": "frame=0\\nencoder said no\\n"}
        return _write(args, ctx)


class Join(Handler):
    name = "join"

    def handle(self, args, ctx):
        SEEN.append(("join", dict(args, work_dir=ctx.work_dir, dry_run=ctx.dry_run)))
        return _write(args, ctx)


class BatchConvert(Handler):
    # One step over many files, as ffmpeg's run_batch: each file's end goes to the hook.
    name = "batchconvert"

    def handle(self, args, ctx):
        SEEN.append(("batchconvert", dict(args, work_dir=ctx.work_dir, dry_run=ctx.dry_run)))
        outputs = []
        for f in args["inputs"]:
            bad = "broken" in f
            outputs.append({"input": f, "output": f + ".out", "returncode": 1 if bad else 0})
            ctx.file_finished(f, f"bad data in {Path(f).name}" if bad else None)
        return {"outputs": outputs}


class Wipe(Handler):
    name = "wipe"

    def handle(self, args, ctx):
        SEEN.append(("wipe", dict(args, work_dir=ctx.work_dir, dry_run=ctx.dry_run)))
        return {}
"""

_TOOLS = """
convert:
  effects: []
  description: "Convert files."
  optional_args: [inputs, output]
  safety_category: safe
shrink:
  effects: []
  description: "Make files smaller."
  optional_args: [inputs, output]
  safety_category: safe
join:
  effects: []
  description: "Join files into one."
  optional_args: [inputs, output]
  joins_inputs: true
  safety_category: safe
batchconvert:
  effects: []
  description: "Convert many files in one step."
  optional_args: [inputs, output]
  safety_category: safe
wipe:
  effects: []
  description: "Wipe files."
  optional_args: [inputs]
  safety_category: destructive
"""


def _agent(tmp_path: Path, *, scratch: bool = False) -> tuple[CommandAgent, list, Path]:
    skill_dir = tmp_path / "repo" / "skills" / "batchy"
    (skill_dir / "python").mkdir(parents=True)
    manifest = 'name: batchy\ndescription: "Demo."\n'
    if scratch:
        manifest += "scratch_intermediates: true\n"
    (skill_dir / "skill.yaml").write_text(manifest, encoding="utf-8")
    (skill_dir / "tools.yaml").write_text(_TOOLS, encoding="utf-8")
    (skill_dir / "python" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "python" / "handlers.py").write_text(textwrap.dedent(_CODE), encoding="utf-8")
    sandbox = tmp_path / "sb"
    _touch(sandbox, "clip10.mov", "clip2.mov", "clip1.mov")
    agent = CommandAgent.from_skill(skill_dir, sandbox=sandbox)
    seen = sys.modules[type(agent.tool_map["convert"]).__module__].SEEN
    seen.clear()
    return agent, seen, sandbox


def _plan(*steps: tuple[str, list[str], str | None]) -> dict:
    out = []
    for tool, inputs, output in steps:
        args: dict = {"inputs": inputs}
        if output:
            args["output"] = output
        out.append({"tool": tool, "args": args})
    return {"plan": out}


_CHAIN = _plan(("convert", ["*.mov"], "c.mp4"), ("shrink", ["c.mp4"], "s.mp4"))
_UTTERANCE = "convert all the mov files here to mp4 and make them smaller"


def _calls(seen: list) -> list[tuple[str, list[str], str | None]]:
    return [(tool, args["inputs"], args.get("output")) for tool, args in seen]


def test_the_chain_runs_once_per_file_in_natural_order(tmp_path: Path) -> None:
    agent, seen, _ = _agent(tmp_path)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=True)
    assert _calls(seen) == [
        ("convert", ["clip1.mov"], "clip1.mp4"),
        ("shrink", ["clip1.mp4"], "clip1_s.mp4"),
        ("convert", ["clip2.mov"], "clip2.mp4"),
        ("shrink", ["clip2.mp4"], "clip2_s.mp4"),
        ("convert", ["clip10.mov"], "clip10.mp4"),
        ("shrink", ["clip10.mp4"], "clip10_s.mp4"),
    ]


def test_a_single_batch_step_reaches_the_skill_as_written(tmp_path: Path) -> None:
    agent, seen, _ = _agent(tmp_path)
    plan = _plan(("convert", ["*.mov"], "c.mp4"))
    agent.execute_plan(plan, utterance="convert all the mov files here", dry_run=True)
    assert _calls(seen) == [("convert", ["*.mov"], "c.mp4")]


def test_a_join_reads_every_files_result_in_natural_order(tmp_path: Path) -> None:
    agent, seen, _ = _agent(tmp_path)
    plan = _plan(("convert", ["*.mov"], "c.mp4"), ("join", ["c.mp4"], "all.mp4"))
    agent.execute_plan(plan, utterance="convert every mov here and join them", dry_run=True)
    assert _calls(seen)[-1] == ("join", ["clip1.mp4", "clip2.mp4", "clip10.mp4"], "all.mp4")


def test_the_lift_runs_before_scratch_routing(tmp_path: Path) -> None:
    """Each file's intermediate is routed like a one-file chain's: the keep rule sees the lift."""
    agent, seen, _ = _agent(tmp_path, scratch=True)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=True)
    work = Path(seen[0][1]["work_dir"])
    assert seen[0][1]["output"] == str(work / "clip1.mp4")
    assert seen[1][1]["inputs"] == [str(work / "clip1.mp4")]
    assert seen[1][1]["output"] == "clip1_s.mp4"


# ── a batch with failures ────────────────────────────────────────────────────────────────────


def _failing(tmp_path: Path, *extra: str) -> tuple[CommandAgent, list, Path]:
    agent, seen, sandbox = _agent(tmp_path)
    _touch(sandbox, *extra)
    agent.batch_failed = lambda line: seen.append(("failed", line))
    return agent, seen, sandbox


def test_a_failed_file_stops_only_its_own_chain(tmp_path: Path) -> None:
    agent, seen, _ = _failing(tmp_path, "clip3_broken.mov")
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=True)
    assert [(t, a if t == "failed" else a["inputs"]) for t, a in seen] == [
        ("convert", ["clip1.mov"]),
        ("shrink", ["clip1.mp4"]),
        ("convert", ["clip2.mov"]),
        ("shrink", ["clip2.mp4"]),
        ("convert", ["clip3_broken.mov"]),
        ("failed", "✗ clip3_broken.mov: cannot read clip3_broken.mov"),
        ("convert", ["clip10.mov"]),
        ("shrink", ["clip10.mp4"]),
    ]
    report = agent.last_batch_report
    assert (report.done, report.failed) == (3, 1)
    assert report.failures == [("clip3_broken.mov", "cannot read clip3_broken.mov")]


def test_a_failed_command_later_in_the_chain_counts_as_that_files_failure(
    tmp_path: Path,
) -> None:
    agent, seen, _ = _failing(tmp_path, "clip3_bad.mov")
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=True)
    assert ("failed", "✗ clip3_bad.mov: encoder said no") in seen
    assert [a["inputs"] for t, a in seen if t == "convert"][-1] == ["clip10.mov"]
    assert (agent.last_batch_report.done, agent.last_batch_report.failed) == (3, 1)


def test_a_join_leaves_out_the_files_that_failed(tmp_path: Path) -> None:
    agent, seen, _ = _failing(tmp_path, "clip3_broken.mov")
    plan = _plan(("convert", ["*.mov"], "c.mp4"), ("join", ["c.mp4"], "all.mp4"))
    agent.execute_plan(plan, utterance="convert every mov here and join them", dry_run=True)
    joins = [a for t, a in seen if t == "join"]
    assert [j["inputs"] for j in joins] == [["clip1.mp4", "clip2.mp4", "clip10.mp4"]]


def test_a_join_with_nothing_left_does_not_run(tmp_path: Path) -> None:
    agent, seen, sandbox = _agent(tmp_path)
    for name in ("clip1.mov", "clip2.mov", "clip10.mov"):
        (sandbox / name).unlink()
    _touch(sandbox, "a_broken.mov", "b_broken.mov")
    plan = _plan(("convert", ["*.mov"], "c.mp4"), ("join", ["c.mp4"], "all.mp4"))
    agent.execute_plan(plan, utterance="convert every mov here and join them", dry_run=True)
    assert not [t for t, _ in seen if t == "join"]
    assert (agent.last_batch_report.done, agent.last_batch_report.failed) == (0, 2)


def test_a_failure_outside_a_batch_still_stops_the_run(tmp_path: Path) -> None:
    agent, _, sandbox = _agent(tmp_path)
    _touch(sandbox, "one_broken.mov")
    plan = _plan(("convert", ["one_broken.mov"], "c.mp4"), ("shrink", ["c.mp4"], "s.mp4"))
    with pytest.raises(ValueError, match="cannot read"):
        agent.execute_plan(plan, utterance="convert one_broken.mov and shrink it", dry_run=True)
    assert agent.last_batch_report is None


def test_a_destructive_step_still_needs_confirmation_in_a_batch(tmp_path: Path) -> None:
    """Only a handler's own failure is the file's; a refused step refuses the whole plan, and
    since the rehearsal before anything runs."""
    agent, seen, _ = _agent(tmp_path)
    plan = _plan(("convert", ["*.mov"], "c.mp4"), ("wipe", ["c.mp4"], None))
    with pytest.raises(ValueError, match="confirmed=True"):
        agent.execute_plan(plan, utterance="convert all the movs and wipe them", dry_run=False)
    assert _real(seen) == []


# ── the rehearsal (owner, 2026-10-24) ───────────────────────────────────────────────────────
#
# Before a real run the whole plan is rehearsed as a dry run that writes nothing; it runs only
# if every step passes. Over many files, a file that fails the rehearsal is reported and
# skipped before anything runs, and the others run; when every file fails, nothing runs.


def _real(seen: list) -> list:
    """What really ran (and each failure line), without the rehearsal's dry calls."""
    return [
        (t, a if t == "failed" else a["inputs"])
        for t, a in seen
        if t == "failed" or not a.get("dry_run")
    ]


def test_a_chain_whose_later_step_would_fail_runs_nothing(tmp_path: Path) -> None:
    from knaif.agent import PlanWouldFail

    agent, seen, sandbox = _agent(tmp_path)
    plan = _plan(("convert", ["clip1.mov"], "bad.mp4"), ("shrink", ["bad.mp4"], "s.mp4"))
    with pytest.raises(PlanWouldFail) as err:
        agent.execute_plan(
            plan,
            utterance="convert clip1.mov to bad.mp4 and shrink it",
            dry_run=False,
            confirmed=True,
        )
    assert err.value.where == "step 2 of 2 would fail, so nothing ran"
    assert "encoder said no" in err.value.cause
    assert _real(seen) == []
    assert not (sandbox / "bad.mp4").exists()


def test_a_file_that_would_fail_is_reported_first_and_the_others_run(tmp_path: Path) -> None:
    agent, seen, _ = _failing(tmp_path, "clip3_broken.mov")
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=False, confirmed=True)
    ran = _real(seen)
    assert ran[0] == ("failed", "✗ clip3_broken.mov: cannot read clip3_broken.mov")
    assert [i for t, i in ran[1:] if t == "convert"] == [
        ["clip1.mov"],
        ["clip2.mov"],
        ["clip10.mov"],
    ]
    assert all("broken" not in str(i) for _, i in ran[1:])


def test_when_every_file_would_fail_nothing_runs(tmp_path: Path) -> None:
    from knaif.agent import PlanWouldFail

    agent, seen, sandbox = _agent(tmp_path)
    for f in sandbox.glob("*.mov"):
        f.unlink()
    _touch(sandbox, "a_broken.mov", "b_broken.mov")
    with pytest.raises(PlanWouldFail) as err:
        agent.execute_plan(
            copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=False, confirmed=True
        )
    assert err.value.where == "every file would fail, so nothing ran"
    assert _real(seen) == []


def test_one_step_on_one_file_is_not_rehearsed(tmp_path: Path) -> None:
    # Nothing can run before it fails, so a rehearsal would only do the work twice.
    agent, seen, _ = _agent(tmp_path)
    plan = _plan(("convert", ["clip1.mov"], "c.mp4"))
    agent.execute_plan(plan, utterance="convert clip1.mov", dry_run=False, confirmed=True)
    assert [a["dry_run"] for _, a in seen] == [False]


def test_the_report_counts_time_and_the_size_left(tmp_path: Path) -> None:
    agent, _, sandbox = _failing(tmp_path, "clip3_broken.mov")
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=False, confirmed=True)
    report = agent.last_batch_report
    assert (report.done, report.failed) == (3, 1)
    # Three files, each leaving the converted and the shrunk copy (100 bytes each).
    assert report.size_bytes == 600
    assert report.seconds >= 0
    assert report.summary() == f"3 done, 1 failed in {report.seconds:.1f}s, 600 B"


def test_a_plan_without_a_batch_chain_has_no_report(tmp_path: Path) -> None:
    agent, _, _ = _agent(tmp_path)
    agent.execute_plan(
        _plan(("convert", ["*.mov"], "c.mp4")), utterance="convert all the movs", dry_run=True
    )
    assert agent.last_batch_report is None


# ── the Python CLI ───────────────────────────────────────────────────────────────────────────


def test_the_cli_prints_failures_and_the_summary_on_stderr_and_exits_1(
    monkeypatch, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from knaif import app
    from knaif.batch_chain import BatchReport

    def fake_execute(self, payload, **kw):
        self.batch_failed("✗ b.mov: broken")
        self.last_batch_report = BatchReport(
            files=["a.mov", "b.mov"], failures=[("b.mov", "broken")], seconds=1.0, size_bytes=2048
        )
        return [{"tool": "convert_video", "args": {}, "result": {}, "duration_ms": 1.0}]

    monkeypatch.setattr("knaif.agent.CommandAgent.execute_plan", fake_execute)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app.cli, ["run", "ffmpeg", "--backend", "mock", "--dry-run", "trim", "clip.mp4"]
    )
    assert result.exit_code == 1, result.output
    assert "✗ b.mov: broken" in result.stderr
    assert "1 done, 1 failed in 1.0s, 2.0 KB" in result.stderr
    assert "1 done" not in result.stdout


# ── one step over many files (a batch without a lift) ────────────────────────────────────────


def test_a_single_batch_step_goes_on_past_a_failed_file(tmp_path: Path) -> None:
    agent, seen, sandbox = _failing(tmp_path, "clip2_broken.mov")
    files = [str(sandbox / n) for n in ("clip1.mov", "clip2_broken.mov", "clip10.mov")]
    plan = _plan(("batchconvert", files, None), ("convert", ["clip1.mov"], "z.mp4"))
    agent.execute_plan(plan, utterance="convert all of these, then clip1.mov", dry_run=True)
    assert [a for t, a in seen if t == "failed"] == [
        "✗ clip2_broken.mov: bad data in clip2_broken.mov"
    ]
    assert [t for t, _ in seen if t != "failed"] == ["batchconvert", "convert"]
    report = agent.last_batch_report
    assert (report.done, report.failed) == (2, 1)
    assert report.failures == [("clip2_broken.mov", "bad data in clip2_broken.mov")]


def test_a_single_batch_step_where_every_file_failed_stops_the_run(tmp_path: Path) -> None:
    agent, seen, sandbox = _failing(tmp_path, "a_broken.mov", "b_broken.mov")
    plan = _plan(
        ("batchconvert", ["a_broken.mov", "b_broken.mov"], None),
        ("convert", ["clip1.mov"], "z.mp4"),
    )
    agent.execute_plan(
        plan, utterance="convert a_broken.mov and b_broken.mov, then clip1.mov", dry_run=True
    )
    assert [t for t, _ in seen if t != "failed"] == ["batchconvert"]
    assert len([t for t, _ in seen if t == "failed"]) == 2
    assert (agent.last_batch_report.done, agent.last_batch_report.failed) == (0, 2)


def test_one_file_is_no_batch(tmp_path: Path) -> None:
    """A one-file step keeps today's error path: no failure line, no report, the run stops."""
    agent, seen, _ = _failing(tmp_path, "one_broken.mov")
    plan = _plan(("batchconvert", ["one_broken.mov"], None), ("convert", ["clip1.mov"], "z.mp4"))
    agent.execute_plan(plan, utterance="convert one_broken.mov, then clip1.mov", dry_run=True)
    assert [t for t, _ in seen] == ["batchconvert"]
    assert agent.last_batch_report is None


def test_a_lifted_chain_reports_each_file_once(tmp_path: Path) -> None:
    """Inside a lifted chain the lift reports the file; the step's own reports are not doubled."""
    agent, seen, _ = _failing(tmp_path, "clip3_broken.mov")
    plan = _plan(("batchconvert", ["*.mov"], "c.mp4"), ("shrink", ["c.mp4"], "s.mp4"))
    agent.execute_plan(plan, utterance=_UTTERANCE, dry_run=True)
    assert [a for t, a in seen if t == "failed"] == [
        "✗ clip3_broken.mov: bad data in clip3_broken.mov"
    ]
    assert (agent.last_batch_report.done, agent.last_batch_report.failed) == (3, 1)


def test_a_reason_never_shows_the_folder_the_run_works_in(tmp_path: Path) -> None:
    agent, seen, sandbox = _failing(tmp_path, "clip2_broken.mov")
    from knaif.agent import _FileTally

    lines: list[str] = []
    tally = _FileTally(lines.append, sandbox)
    tally.record(str(sandbox / "clip1.mov"), None)
    tally.record(str(sandbox / "clip2_broken.mov"), f"cannot open {sandbox / 'clip2_broken.mov'}")
    assert lines == ["✗ clip2_broken.mov: cannot open clip2_broken.mov"]


# ── the preview: what the plan leaves, and a batch shown once ────────────────────────────────


def _previewing(tmp_path: Path, *, approve: bool | None = None):
    agent, seen, sandbox = _agent(tmp_path, scratch=True)
    shown: list[str] = []
    asked: list[str] = []
    agent.show_plan = True
    agent.plan_display = shown.append
    if approve is not None:
        agent.require_approval = True

        def confirm(clause: str) -> bool:
            asked.append(clause)
            return approve

        agent.plan_confirmer = confirm
    return agent, seen, shown, asked


def test_a_single_file_preview_ends_with_what_it_leaves(tmp_path: Path) -> None:
    agent, _, shown, _ = _previewing(tmp_path)
    plan = _plan(("convert", ["clip1.mov"], "c.mp4"), ("shrink", ["c.mp4"], "s.mp4"))
    agent.execute_plan(plan, utterance="convert clip1.mov and shrink it", dry_run=True)
    assert len(shown) == 3
    assert shown[-1] == "leaves s.mp4"
    assert [r.name for r in agent.last_result_set] == ["s.mp4"]


def test_a_batch_previews_the_first_file_then_the_rest(tmp_path: Path) -> None:
    agent, seen, shown, _ = _previewing(tmp_path)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=True)
    assert len(shown) == 4
    assert "clip1.mov" in shown[0] and "clip2" not in " ".join(shown[:2])
    assert shown[2:] == [
        "the same for 2 more files: clip2.mov, clip10.mov",
        "leaves clip1_s.mp4, clip2_s.mp4 and clip10_s.mp4",
    ]
    assert len([t for t, _ in seen if t != "failed"]) == 6


def test_a_batch_is_approved_once(tmp_path: Path) -> None:
    agent, seen, _, asked = _previewing(tmp_path, approve=True)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=True)
    assert len(asked) == 1
    assert len(seen) == 6


def test_a_declined_batch_runs_nothing(tmp_path: Path) -> None:
    agent, seen, _, asked = _previewing(tmp_path, approve=False)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance=_UTTERANCE, dry_run=True)
    assert len(asked) == 1
    assert seen == []
