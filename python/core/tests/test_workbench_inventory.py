"""Tests for the workbench inventory (docs/plans/2026-09-21-skill-prompt-workbench.md T5).

Everything here runs off fixtures on disk — no GGUF, no binary, no GPU. The inventory's job is
to tell the truth about what is present, so the tests are about what it says when things are
missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SHARED = Path("notebooks") / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from workbench.inventory import (  # noqa: E402
    BuildEntry,
    ModelEntry,
    group_models,
    parse_build_label,
    scan_builds,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── models ────────────────────────────────────────────────────────────────────────────────


def test_models_are_grouped_published_curated_experimental() -> None:
    """D3: 41 resolvable entries is "all versions" only if they are legible. The groups come
    from the manifest (published) and models.yaml (curated); everything else is an experiment.
    """
    grouped = group_models(
        backends={
            "knaif-4b-v2": "models/knaif-qwen3-4b-v2-q4_k_m.gguf",
            "qwen3-4b": "models/Qwen3-4B-Q4_K_M.gguf",
            "sft-v9-wild": "models/experiment.gguf",
        },
        published={"knaif-qwen3-4b-v2"},
        curated={"qwen3-4b"},
        resolves=lambda _p: True,
    )
    assert [e.name for e in grouped["published"]] == ["knaif-4b-v2"]
    assert [e.name for e in grouped["curated"]] == ["qwen3-4b"]
    assert [e.name for e in grouped["experimental"]] == ["sft-v9-wild"]


def test_a_missing_gguf_is_greyed_with_its_path_never_dropped() -> None:
    """D3 is explicit: silently dropping an entry hides why an arm you expected is absent."""
    grouped = group_models(
        backends={"gone": "models/not-here.gguf"},
        published=set(),
        curated=set(),
        resolves=lambda _p: False,
    )
    missing = grouped["missing"]
    assert len(missing) == 1
    assert missing[0].resolved is False
    assert missing[0].path == "models/not-here.gguf"
    assert "not-here.gguf" in missing[0].label


def test_a_backend_without_a_model_path_is_not_a_model() -> None:
    """`mock` and the ollama arm carry no `path:` — they are arms, not files on disk."""
    grouped = group_models(backends={}, published=set(), curated=set(), resolves=lambda _p: True)
    assert all(not entries for entries in grouped.values())


# ── builds ────────────────────────────────────────────────────────────────────────────────


def test_a_build_is_labelled_by_what_it_reports() -> None:
    """D1e: the directory name is not evidence. A dir called release-vulkan holding a CUDA
    build must read as CUDA.
    """
    reported = json.dumps(
        {
            "version": "1.1.0",
            "built_with": ["llama", "dynamic-backends", "cuda", "pdfium"],
            "dynamic_backends": True,
            "backends_dir": "C:/Users/x/.knaif/backends",
            "platform": "windows-x64",
            "entries": [],
        }
    )
    entry = parse_build_label(Path("target/release-vulkan/knaif.exe"), reported)
    assert entry.features == ["llama", "dynamic-backends", "cuda", "pdfium"]
    assert entry.dynamic_backends is True
    assert "cuda" in entry.label
    # The lie is visible rather than silently accepted.
    assert entry.mislabelled is True


def test_a_build_whose_directory_matches_is_not_flagged() -> None:
    reported = json.dumps(
        {
            "version": "1.1.0",
            "built_with": ["llama", "vulkan"],
            "dynamic_backends": False,
            "backends_dir": None,
            "platform": "windows-x64",
            "entries": [],
        }
    )
    entry = parse_build_label(Path("target/release-vulkan/knaif.exe"), reported)
    assert entry.mislabelled is False
    assert entry.dynamic_backends is False


def test_a_binary_that_cannot_report_is_listed_as_unknown_not_skipped() -> None:
    """An older binary predating `backend list --json` still exists and can still be run."""
    entry = parse_build_label(Path("target/release/knaif.exe"), "")
    assert entry.features == []
    assert "unknown" in entry.label.lower()


def test_scan_finds_every_release_profile_directory(tmp_path: Path) -> None:
    """`target/release*/` covers plain release and every release-<kind> profile."""
    for name in ("release", "release-cpu", "release-cuda", "debug"):
        _write(tmp_path / "target" / name / "knaif.exe", "x")
    found = {p.parent.name for p in scan_builds(tmp_path, exe_name="knaif.exe")}
    assert found == {"release", "release-cpu", "release-cuda"}


def test_declared_directories_are_added_to_the_scan(tmp_path: Path) -> None:
    """workbench.local.yaml registers directories the scan would not reach."""
    _write(tmp_path / "target" / "release" / "knaif.exe", "x")
    _write(tmp_path / "elsewhere" / "knaif.exe", "x")
    found = {
        p.parent.name for p in scan_builds(tmp_path, exe_name="knaif.exe", declared=["elsewhere"])
    }
    assert found == {"release", "elsewhere"}


def test_a_declared_directory_that_is_gone_is_not_an_error(tmp_path: Path) -> None:
    """A machine-local config outlives the directories it names."""
    _write(tmp_path / "target" / "release" / "knaif.exe", "x")
    found = scan_builds(tmp_path, exe_name="knaif.exe", declared=["vanished"])
    assert len(found) == 1


def test_build_entry_renders_a_dropdown_label() -> None:
    entry = BuildEntry(
        path=Path("target/release-cuda/knaif.exe"),
        version="1.1.0",
        features=["llama", "dynamic-backends", "cuda"],
        dynamic_backends=True,
        backends_dir="/home/x/.knaif/backends",
    )
    assert "release-cuda" in entry.label
    assert "cuda" in entry.label


def test_model_entry_says_which_file_it_is() -> None:
    entry = ModelEntry(
        name="knaif-4b-v2", path="models/knaif-qwen3-4b-v2-q4_k_m.gguf", resolved=True
    )
    assert "knaif-4b-v2" in entry.label


# ── selection ─────────────────────────────────────────────────────────────────────────────


def test_selection_scopes_each_axis_to_where_it_is_real() -> None:
    """D1: runtime picks who runs; inference is Python-only; compute is native-only."""
    from workbench.selectors import Selection

    both = Selection(runtime="both")
    assert both.wants_python and both.wants_native
    assert Selection(runtime="python").wants_native is False
    assert Selection(runtime="native").wants_python is False


def test_dry_run_is_the_default_and_execute_is_deliberate() -> None:
    from workbench.selectors import Selection

    assert Selection().dry_run is True
    assert Selection(mode="execute").dry_run is False


def test_a_selection_describes_what_produced_a_result() -> None:
    """Worth pasting beside a number, so the number stays attributable."""
    from workbench.selectors import Selection

    entry = ModelEntry(name="knaif-4b-v2", path="models/x.gguf", resolved=True)
    line = Selection(runtime="python", model=entry, force_cpu=True).describe()
    assert "model=knaif-4b-v2" in line
    assert "force_cpu" in line


# ── fixtures (D5) ─────────────────────────────────────────────────────────────────────────


def test_fixtures_are_copied_into_the_scratch_not_used_in_place(tmp_path: Path) -> None:
    """A real run must never write into sandbox/fixtures/.

    The executing verifiers grade the files that appear on disk, so a bench that edited a
    fixture would silently change what every later eval run measures — and the damage would
    surface as a model regression in a run that had nothing to do with the notebook.
    """
    from workbench.fixtures import provision

    fixtures = tmp_path / "fixtures" / "ffmpeg"
    _write(fixtures / "clip.mp4", "original")
    work = tmp_path / "scratch"

    n = provision(fixtures, work)
    assert n == 1
    assert (work / "clip.mp4").is_file()

    # Editing the copy must not reach the source.
    (work / "clip.mp4").write_text("mangled", encoding="utf-8")
    assert fixtures.joinpath("clip.mp4").read_text(encoding="utf-8") == "original"


def test_provisioning_a_missing_fixture_dir_is_not_an_error(tmp_path: Path) -> None:
    """A skill with no fixtures generated yet still has to be runnable in dry-run."""
    from workbench.fixtures import provision

    assert provision(tmp_path / "nope", tmp_path / "work") == 0


def test_fixture_dir_is_named_per_skill(tmp_path: Path) -> None:
    from workbench.fixtures import fixture_dir

    assert fixture_dir(tmp_path, "ffmpeg") == tmp_path / "sandbox" / "fixtures" / "ffmpeg"
