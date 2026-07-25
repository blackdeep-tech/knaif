"""Tests for the shared notebook helper modules."""

from __future__ import annotations

import importlib.util
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
    root = Path(".")
    helper_path = (
        root / "skills" / "documents" / "notebooks" / "helpers" / "documents_corpus_review.py"
    )
    expected_eval_dir = root / "skills" / "documents" / "eval"

    spec = importlib.util.spec_from_file_location("documents_corpus_review_test", helper_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

    assert Path(module._SKILL_EVAL).resolve() == expected_eval_dir.resolve()
