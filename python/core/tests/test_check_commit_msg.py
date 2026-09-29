"""Tests for scripts/check_commit_msg.py's `--range` mode (release-1.2 plan R1).

With merge commits, an integration branch's commits land on `main` unchanged, so CI has to lint
every commit of a PR, not only its title. The range mode is what the `pr-title` job runs.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "check_commit_msg.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def _load():
    spec = importlib.util.spec_from_file_location("check_commit_msg", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ccm = _load()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "commit", "--allow-empty", "--no-verify", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _commit(tmp_path, "chore: root")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_range_of_conforming_commits_passes(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "feat(cli): add a flag")
    head = _commit(repo, "docs: explain the flag")
    assert ccm.main(["--range", f"{base}..{head}"]) == 0


def test_one_bad_commit_in_the_middle_fails_and_is_named(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The title can be fine while a commit under it is not; that commit is what lands on main."""
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "feat(cli): add a flag")
    bad = _commit(repo, "wip stuff")
    head = _commit(repo, "fix(cli): handle the empty case")

    assert ccm.main(["--range", f"{base}..{head}"]) == 1
    err = capsys.readouterr().err
    assert bad[:12] in err
    assert "wip stuff" in err


def test_the_base_commit_itself_is_not_checked(repo: Path) -> None:
    """`base..head` excludes base: a legacy commit already on main is not the PR's business."""
    base = _commit(repo, "not conventional at all")
    head = _commit(repo, "feat: fine")
    assert ccm.main(["--range", f"{base}..{head}"]) == 0


def test_merge_commits_in_the_range_are_skipped(repo: Path) -> None:
    """Taking in main's changes by merging (the release-branch rule) must not fail the PR."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "fix: on the side")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "feat: on main")
    _git(repo, "merge", "--no-ff", "--no-verify", "-q", "-m", "take in side, custom text", "side")
    head = _git(repo, "rev-parse", "HEAD")
    assert ccm.main(["--range", f"{base}..{head}"]) == 0


def test_an_unknown_revision_is_an_error_not_a_pass(repo: Path) -> None:
    """A shallow checkout that lacks the base must fail loudly, never lint zero commits."""
    assert ccm.main(["--range", "deadbeefdeadbeef..HEAD"]) == 1


def test_the_message_file_mode_still_works(tmp_path: Path) -> None:
    path = tmp_path / "msg.txt"
    path.write_text("feat(core): something\n", encoding="utf-8")
    assert ccm.main([str(path)]) == 0
    path.write_text("something\n", encoding="utf-8")
    assert ccm.main([str(path)]) == 1


def test_the_pr_title_job_lints_every_commit_of_the_pr() -> None:
    """The job must fetch full history and pass the PR's own base and head through env."""
    text = WORKFLOW.read_text(encoding="utf-8")
    job = text.split("\n  pr-title:\n", 1)[1].split("\n\n  #", 1)[0]
    assert "fetch-depth: 0" in job
    assert "github.event.pull_request.base.sha" in job
    assert "github.event.pull_request.head.sha" in job
    assert "--range" in job
    # Never interpolate `${{ }}` into the run body (script injection).
    run_bodies = re.findall(r"run: \|\n((?:          .*\n?)+)", job)
    assert run_bodies
    assert all("${{" not in body for body in run_bodies)
