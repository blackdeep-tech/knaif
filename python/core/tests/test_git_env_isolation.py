"""The suite must never act on the repository a git hook was started from.

Git hands every hook in a LINKED WORKTREE an absolute `GIT_DIR` (and the commit hooks also
`GIT_INDEX_FILE`) pointing into the main checkout's `.git`. The pre-push hook runs this suite, and
a variable like that outranks `git -C <tmp_path>`, so a test that builds a throwaway repository
builds it inside the real one instead. On 2026-10-02 a push from a worktree did exactly that:
test_check_commit_msg.py's `git init` re-initialised the real repository as bare, set its
user.name/user.email to the test's, and committed its fixtures onto the checked-out branch and
`main`. A push from the main checkout gets no `GIT_DIR`, which is why nobody had seen it.

The root conftest.py removes those variables before any test runs.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


def _load_root_conftest():
    spec = importlib.util.spec_from_file_location("_root_conftest", REPO / "conftest.py")
    assert spec and spec.loader, "the repo-root conftest.py is missing"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _snapshot(repo: Path) -> tuple[str, str, str]:
    """Everything the 2026-10-02 run changed: the config, the branches, and HEAD."""
    config = (repo / ".git" / "config").read_text(encoding="utf-8")
    refs = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    head = (repo / ".git" / "HEAD").read_text(encoding="utf-8")
    return config, refs, head


@pytest.fixture
def sentinel(tmp_path: Path) -> Path:
    if not shutil.which("git"):
        pytest.skip("no git")
    repo = tmp_path / "sentinel"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "work")
    _git(
        repo,
        "-c",
        "user.name=Sentinel",
        "-c",
        "user.email=sentinel@example.invalid",
        "commit",
        "--allow-empty",
        "-q",
        "-m",
        "chore: sentinel",
    )
    return repo


def test_a_hook_s_git_dir_does_not_reach_the_tests(sentinel: Path) -> None:
    before = _snapshot(sentinel)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["GIT_DIR"] = str(sentinel / ".git")
    env["GIT_INDEX_FILE"] = str(sentinel / ".git" / "index")

    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "python/core/tests/test_check_commit_msg.py",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert _snapshot(sentinel) == before, "the suite wrote into the repository named by GIT_DIR"
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]


def test_every_variable_that_binds_git_to_a_repository_is_cleared() -> None:
    if not shutil.which("git"):
        pytest.skip("no git")
    reported = set(
        subprocess.run(
            ["git", "rev-parse", "--local-env-vars"], check=True, capture_output=True, text=True
        ).stdout.split()
    )
    missing = reported - set(_load_root_conftest().GIT_REPO_ENV_VARS)
    assert not missing, f"conftest.py does not clear {sorted(missing)}"
