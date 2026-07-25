from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from knaif import create_agent, list_skills
from knaif.agent import CommandAgent
from knaif.handler_api import HandlerContext
from knaif.skill import Skill
from knaif.tool import Intent, Step

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]
OPTIONAL_DOCUMENT_MODULES = (
    "pikepdf",
    "pypdf",
    "docx",
    "pptx",
    "openpyxl",
    "PIL",
    "reportlab",
)


def _write_text_fixtures(root: Path) -> dict[str, Path]:
    txt = root / "notes.txt"
    txt.write_text("Invoice Alpha\nTotal: 42 EUR\n", encoding="utf-8")

    md = root / "brief.md"
    md.write_text("# Brief\n\nAlpha appears on page one.\n", encoding="utf-8")

    return {"txt": txt, "md": md}


def _execute(
    tool: str, args: dict, sandbox: Path, *, dry_run: bool = True, confirmed: bool = False
):
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=sandbox, root=sandbox)
    return agent.execute_plan(
        {"plan": [{"tool": tool, "args": args}]},
        dry_run=dry_run,
        confirmed=confirmed,
    )[0]["result"]


def _load_fixture_module():
    module_path = DOCUMENTS_SKILL_DIR / "eval" / "fixtures.py"
    spec = importlib.util.spec_from_file_location("_documents_fixture_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_documents_extra():
    for module_name in OPTIONAL_DOCUMENT_MODULES:
        pytest.importorskip(module_name)


def _require_ocr_extra():
    if not shutil.which("tesseract"):
        pytest.skip("tesseract binary is not installed")
    pytest.importorskip("pytesseract")


def _pdf_text_pages(path: Path) -> list[str]:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def _pdf_rotations(path: Path) -> list[int]:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    return [int(page.get("/Rotate", 0) or 0) for page in reader.pages]


def _pdf_is_encrypted(path: Path) -> bool:
    pypdf = pytest.importorskip("pypdf")
    return bool(pypdf.PdfReader(str(path)).is_encrypted)


def _documents_handlers_module():
    Skill.load(DOCUMENTS_SKILL_DIR)
    return sys.modules["_skill_oop_documents_handlers"]


def _handler_context(root: Path) -> HandlerContext:
    return HandlerContext(
        root=root,
        sandbox=root,
        dry_run=False,
        confirmed=True,
        skill_dir=DOCUMENTS_SKILL_DIR,
    )


def _page_has_image_soft_mask(page) -> bool:
    resources = page.get("/Resources") or {}
    xobjects = resources.get("/XObject") or {}
    for value in xobjects.values():
        obj = value.get_object()
        if obj.get("/Subtype") == "/Image" and "/SMask" in obj:
            return True
    return False


def test_skill_loads_documents_manifest():
    skill = Skill.load(DOCUMENTS_SKILL_DIR)
    assert skill.name == "documents"
    assert "document" in skill.description.lower()
    assert skill.tools_yaml_path.exists()


def test_tool_map_contains_phase_one_public_and_internal_tools():
    skill = Skill.load(DOCUMENTS_SKILL_DIR)
    assert skill.tool_map is not None
    for name in (
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
        "run_compress",
        "run_ocr",
        "verify_output",
        "resolve_inputs",
        "clarify",
        "reject",
        "done",
        "wait_for_confirmation",
    ):
        assert name in skill.tool_map


def test_tool_map_uses_step_and_intent_classes():
    skill = Skill.load(DOCUMENTS_SKILL_DIR)
    assert skill.tool_map is not None
    assert isinstance(skill.tool_map["compress_pdf"], Intent)
    assert isinstance(skill.tool_map["ocr_document"], Intent)
    for name, tool in skill.tool_map.items():
        if name in {"compress_pdf", "ocr_document"}:
            continue
        assert isinstance(tool, Step), name


def test_documents_deps_are_a_repo_dependency_group():
    """Skill deps belong to the repo root's [dependency-groups], not the wheel's extras.

    Skill bundles are not packaged into the wheel, so a published ``knaif[documents]``
    extra would offer PyPI users nine PDF libraries for code they do not have.
    Dependency groups (PEP 735) are never published.
    """
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "[dependency-groups]" in text
    assert "documents = [" in text
    for package in (
        "pikepdf",
        "pypdf",
        "pdfminer.six",
        "pypdfium2",
        "python-docx",
        "python-pptx",
        "openpyxl",
        "Pillow",
        "reportlab",
    ):
        assert package in text


def test_documents_ocr_deps_are_a_repo_dependency_group():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "documents-ocr = [" in text
    assert "pytesseract" in text
    assert "ocrmypdf" not in text


def test_skill_deps_are_not_published_as_wheel_extras():
    """The published package must not advertise extras for unshipped skill bundles."""
    published = Path("python/core/pyproject.toml").read_text(encoding="utf-8")
    assert "documents = [" not in published
    assert "documents-ocr = [" not in published
    assert "pikepdf" not in published


def test_list_skills_discovers_documents():
    # io is stale and excluded by default; opt in with include_stale=True.
    assert list_skills() == ["documents", "ffmpeg"]
    assert list_skills(include_stale=True) == ["documents", "ffmpeg", "io"]


def test_create_agent_documents(sandbox: Path):
    agent = create_agent("documents", sandbox=sandbox)
    assert isinstance(agent, CommandAgent)
    assert "inspect_document" in agent.registry


def test_inspect_text_document(sandbox: Path):
    fixtures = _write_text_fixtures(sandbox)

    result = _execute("inspect_document", {"input": str(fixtures["txt"])}, sandbox)

    assert result["format"] == "txt"
    assert result["pages"] == 1
    assert result["encrypted"] is False
    assert result["has_text_layer"] is True
    assert result["size_bytes"] > 0


def test_extract_text_returns_page_records_and_joined_text(sandbox: Path):
    fixtures = _write_text_fixtures(sandbox)

    result = _execute("extract_text", {"input": str(fixtures["md"])}, sandbox)

    assert result["pages"] == [{"page": 1, "text": "# Brief\n\nAlpha appears on page one.\n"}]
    assert "Alpha appears" in result["text"]


def test_find_in_document_defaults_to_case_insensitive_search(sandbox: Path):
    fixtures = _write_text_fixtures(sandbox)

    result = _execute(
        "find_in_document",
        {"input": str(fixtures["txt"]), "query": "alpha"},
        sandbox,
    )

    assert result["count"] == 1
    assert result["matches"][0]["page"] == 1
    assert "Invoice Alpha" in result["matches"][0]["snippet"]
    assert result["matches"][0]["span"][0] < result["matches"][0]["span"][1]


def test_destructive_documents_tools_require_confirmation(sandbox: Path):
    fixtures = _write_text_fixtures(sandbox)
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=sandbox, root=sandbox)

    with pytest.raises(ValueError, match="confirmed=True"):
        agent.execute_plan(
            {
                "plan": [
                    {
                        "tool": "convert_document",
                        "args": {"input": str(fixtures["txt"]), "to_format": "md"},
                    }
                ]
            },
            dry_run=False,
            confirmed=False,
        )


def test_compress_pdf_expands_to_internal_steps():
    skill = Skill.load(DOCUMENTS_SKILL_DIR)
    assert skill.tool_map is not None

    plan = skill.tool_map["compress_pdf"].expand(
        {"input": "report.pdf", "output": "report-small.pdf", "compress_quality": "balanced"}
    )

    assert [step["tool"] for step in plan] == [
        "inspect_document",
        "run_compress",
        "verify_output",
    ]
    assert plan[0]["output"] == "$inspection"
    assert plan[1]["args"]["inspection"] == "$inspection"
    assert plan[1]["output"] == "$compressed"
    assert plan[2]["args"]["compression"] == "$compressed"


def test_ocr_document_expands_to_inspect_and_run_ocr():
    skill = Skill.load(DOCUMENTS_SKILL_DIR)
    assert skill.tool_map is not None

    plan = skill.tool_map["ocr_document"].expand(
        {"input": "scan.pdf", "output": "scan-searchable.pdf", "language": "eng"}
    )

    assert [step["tool"] for step in plan] == ["inspect_document", "run_ocr"]
    assert plan[0]["output"] == "$inspection"
    assert plan[1]["args"] == {
        "input": "scan.pdf",
        "inspection": "$inspection",
        "output": "scan-searchable.pdf",
        "language": "eng",
    }


def test_fixture_generator_creates_text_and_markdown(tmp_path: Path):
    module = _load_fixture_module()

    manifest = module.generate_documents_fixtures(tmp_path)

    assert (tmp_path / "sample.txt").exists()
    assert (tmp_path / "sample.md").exists()
    assert manifest["txt"].name == "sample.txt"
    assert manifest["md"].name == "sample.md"


def test_fixture_generator_creates_full_optional_fixture_set(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()

    manifest = module.generate_documents_fixtures(tmp_path)

    assert set(manifest) >= {"pdf", "scanned_pdf", "docx", "pptx", "xlsx", "png", "txt", "md"}
    for path in manifest.values():
        assert path.exists(), path


def test_inspect_optional_fixture_formats(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)

    expected = {
        "pdf": ("pdf", 3, True),
        "scanned_pdf": ("pdf", 1, False),
        "docx": ("docx", 1, True),
        "pptx": ("pptx", 2, True),
        "xlsx": ("xlsx", 2, True),
        "png": ("png", 1, False),
    }
    for key, (fmt, pages, has_text_layer) in expected.items():
        result = _execute("inspect_document", {"input": str(manifest[key])}, tmp_path)
        assert result["format"] == fmt
        assert result["pages"] == pages
        assert result["encrypted"] is False
        assert result["has_text_layer"] is has_text_layer
        assert result["size_bytes"] > 0


def test_extract_text_optional_fixture_formats(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)

    pdf = _execute("extract_text", {"input": str(manifest["pdf"])}, tmp_path)
    assert [page["page"] for page in pdf["pages"]] == [1, 2, 3]
    assert "Alpha page one" in pdf["pages"][0]["text"]
    assert "Beta page two" in pdf["pages"][1]["text"]
    assert "Gamma page three" in pdf["pages"][2]["text"]

    pdf_subset = _execute("extract_text", {"input": str(manifest["pdf"]), "pages": "2-3"}, tmp_path)
    assert [page["page"] for page in pdf_subset["pages"]] == [2, 3]

    docx = _execute("extract_text", {"input": str(manifest["docx"])}, tmp_path)
    assert "Docx Alpha paragraph" in docx["text"]

    pptx = _execute("extract_text", {"input": str(manifest["pptx"])}, tmp_path)
    assert [page["page"] for page in pptx["pages"]] == [1, 2]
    assert "Slide Alpha" in pptx["pages"][0]["text"]
    assert "Slide Beta" in pptx["pages"][1]["text"]

    xlsx = _execute("extract_text", {"input": str(manifest["xlsx"])}, tmp_path)
    assert [page["page"] for page in xlsx["pages"]] == [1, 2]
    assert "Sheet Alpha" in xlsx["pages"][0]["text"]
    assert "Sheet Beta" in xlsx["pages"][1]["text"]


def test_extract_text_image_requires_tesseract_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()

    monkeypatch.setattr(
        handlers._deps,
        "detect_external_tools",
        lambda: {"gs": None, "soffice": None, "tesseract": None},
    )

    with pytest.raises(handlers.DocumentsDependencyError, match="Install Tesseract"):
        handlers.ExtractTextStep().handle(
            {"input": str(manifest["png"])},
            _handler_context(tmp_path),
        )


def test_extract_text_image_uses_ocr_when_available(tmp_path: Path):
    _require_documents_extra()
    _require_ocr_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)

    result = _execute("extract_text", {"input": str(manifest["png"])}, tmp_path)

    assert result["pages"][0]["page"] == 1
    assert result["text"].strip()


def test_find_in_pdf_reports_matching_page(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)

    result = _execute(
        "find_in_document",
        {"input": str(manifest["pdf"]), "query": "gamma"},
        tmp_path,
    )

    assert result["count"] == 1
    assert result["matches"][0]["page"] == 3
    assert "Gamma page three" in result["matches"][0]["snippet"]


def test_merge_pdfs_executes_and_reports_pages(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    output = tmp_path / "merged.pdf"

    result = _execute(
        "merge_pdfs",
        {"inputs": [str(manifest["pdf"]), str(manifest["pdf"])], "output": str(output)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )

    assert result == {"output": str(output), "pages": 6}
    assert len(_pdf_text_pages(output)) == 6


def test_split_pdf_writes_one_file_per_range(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    output_dir = tmp_path / "splits"

    result = _execute(
        "split_pdf",
        {"input": str(manifest["pdf"]), "ranges": "1-2,3", "output_dir": str(output_dir)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )

    assert result["count"] == 2
    outputs = [Path(path) for path in result["outputs"]]
    assert [len(_pdf_text_pages(path)) for path in outputs] == [2, 1]
    assert "Alpha page one" in _pdf_text_pages(outputs[0])[0]
    assert "Gamma page three" in _pdf_text_pages(outputs[1])[0]


def test_parse_pages_word_grammar():
    handlers = _documents_handlers_module()
    parse = handlers._parse_pages  # noqa: SLF001
    total = 5

    # Relative-position words (case-insensitive), incl. `end` as a `last` synonym.
    assert parse("first", total) == [1]
    assert parse("last", total) == [5]
    assert parse("end", total) == [5]
    assert parse("LAST", total) == [5]
    # Ranges with word endpoints.
    assert parse("3-last", total) == [3, 4, 5]
    assert parse("first-2", total) == [1, 2]
    # Open-ended ranges.
    assert parse("3-", total) == [3, 4, 5]
    assert parse("-2", total) == [1, 2]
    # Mixed multi-part.
    assert parse("first,last", total) == [1, 5]
    assert parse("1,3-last", total) == [1, 3, 4, 5]
    # "all" / "*" mean every page (models emit these for "rotate every page").
    assert parse("all", total) == [1, 2, 3, 4, 5]
    assert parse("*", total) == [1, 2, 3, 4, 5]
    # Unknown words still rejected.
    with pytest.raises(ValueError):
        parse("middle", total)


def test_parse_page_range_specs_accepts_word_endpoints():
    handlers = _documents_handlers_module()
    specs = handlers._parse_page_range_specs("3-last", 5)  # noqa: SLF001
    assert specs == [("3-last", [3, 4, 5])]


def test_split_pdf_single_range_writes_named_output_file(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    new_pdf = tmp_path / "new.pdf"

    result = _execute(
        "split_pdf",
        {"input": str(manifest["pdf"]), "ranges": "1", "output": str(new_pdf)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )

    assert result["count"] == 1
    assert result["outputs"] == [str(new_pdf)]
    # `output` names a single file, not a directory holding a generated file.
    assert new_pdf.is_file()
    assert len(_pdf_text_pages(new_pdf)) == 1
    assert "Alpha page one" in _pdf_text_pages(new_pdf)[0]


def test_reorder_pages_reverse_keyword(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    out = tmp_path / "reversed.pdf"

    result = _execute(
        "reorder_pages",
        {"input": str(manifest["pdf"]), "order": "reverse", "output": str(out)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )

    assert result["pages"] == 3
    pages = _pdf_text_pages(out)
    assert "Gamma page three" in pages[0]  # last page is now first
    assert "Alpha page one" in pages[2]  # first page is now last


def test_missing_required_arg_clarifies_not_errors(tmp_path: Path):
    """A plan that omits a required arg (the model failed to supply it) should
    clarify, not hard-error at validation. Covers gemma's omit-required mode."""
    _require_documents_extra()
    module = _load_fixture_module()
    module.generate_documents_fixtures(tmp_path)
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)

    # protect_pdf without `password` (grounded, required) → clarify
    res = agent.execute_plan(
        {"plan": [{"tool": "protect_pdf", "args": {"input": "sample.pdf"}}]},
        utterance="protect sample.pdf",
        dry_run=True,
    )
    assert res[0]["tool"] == "clarify"

    # rotate_pages without `degrees` (required) → clarify
    res = agent.execute_plan(
        {"plan": [{"tool": "rotate_pages", "args": {"input": "sample.pdf"}}]},
        utterance="rotate sample.pdf",
        dry_run=True,
    )
    assert res[0]["tool"] == "clarify"


def test_reorder_pages_tolerates_padded_order(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    out = tmp_path / "reordered.pdf"

    # The model can't know the page count, so it pads the order with
    # non-existent pages (4-10). The valid prefix "3,1,2" is a correct
    # permutation — drop the out-of-range tail rather than erroring.
    result = _execute(
        "reorder_pages",
        {"input": str(manifest["pdf"]), "order": "3,1,2,4,5,6,7,8,9,10", "output": str(out)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert result["pages"] == 3
    pages = _pdf_text_pages(out)
    assert "Gamma page three" in pages[0]  # page 3 moved to the front


def test_reorder_pages_rejects_all_out_of_range_order(tmp_path: Path):
    # Dropping out-of-range pages must NOT silently produce an empty document:
    # an order with no valid pages is an error.
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()
    with pytest.raises(ValueError, match="no valid pages"):
        handlers.ReorderPagesStep().handle(  # noqa: SLF001
            {"input": str(manifest["pdf"]), "order": "9,10,11"},
            _handler_context(tmp_path),
        )


def test_rotate_remove_and_reorder_pdf_pages(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)

    rotated = tmp_path / "rotated.pdf"
    rotate_result = _execute(
        "rotate_pages",
        {"input": str(manifest["pdf"]), "pages": "2", "degrees": 90, "output": str(rotated)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert rotate_result == {"output": str(rotated), "rotated_pages": [2]}
    assert _pdf_rotations(rotated) == [0, 90, 0]

    removed = tmp_path / "removed.pdf"
    remove_result = _execute(
        "remove_pages",
        {"input": str(manifest["pdf"]), "pages": "2", "output": str(removed)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert remove_result == {"output": str(removed), "pages": 2}
    removed_pages = _pdf_text_pages(removed)
    assert "Alpha page one" in removed_pages[0]
    assert "Gamma page three" in removed_pages[1]

    reordered = tmp_path / "reordered.pdf"
    reorder_result = _execute(
        "reorder_pages",
        {"input": str(manifest["pdf"]), "order": "3,1", "output": str(reordered)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert reorder_result == {"output": str(reordered), "pages": 2}
    reordered_pages = _pdf_text_pages(reordered)
    assert "Gamma page three" in reordered_pages[0]
    assert "Alpha page one" in reordered_pages[1]


def test_protect_and_unlock_pdf(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    protected = tmp_path / "protected.pdf"
    unlocked = tmp_path / "unlocked.pdf"

    protect_result = _execute(
        "protect_pdf",
        {"input": str(manifest["pdf"]), "password": "secret", "output": str(protected)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert protect_result == {"output": str(protected)}
    assert _pdf_is_encrypted(protected) is True

    inspect_result = _execute("inspect_document", {"input": str(protected)}, tmp_path)
    assert inspect_result["encrypted"] is True
    assert inspect_result["pages"] == 0

    unlock_result = _execute(
        "unlock_pdf",
        {"input": str(protected), "password": "secret", "output": str(unlocked)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert unlock_result == {"output": str(unlocked)}
    assert _pdf_is_encrypted(unlocked) is False
    assert "Alpha page one" in _pdf_text_pages(unlocked)[0]


def test_unlock_then_find_chain_threads_to_unlocked_output(tmp_path: Path):
    """End-to-end: ``unlock_pdf`` then ``find_in_document`` where the model points
    the search at the ORIGINAL locked file. The chain linker must forward-thread
    the reused source onto unlock's output so the search runs on the unlocked PDF
    instead of failing on the encrypted original (the reported CLI bug).
    """
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    protected = tmp_path / "sample-protected.pdf"

    _execute(
        "protect_pdf",
        {"input": str(manifest["pdf"]), "password": "secret", "output": str(protected)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert _pdf_is_encrypted(protected) is True

    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    utt = "unlock sample-protected.pdf with pass secret and check if it contains Alpha"
    plan = [
        {"tool": "unlock_pdf", "args": {"input": "sample-protected.pdf", "password": "secret"}},
        {"tool": "find_in_document", "args": {"input": "sample-protected.pdf", "query": "Alpha"}},
    ]
    agent._link_chain_intermediates(plan, utt, agent._output_capable)

    # The search no longer reads the original locked file.
    assert plan[1]["args"]["input"] != "sample-protected.pdf"
    assert plan[1]["args"]["input"] == plan[0]["args"]["output"]

    results = agent.execute_plan({"plan": plan}, utterance=utt, dry_run=False, confirmed=True)
    find_result = results[-1]["result"]
    assert "error" not in find_result
    assert find_result["count"] >= 1


def test_watermark_and_add_page_numbers_embed_overlay_text(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    watermarked = tmp_path / "watermarked.pdf"
    numbered = tmp_path / "numbered.pdf"

    watermark_result = _execute(
        "watermark",
        {
            "input": str(manifest["pdf"]),
            "text": "DRAFT",
            "position": "center",
            "opacity": 0.35,
            "output": str(watermarked),
        },
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert watermark_result == {"output": str(watermarked)}
    assert len(_pdf_text_pages(watermarked)) == 3
    assert "DRAFT" in _pdf_text_pages(watermarked)[0]

    numbers_result = _execute(
        "add_page_numbers",
        {
            "input": str(manifest["pdf"]),
            "position": "bottom-center",
            "start_at": 7,
            "output": str(numbered),
        },
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert numbers_result == {"output": str(numbered)}
    pages = _pdf_text_pages(numbered)
    assert "7" in pages[0]
    assert "8" in pages[1]
    assert "9" in pages[2]


def test_image_watermark_preserves_pdf_pages(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    output = tmp_path / "image-watermarked.pdf"

    result = _execute(
        "watermark",
        {
            "input": str(manifest["pdf"]),
            "image": str(manifest["png"]),
            "position": "bottom-right",
            "opacity": 0.5,
            "output": str(output),
        },
        tmp_path,
        dry_run=False,
        confirmed=True,
    )

    assert result == {"output": str(output)}
    assert _execute("inspect_document", {"input": str(output)}, tmp_path)["pages"] == 3


def test_image_watermark_opacity_creates_soft_mask(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()

    overlay = handlers._make_image_overlay(  # noqa: SLF001
        manifest["png"],
        width=612,
        height=792,
        position="center",
        opacity=0.5,
    )

    assert _page_has_image_soft_mask(overlay)


def test_convert_document_text_markdown_and_pdf_text(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)

    md_output = tmp_path / "notes.md"
    md_result = _execute(
        "convert_document",
        {"input": str(manifest["txt"]), "to_format": "md", "output": str(md_output)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert md_result == {"output": str(md_output)}
    assert "Invoice Alpha" in md_output.read_text(encoding="utf-8")

    txt_output = tmp_path / "report.txt"
    txt_result = _execute(
        "convert_document",
        {"input": str(manifest["pdf"]), "to_format": "txt", "output": str(txt_output)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )
    assert txt_result == {"output": str(txt_output)}
    assert "Gamma page three" in txt_output.read_text(encoding="utf-8")


def test_convert_image_to_pdf(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    output = tmp_path / "image.pdf"

    result = _execute(
        "convert_document",
        {"input": str(manifest["png"]), "to_format": "pdf", "output": str(output)},
        tmp_path,
        dry_run=False,
        confirmed=True,
    )

    assert result == {"output": str(output)}
    assert _execute("inspect_document", {"input": str(output)}, tmp_path)["pages"] == 1


def test_compress_pdf_executes_lossless_workflow(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    output = tmp_path / "compressed.pdf"
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)

    results = agent.execute_plan(
        {
            "plan": [
                {
                    "tool": "compress_pdf",
                    "args": {
                        "input": str(manifest["pdf"]),
                        "compress_quality": "high",
                        "output": str(output),
                    },
                }
            ]
        },
        dry_run=False,
        confirmed=True,
    )

    assert [result["tool"] for result in results] == [
        "inspect_document",
        "run_compress",
        "verify_output",
    ]
    compression = results[1]["result"]
    assert compression["output"] == str(output)
    assert compression["method"] == "lossless-optimize"
    assert compression["text_preserved"] is True
    assert compression["original_size"] > 0
    assert compression["new_size"] > 0
    assert "percent" in compression

    verified = results[2]["result"]
    assert verified["exists"] is True
    assert verified["pages_preserved"] is True
    assert verified["original_pages"] == 3
    assert verified["new_pages"] == 3


def test_run_ocr_dry_run_reports_output(tmp_path: Path):
    fixtures = _write_text_fixtures(tmp_path)
    handlers = _documents_handlers_module()
    output = tmp_path / "searchable.pdf"

    result = handlers.RunOcrStep().handle(
        {
            "input": str(fixtures["txt"]),
            "inspection": {"pages": 1, "has_text_layer": False},
            "language": "eng",
            "output": str(output),
        },
        HandlerContext(
            root=tmp_path,
            sandbox=tmp_path,
            dry_run=True,
            confirmed=False,
            skill_dir=DOCUMENTS_SKILL_DIR,
        ),
    )

    assert result == {
        "mode": "dry_run",
        "input": str(fixtures["txt"]),
        "output": str(output),
        "language": "eng",
        "method": "tesseract-pypdfium2",
        "pages": 1,
        "skipped": False,
    }


def test_run_ocr_copy_skip_reports_copy_method(tmp_path: Path):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()
    output = tmp_path / "already-searchable.pdf"

    result = handlers.RunOcrStep().handle(
        {
            "input": str(manifest["pdf"]),
            "inspection": {"pages": 3, "has_text_layer": True},
            "output": str(output),
        },
        _handler_context(tmp_path),
    )

    assert result["method"] == "copy-existing-text-layer"
    assert result["skipped"] is True
    assert output.read_bytes() == manifest["pdf"].read_bytes()


def test_ocr_document_writes_searchable_pdf_when_available(tmp_path: Path):
    _require_documents_extra()
    _require_ocr_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    output = tmp_path / "searchable.pdf"
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)

    results = agent.execute_plan(
        {
            "plan": [
                {
                    "tool": "ocr_document",
                    "args": {
                        "input": str(manifest["scanned_pdf"]),
                        "output": str(output),
                        "language": "eng",
                    },
                }
            ]
        },
        dry_run=False,
        confirmed=True,
    )

    assert [result["tool"] for result in results] == ["inspect_document", "run_ocr"]
    assert results[1]["result"]["output"] == str(output)
    assert results[1]["result"]["pages"] == 1
    assert output.exists()
    assert _execute("inspect_document", {"input": str(output)}, tmp_path)["has_text_layer"] is True


def test_format_results_surfaces_warning():
    skill = Skill.load(DOCUMENTS_SKILL_DIR)

    formatted = skill.result_formatter(
        [
            {
                "tool": "verify_output",
                "result": {
                    "output": "compressed.pdf",
                    "warning": "Raster compression removes selectable text.",
                },
            }
        ],
        dry_run=False,
    )

    assert {"kind": "info", "message": "Raster compression removes selectable text."} in formatted


def test_run_compress_uses_ghostscript_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()
    output = tmp_path / "gs-compressed.pdf"
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        output.write_bytes(manifest["pdf"].read_bytes())
        return object()

    monkeypatch.setattr(
        handlers._deps,
        "detect_external_tools",
        lambda: {"gs": "gs-test", "soffice": None, "tesseract": None},
    )
    monkeypatch.setattr(handlers._engine.subprocess, "run", fake_run)

    result = handlers.RunCompressStep().handle(
        {
            "input": str(manifest["pdf"]),
            "compress_quality": "balanced",
            "inspection": {"pages": 3},
            "output": str(output),
        },
        _handler_context(tmp_path),
    )

    assert result["method"] == "ghostscript"
    assert result["text_preserved"] is True
    assert result["output"] == str(output)
    assert "-dPDFSETTINGS=/ebook" in commands[0]
    assert f"-sOutputFile={output}" in commands[0]


def test_run_compress_small_without_ghostscript_rasterizes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _require_documents_extra()
    module = _load_fixture_module()
    manifest = module.generate_documents_fixtures(tmp_path)
    handlers = _documents_handlers_module()
    output = tmp_path / "raster-compressed.pdf"

    monkeypatch.setattr(
        handlers._deps,
        "detect_external_tools",
        lambda: {"gs": None, "soffice": None, "tesseract": None},
    )

    result = handlers.RunCompressStep().handle(
        {
            "input": str(manifest["pdf"]),
            "compress_quality": "small",
            "inspection": {"pages": 3},
            "output": str(output),
        },
        _handler_context(tmp_path),
    )

    assert result["method"] == "rasterize-pillow"
    assert result["text_preserved"] is False
    assert result["warning"]
    assert _execute("inspect_document", {"input": str(output)}, tmp_path)["pages"] == 3
    assert "".join(_pdf_text_pages(output)).strip() == ""


def test_protect_pdf_declares_password_grounded():
    from knaif.registry import load_registry

    reg = load_registry(DOCUMENTS_SKILL_DIR / "tools.yaml")
    assert "password" in reg["protect_pdf"].grounded_args


def test_protect_pdf_clarifies_when_password_not_in_utterance():
    from knaif.nl_clarify_gate import nl_clarify_gate
    from knaif.registry import load_registry

    reg = load_registry(DOCUMENTS_SKILL_DIR / "tools.yaml")
    # Model hallucinated a placeholder password the user never said.
    plan = [{"tool": "protect_pdf", "args": {"input": "sample.pdf", "password": "your_password"}}]
    result = nl_clarify_gate("password protect sample.pdf", plan, registry=reg)
    assert result[0]["tool"] == "clarify"


def test_protect_pdf_grounded_password_not_clarified_about_password():
    from knaif.nl_clarify_gate import nl_clarify_gate
    from knaif.registry import load_registry

    reg = load_registry(DOCUMENTS_SKILL_DIR / "tools.yaml")
    # A grounded password (present in the utterance) must not trigger the
    # password clarify. (The file-input check is a separate concern.)
    plan = [{"tool": "protect_pdf", "args": {"input": "sample.pdf", "password": "hunter2"}}]
    result = nl_clarify_gate("protect sample.pdf with hunter2", plan, registry=reg)
    asked = (
        result[0]["args"].get("question", "") if result and result[0]["tool"] == "clarify" else ""
    )
    assert "password" not in asked.lower()


def test_inspect_dry_run_tolerates_missing_chain_intermediate(tmp_path: Path):
    """In a dry-run chain preview an upstream output doesn't exist yet; inspect
    must return a stub rather than raising on the missing file."""
    handlers = _documents_handlers_module()
    ctx = HandlerContext(
        root=tmp_path,
        sandbox=tmp_path,
        dry_run=True,
        confirmed=False,
        skill_dir=DOCUMENTS_SKILL_DIR,
    )
    result = handlers.InspectDocumentStep().handle({"input": "not-yet-made.pdf"}, ctx)
    assert result["mode"] == "dry_run"
    assert result["pages"] == 1


def test_run_compress_dry_run_tolerates_missing_chain_intermediate(tmp_path: Path):
    """run_compress reads the input size; under a dry-run chain the upstream
    output is absent, so it must not raise."""
    handlers = _documents_handlers_module()
    ctx = HandlerContext(
        root=tmp_path,
        sandbox=tmp_path,
        dry_run=True,
        confirmed=False,
        skill_dir=DOCUMENTS_SKILL_DIR,
    )
    result = handlers.RunCompressStep().handle(
        {"input": "not-yet-made.pdf", "compress_quality": "small", "inspection": {"pages": 3}}, ctx
    )
    assert result["mode"] == "dry_run"
    assert result["original_size"] == 0


@pytest.mark.parametrize("filename", ["train.jsonl", "safety_test.jsonl"])
def test_data_jsonl_is_valid(filename: str):
    path = DOCUMENTS_SKILL_DIR / "data" / filename
    assert path.exists()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        assert "utterance" in row
        assert "plan" in row
        assert isinstance(row["plan"]["plan"], list)
