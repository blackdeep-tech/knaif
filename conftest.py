"""Repo-wide pytest setup, loaded before either test tree (python/core/tests, skills/).

Keep the suite away from the repository a git hook was started from. In a linked worktree git
hands every hook an absolute GIT_DIR (the commit hooks also GIT_INDEX_FILE) pointing into the main
checkout's .git, and the pre-push hook runs this suite. Those variables outrank `git -C <dir>`, so a
test that builds a throwaway repository under tmp_path would build it inside the real one — on
2026-10-02 one re-initialised it as bare and committed fixtures onto its branches. See
python/core/tests/test_git_env_isolation.py.

Cleared at import, not in a fixture, so git run while collecting a module is covered too.
"""

from __future__ import annotations

import os

# `git rev-parse --local-env-vars`: the variables that bind a git process to one repository.
# test_git_env_isolation.py fails if a newer git reports one this list does not have.
GIT_REPO_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)

for _name in GIT_REPO_ENV_VARS:
    os.environ.pop(_name, None)
