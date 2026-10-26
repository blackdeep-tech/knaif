"""knaif.alert: the completion alert itself — when a run ends, a desktop notification and a beep.

- It fires only when the plan asked for one, the run was not a dry run, and work started; then
  on success, on failure, and when a confirmation was declined mid-run.
- `KNAIF_OS_SINK=<file>` records each alert as a JSON line instead of making it (tests, evals).
- It never writes to stdout (the eval lanes parse it), never runs a shell, and never puts the
  message into script source: PowerShell reads it from the environment, osascript and
  notify-send take it as argv.
- It never fails a run: a backend that is missing or errors falls back, at worst to the
  terminal bell on stderr.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from knaif.alert import Alert, alert_for, fire, run_outcome

# ── run_outcome ──────────────────────────────────────────────────────────────


def _r(tool: str, result: Any = None) -> dict:
    return {"tool": tool, "args": {}, "result": result if result is not None else {}}


def test_no_work_is_no_outcome():
    assert run_outcome([_r("clarify", {"status": "clarification_needed"})], None, False) is None


def test_done():
    assert run_outcome([_r("run_batch", {"outputs": [{"returncode": 0}]})], None, True) == "done"


def test_an_exception_after_work_started_is_failed():
    assert run_outcome([], ValueError("boom"), True) == "failed"


def test_an_exception_before_work_started_is_no_outcome():
    """Refused before anything ran (a bad path): the user is reading the error already."""
    assert run_outcome([], ValueError("outside the sandbox"), False) is None


@pytest.mark.parametrize(
    "result",
    [{"returncode": 1}, {"outputs": [{"returncode": 0}, {"returncode": 2}]}, {"error": "bad pdf"}],
)
def test_a_failed_step_is_failed(result):
    assert run_outcome([_r("x", result)], None, True) == "failed"


def test_a_declined_confirmation_after_work_is_stopped():
    results = [
        _r("run_preview", {"returncode": 0}),
        _r("wait_for_confirmation", {"status": "declined"}),
    ]
    assert run_outcome(results, None, True) == "stopped"


# ── the text ─────────────────────────────────────────────────────────────────


def test_alert_for_done():
    a = alert_for("done", request="convert clip.mov to mp4 and beep when it's done", seconds=12.4)
    assert a == Alert(
        outcome="done",
        title="knaif",
        message="Done (12 s): convert clip.mov to mp4 and beep when it's done",
    )


def test_alert_for_failed_names_the_reason():
    a = alert_for("failed", request="x", seconds=1.0, reason="ffmpeg exited with 1")
    assert a.message == "Failed: ffmpeg exited with 1"


def test_alert_for_stopped():
    assert alert_for("stopped", request="x", seconds=1.0).message.startswith("Stopped")


def test_alert_text_is_short():
    a = alert_for("done", request="word " * 100, seconds=3.0)
    assert len(a.message) <= 120
    assert a.message.endswith("…")


def test_no_request_still_says_done():
    assert alert_for("done", request=None, seconds=2.0).message == "Done (2 s)"


# ── fire: the sink ───────────────────────────────────────────────────────────


def _alert() -> Alert:
    return Alert(outcome="done", title="knaif", message='Done (1 s): a "quoted" & <odd> one')


def test_the_sink_records_instead_of_alerting(tmp_path: Path):
    sink = tmp_path / "sink.jsonl"
    calls: list = []
    used = fire(_alert(), env={"KNAIF_OS_SINK": str(sink)}, run=lambda *a, **k: calls.append(a))
    assert used == "sink"
    assert calls == []
    [line] = sink.read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == {
        "kind": "alert",
        "outcome": "done",
        "title": "knaif",
        "message": 'Done (1 s): a "quoted" & <odd> one',
        "sound": True,
    }


def test_the_sink_appends(tmp_path: Path):
    sink = tmp_path / "sink.jsonl"
    fire(_alert(), env={"KNAIF_OS_SINK": str(sink)})
    fire(_alert(), env={"KNAIF_OS_SINK": str(sink)})
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 2


def test_fire_never_writes_to_stdout(tmp_path: Path, capsys):
    fire(_alert(), env={"KNAIF_OS_SINK": str(tmp_path / "s.jsonl")})
    fire(_alert(), env={}, platform="linux", which=lambda _: None, stderr=io.StringIO())
    assert capsys.readouterr().out == ""


# ── fire: the backends ───────────────────────────────────────────────────────


class _Run:
    def __init__(self, fail: set[str] | None = None):
        self.calls: list[tuple[list[str], dict]] = []
        self.fail = fail or set()

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if any(x in argv[0] for x in self.fail):
            raise OSError("not there")

        class _P:
            returncode = 0

        return _P()


def test_windows_toast_and_beep():
    run, beeps = _Run(), []
    used = fire(_alert(), env={}, platform="win32", run=run, beep=lambda: beeps.append(1))
    assert used == "windows-toast"
    assert beeps == [1]
    argv, kw = run.calls[0]
    assert argv[0].lower().endswith("powershell.exe") or argv[0].lower() == "powershell"
    assert "-NonInteractive" in argv
    # The message travels in the environment, never in the script text.
    assert _alert().message not in " ".join(argv)
    assert kw["env"]["KNAIF_ALERT_MESSAGE"] == _alert().message
    assert kw["env"]["KNAIF_ALERT_TITLE"] == "knaif"
    assert kw.get("shell") in (None, False)


def test_windows_uses_knaifs_app_id_once_registered():
    """The installer registers knaif's app id; then the banner says knaif, not PowerShell."""
    run = _Run()
    fire(_alert(), env={}, platform="win32", run=run, beep=lambda: None, app_id=lambda: "knaif.cli")
    _, kw = run.calls[0]
    assert kw["env"]["KNAIF_ALERT_APP_ID"] == "knaif.cli"


def test_windows_borrows_powershells_app_id_until_then():
    run = _Run()
    fire(_alert(), env={}, platform="win32", run=run, beep=lambda: None, app_id=lambda: None)
    _, kw = run.calls[0]
    assert "KNAIF_ALERT_APP_ID" not in kw["env"]


def test_windows_falls_back_to_the_bell_when_powershell_fails():
    err = io.StringIO()
    run = _Run(fail={"powershell"})
    used = fire(_alert(), env={}, platform="win32", run=run, beep=lambda: None, stderr=err)
    assert used == "bell"
    assert err.getvalue() == "\a"


def test_macos_osascript_takes_the_text_as_argv():
    run = _Run()
    used = fire(_alert(), env={}, platform="darwin", run=run)
    assert used == "macos"
    argv, _ = run.calls[0]
    assert argv[0] == "osascript"
    assert argv[-2:] == ["knaif", _alert().message]
    script = " ".join(argv[:-2])
    assert _alert().message not in script


def test_linux_notify_send_with_a_desktop():
    run = _Run()
    which = {"notify-send": "/usr/bin/notify-send"}.get
    used = fire(
        _alert(),
        env={"DISPLAY": ":0"},
        platform="linux",
        run=run,
        which=which,
        stderr=io.StringIO(),
    )
    assert used == "notify-send"
    argv, _ = run.calls[0]
    assert argv == ["/usr/bin/notify-send", "knaif", _alert().message]


@pytest.mark.parametrize("env", [{}, {"DISPLAY": ":0", "SSH_CONNECTION": "1 2 3 4"}])
def test_linux_without_a_desktop_rings_the_bell_only(env):
    err, run = io.StringIO(), _Run()
    which = {"notify-send": "/usr/bin/notify-send"}.get
    used = fire(_alert(), env=env, platform="linux", run=run, which=which, stderr=err)
    assert used == "bell"
    assert run.calls == []
    assert err.getvalue() == "\a"


def test_a_backend_error_never_escapes():
    def boom(*a, **k):
        raise RuntimeError("anything")

    used = fire(_alert(), env={}, platform="darwin", run=boom, stderr=io.StringIO())
    assert used == "bell"


# ── the agent fires it when the run ends ─────────────────────────────────────


def _agent(tmp_path: Path):
    from knaif import CommandAgent

    return CommandAgent.from_skill(
        Path(__file__).resolve().parents[3] / "skills" / "documents", sandbox=str(tmp_path)
    )


def _sample(tmp_path: Path) -> Path:
    src = Path(__file__).resolve().parents[3] / "sandbox" / "fixtures" / "documents" / "sample.pdf"
    if not src.exists():
        pytest.skip("documents fixtures not generated")
    dst = tmp_path / "sample.pdf"
    dst.write_bytes(src.read_bytes())
    return dst


def _plan(pdf: Path, alert: bool) -> dict:
    plan = [{"tool": "inspect_document", "args": {"input": str(pdf)}}]
    if alert:
        plan.append({"tool": "alert_when_done", "args": {}})
    return {"plan": plan}


def test_a_real_run_that_asked_fires_once(tmp_path, monkeypatch):
    sink = tmp_path / "sink.jsonl"
    monkeypatch.setenv("KNAIF_OS_SINK", str(sink))
    agent = _agent(tmp_path)
    agent.execute_plan(
        _plan(_sample(tmp_path), True),
        dry_run=False,
        confirmed=True,
        utterance="inspect sample.pdf and beep",
    )
    [line] = sink.read_text(encoding="utf-8").splitlines()
    rec = json.loads(line)
    assert rec["outcome"] == "done"
    assert rec["message"].startswith("Done (")


def test_a_dry_run_never_fires(tmp_path, monkeypatch):
    sink = tmp_path / "sink.jsonl"
    monkeypatch.setenv("KNAIF_OS_SINK", str(sink))
    _agent(tmp_path).execute_plan(_plan(_sample(tmp_path), True), dry_run=True)
    assert not sink.exists()


def test_no_alert_asked_never_fires(tmp_path, monkeypatch):
    sink = tmp_path / "sink.jsonl"
    monkeypatch.setenv("KNAIF_OS_SINK", str(sink))
    _agent(tmp_path).execute_plan(_plan(_sample(tmp_path), False), dry_run=False, confirmed=True)
    assert not sink.exists()


def test_a_failure_after_work_started_fires_failed(tmp_path, monkeypatch):
    sink = tmp_path / "sink.jsonl"
    monkeypatch.setenv("KNAIF_OS_SINK", str(sink))
    agent = _agent(tmp_path)
    pdf = _sample(tmp_path)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really")
    plan = {
        "plan": [
            {"tool": "inspect_document", "args": {"input": str(pdf)}},
            {"tool": "inspect_document", "args": {"input": str(broken)}},
            {"tool": "alert_when_done", "args": {}},
        ]
    }
    try:
        agent.execute_plan(plan, dry_run=False, confirmed=True)
    except Exception:  # noqa: BLE001 - either way the alert must say "failed"
        pass
    [line] = sink.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["outcome"] == "failed"


def test_an_intent_declined_mid_run_fires_stopped(tmp_path, monkeypatch):
    sink = tmp_path / "sink.jsonl"
    monkeypatch.setenv("KNAIF_OS_SINK", str(sink))
    agent = _agent(tmp_path)
    answers = iter([True, False])
    agent.plan_confirmer = lambda clause: next(answers)
    agent.plan_display = lambda clause: None
    pdf = _sample(tmp_path)
    plan = {
        "plan": [
            {"tool": "inspect_document", "args": {"input": str(pdf)}},
            {"tool": "inspect_document", "args": {"input": str(pdf)}},
            {"tool": "alert_when_done", "args": {}},
        ]
    }
    agent.execute_plan(plan, dry_run=False, confirmed=True, require_approval=True)
    [line] = sink.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["outcome"] == "stopped"


def test_a_pre_run_clarify_never_fires(tmp_path, monkeypatch):
    sink = tmp_path / "sink.jsonl"
    monkeypatch.setenv("KNAIF_OS_SINK", str(sink))
    plan = {
        "plan": [
            {"tool": "clarify", "args": {"question": "Which file?"}},
            {"tool": "alert_when_done", "args": {}},
        ]
    }
    _agent(tmp_path).execute_plan(plan, dry_run=False, confirmed=True)
    assert not sink.exists()
