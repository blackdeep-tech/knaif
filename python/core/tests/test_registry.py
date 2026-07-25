"""Tests for knaif.registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.registry import ArgSchema, ToolDef, _normalize, load_registry, retrieve_tools

TOOLS_YAML = Path("configs") / "tools.yaml"
FFMPEG_TOOLS_YAML = Path("skills") / "ffmpeg" / "tools.yaml"


@pytest.fixture()
def ffmpeg_registry():
    """Return the ToolDef registry loaded from skills/ffmpeg/tools.yaml."""
    return load_registry(FFMPEG_TOOLS_YAML)


def test_load_registry_returns_all_tools(registry):
    expected = {
        "list_files",
        "find_files",
        "delete_files",
        "move_files",
        "clarify",
        "reject",
        "done",
    }
    assert expected == set(registry.keys())


def test_tool_def_type(registry):
    for tool_def in registry.values():
        assert isinstance(tool_def, ToolDef)


def test_list_files_fields(registry):
    lt = registry["list_files"]
    assert lt.name == "list_files"
    assert "path" in lt.required_args
    assert "pattern" in lt.optional_args
    assert lt.safety_category == "safe"


def test_find_files_fields(registry):
    ff = registry["find_files"]
    assert ff.required_args == ("path",)
    assert "file_type" in ff.optional_args
    assert "pattern" in ff.optional_args


def test_delete_files_is_destructive(registry):
    assert registry["delete_files"].safety_category == "destructive"


def test_move_files_is_destructive(registry):
    assert registry["move_files"].safety_category == "destructive"


def test_clarify_required_args(registry):
    assert registry["clarify"].required_args == ("question",)


def test_reject_required_args(registry):
    assert registry["reject"].required_args == ("reason",)


def test_args_are_tuples(registry):
    for tool_def in registry.values():
        assert isinstance(tool_def.required_args, tuple)
        assert isinstance(tool_def.optional_args, tuple)


def test_grounded_args_parsed(tmp_path):
    y = tmp_path / "t.yaml"
    y.write_text(
        "foo:\n"
        "  description: d\n"
        "  required_args: [input, password]\n"
        "  grounded_args: [password]\n",
        encoding="utf-8",
    )
    reg = load_registry(y)
    assert reg["foo"].grounded_args == ("password",)


def test_grounded_args_default_empty(registry):
    for tool_def in registry.values():
        assert tool_def.grounded_args == ()


def test_missing_yaml_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_registry(tmp_path / "nonexistent.yaml")


def test_invalid_yaml_structure(tmp_path):
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("- not_a_mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_registry(bad_yaml)


# ── keywords ──────────────────────────────────────────────────────────────────


def test_keywords_are_tuples(registry):
    for tool_def in registry.values():
        assert isinstance(tool_def.keywords, tuple)


def test_keywords_nonempty_for_file_tools(registry):
    for name in ("list_files", "find_files", "delete_files", "move_files"):
        assert len(registry[name].keywords) > 0


def test_shared_keyword_is_allowed(tmp_path):
    # A keyword shared by a couple of tools is legitimate (natural language isn't
    # exclusive); retrieval down-weights it by document frequency.
    ok = tmp_path / "shared.yaml"
    ok.write_text(
        "tool_a:\n  description: A\n  keywords: [foo, bar]\n  required_args: []\n"
        "tool_b:\n  description: B\n  keywords: [foo, baz]\n  required_args: []\n",
        encoding="utf-8",
    )
    reg = load_registry(ok)
    assert "tool_a" in reg and "tool_b" in reg


def test_overly_generic_keyword_raises(tmp_path):
    # A keyword claimed by more than _MAX_KEYWORD_TOOLS (4) tools is a curation
    # mistake — too generic to discriminate — and is rejected at load time.
    bad = tmp_path / "generic.yaml"
    bad.write_text(
        "".join(
            f"tool_{i}:\n  description: T{i}\n  keywords: [foo, kw{i}]\n  required_args: []\n"
            for i in range(5)
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="too generic"):
        load_registry(bad)


# ── retrieve_tools ────────────────────────────────────────────────────────────


def test_retrieve_returns_relevant_tool(registry):
    result = retrieve_tools("find files", registry)
    assert "find_files" in result


# ── ArgSchema ──────────────────────────────────────────────────────────────────


def test_arg_schema_is_importable():
    assert ArgSchema is not None


def test_arg_schema_default_type_is_string():
    s = ArgSchema()
    assert s.type == "string"


def test_arg_schema_fields():
    s = ArgSchema(type="enum", enum=("low", "med", "high"), help="priority level")
    assert s.type == "enum"
    assert s.enum == ("low", "med", "high")
    assert s.help == "priority level"
    assert s.min is None
    assert s.max is None
    assert s.items is None
    assert s.path_role is None


def test_tool_def_arg_schemas_default_empty():
    td = ToolDef(name="t", description="d", required_args=())
    assert td.arg_schemas == {}


def test_load_registry_parses_arg_schemas(tmp_path):
    yaml_text = """\
my_tool:
  description: Test tool
  required_args: [priority, count]
  arg_schemas:
    priority:
      type: enum
      enum: [low, med, high]
      help: Task priority
    count:
      type: integer
      min: 1
      max: 100
"""
    p = tmp_path / "tools.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    reg = load_registry(p)
    td = reg["my_tool"]
    assert "priority" in td.arg_schemas
    assert "count" in td.arg_schemas
    ps = td.arg_schemas["priority"]
    assert ps.type == "enum"
    assert "low" in ps.enum
    assert "high" in ps.enum
    assert ps.help == "Task priority"
    cs = td.arg_schemas["count"]
    assert cs.type == "integer"
    assert cs.min == 1
    assert cs.max == 100


def test_load_registry_tool_without_arg_schemas(tmp_path):
    yaml_text = """\
plain_tool:
  description: No schemas
  required_args: [name]
"""
    p = tmp_path / "tools.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    reg = load_registry(p)
    assert reg["plain_tool"].arg_schemas == {}


def test_retrieve_always_includes_system_tools(registry):
    result = retrieve_tools("xyzzy no match at all", registry)
    assert "clarify" in result
    assert "reject" in result
    assert "done" in result


def test_retrieve_respects_top_k(registry):
    system = {"clarify", "reject", "done"}
    result = retrieve_tools("files", registry, top_k=2)
    non_system = [k for k in result if k not in system]
    assert len(non_system) <= 2


def test_retrieve_keyword_beats_description(registry):
    # "delete" is a keyword of delete_files but appears in no other tool's keywords
    result = retrieve_tools("delete old logs", registry, top_k=1)
    assert "delete_files" in result


def test_retrieve_copy_into_selects_move_files(registry):
    result = retrieve_tools(
        "copy all text files from dir folder into newfolder folder",
        registry,
        top_k=1,
    )
    assert "move_files" in result


# ── readonly flag ─────────────────────────────────────────────────────────────


def test_list_files_is_readonly(registry):
    assert registry["list_files"].readonly is True


def test_find_files_is_readonly(registry):
    assert registry["find_files"].readonly is True


def test_action_tools_are_not_readonly(registry):
    assert registry["delete_files"].readonly is False
    assert registry["move_files"].readonly is False


def test_system_tools_are_not_readonly(registry):
    assert registry["clarify"].readonly is False
    assert registry["reject"].readonly is False
    assert registry["done"].readonly is False


def test_readonly_defaults_to_false(tmp_path):
    yaml_file = tmp_path / "tools.yaml"
    yaml_file.write_text(
        "my_tool:\n  description: A tool\n  required_args: []\n",
        encoding="utf-8",
    )
    reg = load_registry(yaml_file)
    assert reg["my_tool"].readonly is False


# ── _normalize helper ─────────────────────────────────────────────────────────


def test_normalize_strips_diacritics():
    assert _normalize("comprimír") == "comprimir"
    assert _normalize("kürzEN") == "kurzen"
    assert _normalize("réduire") == "reduire"


def test_normalize_lowercases():
    assert _normalize("COMPRESS") == "compress"


# ── multilingual retrieval (ffmpeg registry) ──────────────────────────────────


@pytest.mark.parametrize(
    "query,expected_tool",
    [
        # Spanish
        ("comprimir video", "compress_video"),
        ("convertir a mp4", "convert_video"),
        ("recortar el clip", "trim_video"),
        ("extraer audio", "extract_audio"),
        ("unir los videos", "concat_video"),
        ("invertir video", "reverse_video"),
        ("silenciar video", "strip_audio"),
        ("velocidad del video", "adjust_speed"),
        # German
        ("video komprimieren", "compress_video"),
        ("video schneiden", "trim_video"),
        ("videos verbinden", "concat_video"),
        ("video umkehren", "reverse_video"),
        # French
        ("compresser la video", "compress_video"),
        ("couper la video", "trim_video"),
        ("fusionner les videos", "concat_video"),
        # Russian
        ("сжать видео", "compress_video"),
        ("обрезать видео", "trim_video"),
        ("объединить видео", "concat_video"),
    ],
)
def test_multilingual_retrieval(ffmpeg_registry, query, expected_tool):
    result = retrieve_tools(query, ffmpeg_registry, top_k=5)
    assert (
        expected_tool in result
    ), f"Expected '{expected_tool}' in top-5 for query {query!r}, got {list(result)}"


def test_diacritic_query_matches_plain_keyword(ffmpeg_registry):
    # "comprimír" (accented) should still retrieve compress_video via normalization
    result = retrieve_tools("comprimír video", ffmpeg_registry, top_k=5)
    assert "compress_video" in result


def test_ffmpeg_keywords_not_overly_generic(ffmpeg_registry):
    # Keywords MAY be shared across tools (retrieval down-weights by document
    # frequency), but no keyword should be claimed by more than a handful of tools
    # — that would be too generic to discriminate. Catches curation mistakes.
    from collections import Counter

    counts = Counter(kw for tool in ffmpeg_registry.values() for kw in tool.keywords)
    over = {kw: n for kw, n in counts.items() if n > 4}
    assert not over, f"Over-generic keywords claimed by >4 tools: {over}"


# ── ToolDef.defaults ──────────────────────────────────────────────────────────


def test_tooldef_defaults_field_exists():
    """ToolDef has a defaults dict attribute."""
    t = ToolDef(name="t", description="d", required_args=())
    assert hasattr(t, "defaults")
    assert isinstance(t.defaults, dict)


def test_tooldef_defaults_empty_when_not_declared():
    t = ToolDef(name="t", description="d", required_args=())
    assert t.defaults == {}


def test_load_registry_loads_defaults(tmp_path):
    """A tools.yaml with a defaults block loads into ToolDef.defaults."""
    yaml_text = """\
my_tool:
  description: A tool
  required_args: [input]
  optional_args: [output]
  defaults:
    output: out.mp4
"""
    p = tmp_path / "tools.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    reg = load_registry(p)
    assert reg["my_tool"].defaults == {"output": "out.mp4"}


def test_load_registry_defaults_absent_is_empty(tmp_path):
    """A tool without a defaults key gets an empty dict, not None."""
    yaml_text = """\
my_tool:
  description: A tool
  required_args: [input]
"""
    p = tmp_path / "tools.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    reg = load_registry(p)
    assert reg["my_tool"].defaults == {}
