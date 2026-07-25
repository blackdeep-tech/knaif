"""Tests for the pure logic of scripts/publish_model.py (manifest rewrite + URL pinning).

Not in the default pytest testpaths (this is admin tooling, like the bench/training scripts).
Run explicitly:
  uv run --with ruamel.yaml --with pytest python -m pytest scripts/test_publish_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402
from publish_model import (  # noqa: E402
    _is_placeholder,
    _parse_dotenv,
    local_name,
    manifest_file_for,
    model_specs,
    needs_publish,
    resolve_url,
    updated_manifest,
)

MANIFEST = """\
# Header comment that must survive a round-trip.
models:
  qwen3-4b-v3:
    file: knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf
    url: "TODO"
    sha256: "TODO"
    size_bytes: 0
    license: "Apache-2.0 (Qwen3 base) + knaif fine-tune"
    skills: [ffmpeg, documents]

  qwen3-1.7b-sft-v3-flat-q6:
    file: knaif-qwen3-1.7b-sft-v3-flat-q6_k.gguf
    url: "TODO"
    sha256: "TODO"
    size_bytes: 0
recommendations:
  default: qwen3-4b-v3
"""


def test_resolve_url_is_commit_pinned():
    url = resolve_url("blackdeep/knaif", "abc123", "knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf")
    assert url == (
        "https://huggingface.co/blackdeep/knaif/resolve/abc123/"
        "knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf"
    )


def test_manifest_file_for_returns_canonical_name():
    assert manifest_file_for(MANIFEST, "qwen3-4b-v3") == "knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf"


def test_updated_manifest_sets_fields_and_preserves_rest():
    out = updated_manifest(
        MANIFEST,
        "qwen3-4b-v3",
        url="https://huggingface.co/blackdeep/knaif/resolve/sha/knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf",
        sha256="deadbeef",
        size_bytes=2560000000,
    )
    # target entry updated
    assert "resolve/sha/knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf" in out
    assert "deadbeef" in out
    assert "2560000000" in out
    # header comment preserved (ruamel round-trip)
    assert "Header comment that must survive a round-trip." in out
    # the OTHER model is untouched (still TODO)
    assert out.count('url: "TODO"') == 1
    # canonical file name of the target is unchanged
    assert "knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf" in out


def test_updated_manifest_rejects_unknown_model():
    with pytest.raises(KeyError):
        updated_manifest(MANIFEST, "does-not-exist", url="u", sha256="s", size_bytes=1)


def test_parse_dotenv_reads_token_skips_noise():
    env = _parse_dotenv(
        "\n".join(
            [
                "# a comment",
                "",
                "HF_TOKEN=hf_abc123",
                'QUOTED="with spaces"',
                "export EXPORTED=val",
                "NOT_A_PAIR",
            ]
        )
    )
    assert env["HF_TOKEN"] == "hf_abc123"
    assert env["QUOTED"] == "with spaces"
    assert env["EXPORTED"] == "val"  # leading `export ` tolerated
    assert "NOT_A_PAIR" not in env  # no '=' -> skipped


def test_parse_dotenv_splits_on_first_equals_only():
    # a value that itself contains '=' (e.g. base64 padding) stays intact
    assert _parse_dotenv("K=a=b=c")["K"] == "a=b=c"


@pytest.mark.parametrize("val", [None, "", "  ", "TODO"])
def test_is_placeholder_true(val):
    assert _is_placeholder(val)


@pytest.mark.parametrize("val", ["deadbeef", "https://x", "0"])
def test_is_placeholder_false(val):
    assert not _is_placeholder(val)


def test_model_specs_lists_files_and_fields():
    specs = model_specs(MANIFEST)
    assert set(specs) == {"qwen3-4b-v3", "qwen3-1.7b-sft-v3-flat-q6"}
    assert specs["qwen3-4b-v3"]["file"] == "knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf"
    assert specs["qwen3-4b-v3"]["url"] == "TODO"  # placeholder preserved as-is


def test_needs_publish_logic():
    # unhosted (url/sha are TODO) -> publish regardless of local sha
    assert needs_publish({"url": "TODO", "sha256": "TODO"}, "abc")
    # hosted + matching checksum -> skip
    assert not needs_publish({"url": "https://x", "sha256": "abc"}, "abc")
    # hosted but local bytes drifted -> republish
    assert needs_publish({"url": "https://x", "sha256": "abc"}, "xyz")


def test_local_name_prefers_source_over_file():
    # local FT-cycle file backs the entry when `source` is set; else the public `file` name
    assert local_name({"file": "pub-v1.gguf", "source": "ft-sft-v3.gguf"}) == "ft-sft-v3.gguf"
    assert local_name({"file": "pub-v1.gguf"}) == "pub-v1.gguf"
    assert local_name({"file": "pub-v1.gguf", "source": None}) == "pub-v1.gguf"


def test_model_specs_reads_source_and_training_run_absence():
    manifest = (
        "models:\n"
        "  knaif-qwen3-4b-v1:\n"
        "    file: knaif-qwen3-4b-v1-q4_k_m.gguf\n"
        "    source: knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf\n"
        "    training_run: sft-v3-flat\n"
        '    url: "TODO"\n'
        '    sha256: "TODO"\n'
    )
    specs = model_specs(manifest)
    spec = specs["knaif-qwen3-4b-v1"]
    assert spec["file"] == "knaif-qwen3-4b-v1-q4_k_m.gguf"
    assert spec["source"] == "knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf"
    assert local_name(spec) == "knaif-qwen3-4b-sft-v3-flat-q4_k_m.gguf"
