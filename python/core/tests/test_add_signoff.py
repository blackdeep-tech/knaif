"""scripts/add_signoff.py, the prepare-commit-msg hook: every commit made here carries a DCO sign-off."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "add_signoff.py"
NAME, EMAIL = "Alice Example", "1+alice@users.noreply.github.com"
TRAILER = f"Signed-off-by: {NAME} <{EMAIL}>"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", NAME], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", EMAIL], check=True)
    return tmp_path


def _hook(repo: Path, message: str, *args: str) -> str:
    msg = repo / "COMMIT_EDITMSG"
    msg.write_text(message, encoding="utf-8")
    subprocess.run([sys.executable, str(SCRIPT), str(msg), *args], cwd=repo, check=True)
    return msg.read_text(encoding="utf-8")


def test_a_message_gets_the_committers_sign_off(repo: Path) -> None:
    out = _hook(repo, "fix(evals): a thing\n\nWhy it changed.\n", "message")
    assert out.rstrip().endswith(TRAILER)
    assert out.startswith("fix(evals): a thing\n\nWhy it changed.\n")


def test_a_subject_alone_gets_a_blank_line_before_the_sign_off(repo: Path) -> None:
    out = _hook(repo, "docs(todo): one line\n", "message")
    assert out.splitlines()[:3] == ["docs(todo): one line", "", TRAILER]


def test_a_message_already_signed_off_is_left_alone(repo: Path) -> None:
    signed = f"fix(evals): a thing\n\n{TRAILER}\n"
    assert _hook(repo, signed, "message").count("Signed-off-by:") == 1


def test_another_persons_sign_off_is_kept_and_ours_added(repo: Path) -> None:
    other = "Signed-off-by: Bob <2+bob@users.noreply.github.com>"
    out = _hook(repo, f"eval(release): theirs\n\n{other}\n", "commit", "HEAD")
    assert other in out and TRAILER in out


def test_the_editor_template_keeps_its_comments_after_the_sign_off(repo: Path) -> None:
    out = _hook(repo, "feat: x\n\n# Please enter the commit message\n# Lines starting with '#'\n")
    lines = out.splitlines()
    assert TRAILER in lines
    assert lines.index(TRAILER) < lines.index("# Please enter the commit message")


def test_without_a_git_identity_nothing_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    env_free = tmp_path / "COMMIT_EDITMSG"
    env_free.write_text("feat: x\n", encoding="utf-8")
    # No identity in this repo; a global one may exist, so only check the hook never fails.
    subprocess.run(
        [sys.executable, str(SCRIPT), str(env_free)],
        cwd=tmp_path,
        check=True,
        env={
            "GIT_CONFIG_GLOBAL": str(tmp_path / "none"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": os.environ["PATH"],
        },
    )
    assert env_free.read_text(encoding="utf-8") == "feat: x\n"
