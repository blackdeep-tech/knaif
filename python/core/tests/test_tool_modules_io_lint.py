"""Tool modules do no direct I/O (owner decision 2026-10-08/2026-10-11).

Python cannot stop a handler from calling ``open()`` itself, so this lint does: in a skill's or a
toolkit's tool modules (``skills/*/python/*.py``, ``toolkits/*/python/*.py``, tests excluded) it
bans process and network modules, shutil's copy/move/remove functions, pathlib/os writes and
deletes, and ``open`` in a write mode. A step runs programs through ``ctx.exec`` and writes
through the fs toolkit (``ctx.call_tool``), where its declared ``effects:`` are enforced.

Every exception is listed in ``ALLOWED`` with its reason, keyed by file, function and kind (not
by line, so editing around it does not break it). An exception that no longer matches anything is
an error too: stale permissions are how a lint stops meaning anything.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

BANNED_MODULES = {"subprocess", "urllib", "httpx", "requests", "socket", "http"}
#: Submodules of a banned package that do no I/O: URL parsing is string work.
PURE_SUBMODULES = {"urllib.parse"}
SHUTIL_WRITES = {"move", "copy", "copy2", "copyfile", "copytree", "rmtree", "copyfileobj"}
PATH_WRITES = {
    "write_text",
    "write_bytes",
    "unlink",
    "mkdir",
    "rmdir",
    "touch",
    "symlink_to",
    "rename",
}
OS_WRITES = {"remove", "unlink", "rename", "replace", "makedirs", "mkdir", "rmdir", "removedirs"}

# (file relative to the repo, enclosing function, kind) -> why it may write directly.
_FS = "the fs toolkit is where writes live: these are the write tools themselves (fs.write)"
_TARGET = (
    "writes only the path `_writing` hands out (a staged path in the run scratch, committed by "
    "fs `commit_output`); a library or program needs a real file path to write to"
)
_IO = "io is stale (hidden from discovery, slated for rebuild); it moves onto fs when rebuilt"
_EVAL = "eval artifact runner: provisions a row folder for the eval harness, not a tool step"
_NET = (
    "the net toolkit is where network access lives (net); it writes the bytes it downloads "
    "into the run scratch only (`<dest_dir>/<name>.part`, renamed when complete)"
)
_NETW = "toolkits/net/python/download.py"
_DOC = "skills/documents/python/"
_FSW = "toolkits/fs/python/write.py"
ALLOWED: dict[tuple[str, str, str], str] = {
    (_FSW, "StageOutput.handle", ".mkdir"): _FS,
    (_FSW, "CommitOutput.handle", ".unlink"): _FS,
    (_FSW, "CommitOutput.handle", ".mkdir"): _FS,
    (_FSW, "CommitOutput.handle", "shutil.move"): _FS,
    (_FSW, "EnsureParent.handle", ".mkdir"): _FS,
    (_NETW, "<module>", "import requests"): _NET,
    (_NETW, "_fetch", ".mkdir"): _NET,
    (_NETW, "_fetch", ".open(write)"): _NET,
    (_NETW, "_fetch", ".unlink"): _NET,
    (_DOC + "_engine.py", "_write_ocr_image_pdf", ".write_bytes"): _TARGET,
    (_DOC + "_engine.py", "_write_ocr_pdf", ".open(write)"): _TARGET,
    (_DOC + "_engine.py", "_write_text_overlay_pdf", ".open(write)"): _TARGET,
    (_DOC + "_engine.py", "_write_image_overlay_pdf", ".open(write)"): _TARGET,
    (_DOC + "steps.py", "ConvertDocumentStep.handle", ".write_text"): _TARGET,
    (_DOC + "steps.py", "ConvertDocumentStep.handle", "shutil.move"): _TARGET
    + " (soffice's result, moved from its private folder)",
    (_DOC + "steps.py", "RunCompressStep.handle", "shutil.copyfile"): _TARGET
    + " (the input's own bytes when compressing did not shrink it)",
    (_DOC + "steps.py", "RunOcrStep.handle", "shutil.copyfile"): _TARGET
    + " (a PDF that already has a text layer)",
    (_DOC + "handlers.py", "DocumentsSkill.run_artifact", ".mkdir"): _EVAL,
    (_DOC + "handlers.py", "DocumentsSkill.run_artifact", "shutil.copy2"): _EVAL,
    ("skills/io/python/handlers.py", "DeleteFilesStep.handle", ".unlink"): _IO,
    ("skills/io/python/handlers.py", "MoveFilesStep.handle", ".mkdir"): _IO,
    ("skills/io/python/handlers.py", "MoveFilesStep.handle", ".rename"): _IO,
}


def _modules() -> list[Path]:
    files = sorted((REPO / "skills").glob("*/python/*.py")) + sorted(
        (REPO / "toolkits").glob("*/python/*.py")
    )
    return [f for f in files if "tests" not in f.parts]


def _write_mode(call: ast.Call, mode_index: int) -> bool:
    mode = None
    if len(call.args) > mode_index and isinstance(call.args[mode_index], ast.Constant):
        mode = call.args[mode_index].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and any(c in mode for c in "wax+")


def _findings(path: Path) -> list[tuple[str, str, str, int]]:
    """``(file, function, kind, line)`` for every direct I/O site in *path*."""
    rel = path.relative_to(REPO).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str, str, int]] = []

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = func
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = child.name if func == "<module>" else f"{func}.{child.name}"
            kind = None
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if (
                        alias.name.split(".")[0] in BANNED_MODULES
                        and alias.name not in PURE_SUBMODULES
                    ):
                        kind = f"import {alias.name}"
            elif isinstance(child, ast.ImportFrom) and child.module:
                if (
                    child.module.split(".")[0] in BANNED_MODULES
                    and child.module not in PURE_SUBMODULES
                ):
                    kind = f"import {child.module}"
            elif isinstance(child, ast.Call):
                f = child.func
                if isinstance(f, ast.Attribute):
                    owner = f.value.id if isinstance(f.value, ast.Name) else None
                    if owner == "shutil" and f.attr in SHUTIL_WRITES:
                        kind = f"shutil.{f.attr}"
                    elif owner == "os" and f.attr in OS_WRITES:
                        kind = f"os.{f.attr}"
                    elif f.attr in PATH_WRITES:
                        kind = f".{f.attr}"
                    elif (
                        f.attr == "open"
                        and owner not in ("pikepdf", "Image")
                        and _write_mode(child, 0)
                    ):
                        kind = ".open(write)"
                elif isinstance(f, ast.Name) and f.id == "open" and _write_mode(child, 1):
                    kind = "open(write)"
            if kind:
                out.append((rel, name, kind, child.lineno))
            visit(child, name)

    visit(tree, "<module>")
    return out


def test_tool_modules_do_no_unlisted_direct_io() -> None:
    found = [f for path in _modules() for f in _findings(path)]
    unlisted = [f for f in found if f[:3] not in ALLOWED]
    assert not unlisted, (
        "direct I/O in a tool module (use ctx.exec / the fs toolkit, or list it in ALLOWED with a reason):\n"
        + "\n".join(f"  {rel}:{line}  {func}  {kind}" for rel, func, kind, line in unlisted)
    )


def test_every_exception_is_still_needed() -> None:
    found = {f[:3] for path in _modules() for f in _findings(path)}
    stale = sorted(set(ALLOWED) - found)
    assert not stale, f"ALLOWED entries that match nothing any more (remove them): {stale}"


def test_the_lint_sees_what_it_bans(tmp_path: Path) -> None:
    """A planted module with one of each banned call is reported in full."""
    src = (
        "import subprocess\nimport shutil\nimport os\nfrom pathlib import Path\n"
        "import urllib.parse\nfrom urllib.parse import urljoin\nimport urllib.request\n"
        "def f(p):\n"
        "    shutil.move(p, p)\n    os.remove(p)\n    Path(p).write_text('x')\n"
        "    open(p, 'w')\n    Path(p).open('ab')\n    shutil.which('ffmpeg')\n    open(p)\n"
        "    Path(p).rename(p)\n"
    )
    tree_path = tmp_path / "probe.py"
    tree_path.write_text(src, encoding="utf-8")
    kinds = {k for _r, _f, k, _l in _findings_from(tree_path, src)}
    assert kinds == {
        "import subprocess",
        "import urllib.request",
        "shutil.move",
        "os.remove",
        ".write_text",
        "open(write)",
        ".open(write)",
        ".rename",
    }


def _findings_from(path: Path, src: str) -> list[tuple[str, str, str, int]]:
    """:func:`_findings` for a file outside the repo (the planted probe)."""
    global REPO
    saved = REPO
    try:
        REPO = path.parent
        return _findings(path)
    finally:
        REPO = saved
