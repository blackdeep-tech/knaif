from __future__ import annotations

from pathlib import Path

import pytest

from knaif import create_agent
from knaif.evalsuite.runner import run_corpus

_EVAL_JSONL = Path(__file__).parents[2] / "data" / "eval.jsonl"

_VALID_FIXTURES = frozenset(
    {
        "sample.pdf",
        "sample-protected.pdf",
        "sample-scanned.pdf",
        "sample.docx",
        "sample.pptx",
        "sample.xlsx",
        "sample.png",
        "sample.txt",
        "sample.md",
    }
)

_VALID_TOOLS = frozenset(
    {
        "inspect_document",
        "extract_text",
        "find_in_document",
        "merge_pdfs",
        "split_pdf",
        "rotate_pages",
        "remove_pages",
        "reorder_pages",
        "watermark",
        "add_page_numbers",
        "protect_pdf",
        "unlock_pdf",
        "convert_document",
        "compress_pdf",
        "ocr_document",
        "clarify",
        "reject",
    }
)


@pytest.fixture(scope="module")
def corpus():
    from knaif.evalsuite.corpus import load_corpus

    return load_corpus(_EVAL_JSONL)


def test_documents_eval_corpus_loads(corpus):
    assert len(corpus) >= 20


def test_documents_eval_ids_are_unique(corpus):
    ids = [row.id for row in corpus]
    assert len(ids) == len(set(ids))


def test_documents_plan_rows_have_expected_tools(corpus):
    missing = [row.id for row in corpus if row.expected_outcome == "plan" and not row.expected_tool]
    assert not missing


def test_documents_expected_tools_are_known(corpus):
    unknown = [
        (row.id, row.expected_tool)
        for row in corpus
        if row.expected_tool and row.expected_tool not in _VALID_TOOLS
    ]
    assert not unknown


def test_documents_eval_fixtures_are_known(corpus):
    unknown = [
        (row.id, row.fixture)
        for row in corpus
        if row.fixture and row.fixture not in _VALID_FIXTURES
    ]
    assert not unknown


def test_documents_plan_rows_have_success_criteria(corpus):
    missing = [
        row.id
        for row in corpus
        if row.expected_outcome == "plan"
        and row.expected_tool not in {"compress_pdf"}
        and not row.success_criteria
    ]
    assert not missing


def test_mock_eval_routes_first_row(tmp_path: Path, corpus):
    from skills.documents.eval.fixtures import generate_documents_fixtures

    fixture_dir = tmp_path / "fixtures"
    generate_documents_fixtures(fixture_dir)
    agent = create_agent("documents", sandbox=fixture_dir)

    outputs = run_corpus(agent, corpus[:1], use_mock=True, apply_retrieval=True)

    assert [output.outcome for output in outputs] == ["plan"]
