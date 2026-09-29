"""A default output name that already exists is never overwritten.

Found 2026-09-24 by the first native L4 run of documents: `convert sample.txt to markdown` wrote
`sample.md`, which already existed, and `protect_pdf` wrote `sample-protected.pdf` over an existing
file. Both runtimes did it; the Python eval never noticed because it grades the path a tool reports.
For a user, "convert notes.txt to markdown" destroyed an existing notes.md they never mentioned.

The rule, the same as ffmpeg's: a name the plan did not choose moves to the next free one
(`<name>-1<ext>`, `-2`, ...); an explicit `output` is a request and is honoured. Mirrored in
skills/documents/native/src/run.rs (`derive_output`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent

DOCUMENTS_SKILL_DIR = Path(__file__).parents[2]


def _run(tool: str, args: dict, sandbox: Path, *, dry_run: bool = False) -> dict:
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=sandbox, root=sandbox)
    results = agent.execute_plan(
        {"plan": [{"tool": tool, "args": args}]}, dry_run=dry_run, confirmed=True
    )
    return results[0]["result"]


def test_a_default_output_that_exists_moves_to_the_next_free_name(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("Invoice Alpha\n", encoding="utf-8")
    existing = tmp_path / "notes.md"
    existing.write_text("the user's own notes\n", encoding="utf-8")

    result = _run("convert_document", {"input": "notes.txt", "to_format": "md"}, tmp_path)

    assert Path(result["output"]).name == "notes-1.md"
    assert existing.read_text(encoding="utf-8") == "the user's own notes\n"
    assert "Invoice Alpha" in (tmp_path / "notes-1.md").read_text(encoding="utf-8")


def test_the_next_free_name_skips_every_taken_one(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    for name in ("notes.md", "notes-1.md", "notes-2.md"):
        (tmp_path / name).write_text("taken\n", encoding="utf-8")

    result = _run("convert_document", {"input": "notes.txt", "to_format": "md"}, tmp_path)

    assert Path(result["output"]).name == "notes-3.md"


def test_a_free_default_name_is_used_as_is(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    result = _run("convert_document", {"input": "notes.txt", "to_format": "md"}, tmp_path)
    assert Path(result["output"]).name == "notes.md"


def test_an_explicit_output_is_honoured_even_when_it_exists(tmp_path: Path) -> None:
    """Naming the destination is the request; renaming it would second-guess it."""
    (tmp_path / "notes.txt").write_text("fresh\n", encoding="utf-8")
    (tmp_path / "out.md").write_text("old\n", encoding="utf-8")

    result = _run(
        "convert_document", {"input": "notes.txt", "to_format": "md", "output": "out.md"}, tmp_path
    )

    assert Path(result["output"]).name == "out.md"
    assert "fresh" in (tmp_path / "out.md").read_text(encoding="utf-8")


def test_a_dry_run_reports_the_name_a_real_run_would_write(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("taken\n", encoding="utf-8")
    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    results = agent.execute_plan(
        {"plan": [{"tool": "convert_document", "args": {"input": "notes.txt", "to_format": "md"}}]},
        dry_run=True,
    )
    assert "notes-1.md" in str(results)


def test_a_suffixed_default_moves_too(tmp_path: Path) -> None:
    pytest.importorskip("pikepdf")
    import pikepdf

    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    pdf.save(tmp_path / "doc.pdf")
    (tmp_path / "doc-protected.pdf").write_bytes(b"someone else's file")

    result = _run("protect_pdf", {"input": "doc.pdf", "password": "secret"}, tmp_path)

    assert Path(result["output"]).name == "doc-protected-1.pdf"
    assert (tmp_path / "doc-protected.pdf").read_bytes() == b"someone else's file"


# -- Office -> PDF: LibreOffice's own intermediate name ------------------------------------------
# Found 2026-09-26 while strengthening documents grading: converting `sample.docx` to `conv.pdf`
# deleted the user's unrelated `sample.pdf`. `soffice --outdir <output's folder>` writes
# `<stem>.pdf` there first, silently replacing a file of that name, and the step then moved it to
# the requested output. Both runtimes. The conversion now happens in a private directory.


@pytest.fixture
def fake_soffice(monkeypatch: pytest.MonkeyPatch):
    """`soffice --convert-to pdf --outdir DIR INPUT` as it behaves: writes DIR/<stem>.pdf."""
    import subprocess
    import sys

    sys.modules.pop("_skill_oop_documents_handlers", None)
    from knaif.skill import Skill

    Skill.load(DOCUMENTS_SKILL_DIR)
    pkg = sys.modules["_skill_oop_documents_handlers"].__package__
    steps = sys.modules[pkg + ".steps"]
    deps = sys.modules[pkg + "._deps"]
    monkeypatch.setattr(deps, "detect_external_tools", lambda: {"soffice": "soffice"})

    def _run(argv, **kwargs):
        outdir = Path(argv[argv.index("--outdir") + 1])
        source = Path(argv[-1])
        (outdir / f"{source.stem}.pdf").write_bytes(b"%PDF-converted")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(steps.subprocess, "run", _run)


@pytest.mark.parametrize("output", ["conv.pdf", None])
def test_office_conversion_never_replaces_a_same_stem_pdf(
    tmp_path: Path, fake_soffice, output: str | None
) -> None:
    (tmp_path / "sample.docx").write_bytes(b"docx")
    users_pdf = tmp_path / "sample.pdf"
    users_pdf.write_bytes(b"%PDF-the user's own file")
    args = {"input": "sample.docx", "to_format": "pdf"}
    if output:
        args["output"] = output

    result = _run("convert_document", args, tmp_path)

    assert users_pdf.read_bytes() == b"%PDF-the user's own file"
    assert Path(result["output"]).read_bytes() == b"%PDF-converted"
    assert Path(result["output"]) != users_pdf


# -- compress never hands back a bigger file -------------------------------------------------------
# Found 2026-09-26 while strengthening documents grading: compress_pdf wrote a LARGER file for
# both fixtures (sample.pdf 2227 -> 3952 bytes, sample-scanned.pdf 5707 -> 7689) and reported a
# negative percentage. A result that is not smaller is not a compression; the input's own bytes
# are kept instead, and the result says so (`kept_original`).


@pytest.mark.parametrize("quality", ["small", "balanced", "high"])
def test_compress_never_writes_a_larger_file(tmp_path: Path, quality: str) -> None:
    # The committed contract fixture: a small text PDF, the case where every method grew it.
    fixture = Path(__file__).parents[4] / "contracts" / "parity" / "fixtures" / "documents"
    source = tmp_path / "sample.pdf"
    source.write_bytes((fixture / "sample.pdf").read_bytes())

    agent = CommandAgent.from_skill(DOCUMENTS_SKILL_DIR, sandbox=tmp_path, root=tmp_path)
    results = agent.execute_plan(
        {
            "plan": [
                {
                    "tool": "compress_pdf",
                    "args": {
                        "input": "sample.pdf",
                        "compress_quality": quality,
                        "output": "out.pdf",
                    },
                }
            ]
        },
        dry_run=False,
        confirmed=True,
    )
    result = next(r["result"] for r in results if "new_size" in (r.get("result") or {}))

    out = tmp_path / "out.pdf"
    assert out.stat().st_size <= source.stat().st_size, result
    if result["kept_original"]:
        assert out.read_bytes() == source.read_bytes()
        assert result["percent"] == 0.0 and result["text_preserved"] is True
