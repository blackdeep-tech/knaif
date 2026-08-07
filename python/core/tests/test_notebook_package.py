"""Tests for the shared notebook helper modules."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("ipywidgets")


def test_notebooks_shared_exports_public_helpers():
    shared = Path("notebooks") / "shared"
    sys.path.insert(0, str(shared))
    try:
        from baseline_reviewer import make_reviewer
        from debug_trace import show_debug
        from model_selector import ModelSelector
        from tester_widget import FFmpegNLTester, TesterWidget
    finally:
        sys.path.remove(str(shared))

    assert TesterWidget is not None
    assert TesterWidget.__new__ is object.__new__
    assert FFmpegNLTester is not None
    assert ModelSelector is not None
    assert make_reviewer is not None
    assert show_debug is not None


def test_documents_tester_widget_is_skill_local():
    root = Path(".")

    assert not (root / "notebooks" / "shared" / "documents_tester_widget.py").exists()
    assert (
        root / "skills" / "documents" / "notebooks" / "helpers" / "documents_tester_widget.py"
    ).exists()


def test_tester_widget_loads_documents_skill_local_helper(tmp_path):
    shared = Path("notebooks") / "shared"
    sys.path.insert(0, str(shared))
    try:
        from tester_widget import TesterWidget

        widget = TesterWidget(
            skill="documents",
            skill_dir=Path("skills") / "documents",
            fixtures_dir=tmp_path,
            model_configs={},
            ollama_url="http://localhost:11434",
            root=Path("."),
        )
    finally:
        sys.path.remove(str(shared))

    assert type(widget).__name__ == "TesterWidget"
    assert widget.skill == "documents"
    assert type(widget._delegate).__name__ == "DocumentsNLTester"


def test_documents_corpus_review_finds_skill_eval_helpers():
    """Import the helper the way its own docstring says a notebook does.

    This used to `exec_module` the file under a synthetic module name, and CI on Python
    3.14.7 failed inside the helper's `@dataclass` with `AttributeError: 'NoneType' object
    has no attribute '__dict__'`. The mechanism is CPython's `dataclasses._is_type`, which
    resolves a string annotation via an **unguarded** `sys.modules.get(cls.__module__)
    .__dict__` — the identical lookup in `_process_class` *is* guarded. A dataclass whose
    defining module is not in `sys.modules` therefore raises before any assertion here can
    run. Reproduced directly on 3.10 and 3.14: unregistered fails, registered succeeds. It
    is not a version-specific bug, which is why the fix is not a version guard.

    What is *not* established is why 3.14.7 tripped it when this test did register the
    module — that could not be reproduced locally, and CI is the only environment with that
    interpreter. Hence the fix is to stop depending on that bookkeeping at all rather than
    to patch around a mechanism only half understood.

    Importing by name is what a normal import does for you, and it exercises the path that
    actually ships: the helper documents `from documents_corpus_review import
    review_corpus, render_review`. `test_tester_widget_loads_documents_skill_local_helper`
    above already does exactly this for its sibling.
    """
    helpers = Path("skills") / "documents" / "notebooks" / "helpers"
    expected_eval_dir = Path("skills") / "documents" / "eval"

    sys.path.insert(0, str(helpers))
    try:
        import documents_corpus_review
    finally:
        sys.path.remove(str(helpers))
        sys.modules.pop("documents_corpus_review", None)

    assert Path(documents_corpus_review._SKILL_EVAL).resolve() == expected_eval_dir.resolve()
