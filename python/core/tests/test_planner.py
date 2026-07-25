"""Tests for knaif.planner."""

from __future__ import annotations

import pytest

from knaif.planner import (
    StemAmbiguousError,
    StemNotFoundError,
    _resolve_path,
    normalize_plan,
    optimize_plan,
    parse_plan,
    resolve_args,
    resolve_stems,
    summarize_plan,
    summarize_plan_steps,
    validate_arg_by_schema,
    validate_plan,
    validate_step,
)
from knaif.registry import ArgSchema

# ── parse_plan ────────────────────────────────────────────────────────────────


def test_parse_valid_plan():
    payload = parse_plan('{"plan": [{"tool": "list_files", "args": {"path": "/tmp"}}]}')
    assert payload["plan"][0]["tool"] == "list_files"


def test_parse_invalid_json():
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_plan("not valid json }{")


def test_parse_missing_plan_key():
    with pytest.raises(ValueError, match="'plan' field"):
        parse_plan('{"steps": []}')


def test_parse_plan_not_a_dict():
    with pytest.raises(ValueError, match="'plan' field"):
        parse_plan('["list"]')


def test_parse_plan_list_not_a_list():
    with pytest.raises(ValueError, match="'plan' must be a list"):
        parse_plan('{"plan": "list_files"}')


# ── validate_step ─────────────────────────────────────────────────────────────


def test_validate_step_unknown_tool(registry, sandbox, tmp_path):
    step = {"tool": "unknown_tool", "args": {}}
    with pytest.raises(ValueError, match="Unknown tool"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_none_tool(registry, sandbox, tmp_path):
    step = {"args": {"path": str(sandbox)}}
    with pytest.raises(ValueError, match="Unknown tool"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_missing_args_object(registry, sandbox, tmp_path):
    step = {"tool": "list_files"}
    with pytest.raises(ValueError, match="'args' object"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_missing_required_arg(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {}}  # path is required
    with pytest.raises(ValueError, match="missing required args"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_extra_arg(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": str(sandbox), "extra": "val"}}
    with pytest.raises(ValueError, match="unsupported args"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_any_of_args(tmp_path):
    """A tool declaring any_of_args must receive at least one of those args."""
    from knaif.registry import ToolDef

    reg = {
        "concat_video": ToolDef(
            name="concat_video",
            description="join clips",
            required_args=("output",),
            optional_args=("inputs", "base", "append"),
            any_of_args=("inputs", "base", "append"),
        ),
    }
    bad = {"tool": "concat_video", "args": {"output": "out.mp4"}}
    with pytest.raises(ValueError, match="at least one of"):
        validate_step(bad, reg, tmp_path, None)

    ok = {"tool": "concat_video", "args": {"output": "out.mp4", "inputs": ["a", "b"]}}
    validate_step(ok, reg, tmp_path, None)  # does not raise


def test_validate_step_path_outside_sandbox(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": "/etc"}}
    with pytest.raises(ValueError, match="outside sandbox"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_path_must_be_string(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": 123}}
    with pytest.raises(ValueError, match="'path' must be a string"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_pattern_must_be_string(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": str(sandbox), "pattern": 42}}
    with pytest.raises(ValueError, match="'pattern' must be a string"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_recursive_must_be_bool(registry, sandbox, tmp_path):
    step = {
        "tool": "delete_files",
        "args": {"path": str(sandbox), "recursive": "yes"},
    }
    with pytest.raises(ValueError, match="'recursive' must be a boolean"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_valid_list_files(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": str(sandbox), "pattern": "*.txt"}}
    validate_step(step, registry, tmp_path, sandbox)  # should not raise


def test_validate_step_valid_move_files(registry, sandbox, tmp_path):
    step = {
        "tool": "move_files",
        "args": {
            "src": str(sandbox / "tmp"),
            "dst": str(sandbox / "reports"),
            "pattern": "*.log",
        },
    }
    validate_step(step, registry, tmp_path, sandbox)  # should not raise


# ── file_type validation ──────────────────────────────────────────────────────


def test_validate_step_valid_file_type(registry, sandbox, tmp_path):
    step = {"tool": "find_files", "args": {"path": str(sandbox), "file_type": "executable"}}
    validate_step(step, registry, tmp_path, sandbox)  # should not raise


def test_validate_step_invalid_file_type(registry, sandbox, tmp_path):
    step = {"tool": "find_files", "args": {"path": str(sandbox), "file_type": "unknown"}}
    with pytest.raises(ValueError, match="Unknown file_type"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_file_type_must_be_string(registry, sandbox, tmp_path):
    step = {"tool": "find_files", "args": {"path": str(sandbox), "file_type": 42}}
    with pytest.raises(ValueError, match="'file_type' must be a string"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_step_find_files_no_filter(registry, sandbox, tmp_path):
    step = {"tool": "find_files", "args": {"path": str(sandbox)}}
    validate_step(step, registry, tmp_path, sandbox)  # pattern no longer required


# ── validate_plan ─────────────────────────────────────────────────────────────


def test_validate_plan_success(registry, sandbox, tmp_path):
    payload = {"plan": [{"tool": "list_files", "args": {"path": str(sandbox)}}]}
    validate_plan(payload, registry, tmp_path, sandbox)  # should not raise


def test_validate_plan_reports_step_number(registry, sandbox, tmp_path):
    payload = {
        "plan": [
            {"tool": "list_files", "args": {"path": str(sandbox)}},
            {"tool": "bad_tool", "args": {}},
        ]
    }
    with pytest.raises(ValueError, match="Plan step 2 invalid"):
        validate_plan(payload, registry, tmp_path, sandbox)


# ── _resolve_path ─────────────────────────────────────────────────────────────


def test_resolve_path_absolute_inside_sandbox(sandbox, tmp_path):
    result = _resolve_path(str(sandbox / "reports"), tmp_path, sandbox)
    assert result == (sandbox / "reports").resolve()


def test_resolve_path_relative_resolves_via_sandbox(sandbox, tmp_path):
    # Relative paths are now resolved against the sandbox root.
    result = _resolve_path("reports", tmp_path, sandbox)
    assert result == (sandbox / "reports").resolve()


def test_resolve_path_dot_resolves_to_sandbox(sandbox, tmp_path):
    # "." should resolve to the sandbox root itself.
    result = _resolve_path(".", tmp_path, sandbox)
    assert result == sandbox.resolve()


def test_resolve_path_outside_sandbox_raises(sandbox, tmp_path):
    with pytest.raises(ValueError, match="outside sandbox"):
        _resolve_path("/usr/bin", tmp_path, sandbox)


# ── output field validation ───────────────────────────────────────────────────


def test_validate_output_accepts_identifier(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": str(sandbox)}, "output": "$result"}
    validate_step(step, registry, tmp_path, sandbox)  # should not raise


def test_validate_output_rejects_dotted_declaration(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": str(sandbox)}, "output": "$result.files"}
    with pytest.raises(ValueError, match="output"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_output_rejects_missing_dollar(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": str(sandbox)}, "output": "result"}
    with pytest.raises(ValueError, match="output"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_output_rejects_non_string(registry, sandbox, tmp_path):
    step = {"tool": "list_files", "args": {"path": str(sandbox)}, "output": 42}
    with pytest.raises(ValueError, match="output"):
        validate_step(step, registry, tmp_path, sandbox)


# ── $var reference: skip semantic checks ─────────────────────────────────────


def test_validate_var_ref_skips_path_sandbox_check(registry, sandbox, tmp_path):
    # "$target" looks like a $var ref — sandbox check must be skipped
    step = {"tool": "list_files", "args": {"path": "$target"}}
    validate_step(step, registry, tmp_path, sandbox)  # should not raise


def test_validate_var_ref_skips_file_type_enum_check(registry, sandbox, tmp_path):
    step = {"tool": "find_files", "args": {"path": str(sandbox), "file_type": "$kind"}}
    validate_step(step, registry, tmp_path, sandbox)  # should not raise


def test_validate_var_ref_skips_recursive_bool_check(registry, sandbox, tmp_path):
    step = {"tool": "delete_files", "args": {"path": str(sandbox), "recursive": "$flag"}}
    validate_step(step, registry, tmp_path, sandbox)  # should not raise


def test_validate_var_ref_malformed_syntax_raises(registry, sandbox, tmp_path):
    # "$bad ref!" is not a valid reference syntax
    step = {"tool": "list_files", "args": {"path": "$bad ref!"}}
    with pytest.raises(ValueError, match="variable reference"):
        validate_step(step, registry, tmp_path, sandbox)


def test_validate_var_ref_valid_dotted_reference(registry, sandbox, tmp_path):
    # "$market.path" is a valid dotted reference in an arg
    step = {"tool": "list_files", "args": {"path": "$market.path"}}
    validate_step(step, registry, tmp_path, sandbox)  # should not raise


# ── forward-reference check in validate_plan ─────────────────────────────────


def test_validate_forward_ref_valid_chain_passes(registry, sandbox, tmp_path):
    payload = {
        "plan": [
            {"tool": "list_files", "args": {"path": str(sandbox)}, "output": "$found"},
            {"tool": "list_files", "args": {"path": "$found"}},
        ]
    }
    validate_plan(payload, registry, tmp_path, sandbox)  # should not raise


def test_validate_forward_ref_missing_raises(registry, sandbox, tmp_path):
    payload = {
        "plan": [
            {"tool": "list_files", "args": {"path": "$nowhere"}},
            {"tool": "list_files", "args": {"path": str(sandbox)}},
        ]
    }
    with pytest.raises(ValueError, match="\\$nowhere"):
        validate_plan(payload, registry, tmp_path, sandbox)


def test_validate_forward_ref_skipped_for_single_step(registry, sandbox, tmp_path):
    # Single-step plan: $var reference is allowed without a prior assignment
    payload = {"plan": [{"tool": "list_files", "args": {"path": "$target"}}]}
    validate_plan(payload, registry, tmp_path, sandbox)  # should not raise


# ── resolve_args ──────────────────────────────────────────────────────────────


def test_resolve_scalar_var():
    result = resolve_args({"exchange": "$best"}, {"best": "Binance"})
    assert result == {"exchange": "Binance"}


def test_resolve_dotted_var():
    ctx = {"m": {"exchange": "Binance", "price": 41000}}
    result = resolve_args({"exchange": "$m.exchange"}, ctx)
    assert result == {"exchange": "Binance"}


def test_resolve_non_string_passthrough():
    result = resolve_args({"amount": 2, "active": True}, {})
    assert result == {"amount": 2, "active": True}


def test_resolve_missing_var_raises():
    with pytest.raises(ValueError, match="\\$unknown"):
        resolve_args({"x": "$unknown"}, {})


def test_resolve_dotted_missing_field_raises():
    with pytest.raises(ValueError, match="foo"):
        resolve_args({"x": "$m.foo"}, {"m": {"bar": 1}})


def test_resolve_dotted_non_dict_raises():
    with pytest.raises(ValueError, match="\\$m"):
        resolve_args({"x": "$m.foo"}, {"m": "scalar"})


def test_resolve_does_not_mutate_input():
    original = {"path": "$target"}
    resolve_args(original, {"target": "/tmp/sandbox"})
    assert original == {"path": "$target"}


def test_resolve_multiple_args_mixed():
    ctx = {"base": {"dir": "reports"}}
    result = resolve_args({"path": "$base.dir", "count": 5, "tag": "$base.dir"}, ctx)
    assert result == {"path": "reports", "count": 5, "tag": "reports"}


# ── optimize_plan ─────────────────────────────────────────────────────────────

# Helpers: build minimal step dicts for optimizer tests.


def _step(tool, output=None, **args):
    s = {"tool": tool, "args": args}
    if output:
        s["output"] = output
    return s


def test_optimize_single_readonly_kept(registry):
    # Terminal readonly — IS the answer; must not be removed.
    plan = [_step("list_files", path=".")]
    assert optimize_plan(plan, registry) == plan


def test_optimize_two_readonly_no_action_kept(registry):
    # No action step exists; neither readonly step is removable.
    plan = [_step("list_files", path="."), _step("find_files", path=".")]
    assert optimize_plan(plan, registry) == plan


def test_optimize_removes_readonly_no_output_before_action(registry):
    # find_files has no output field → redundant when move_files follows.
    plan = [_step("find_files", path="."), _step("move_files", src=".", dst="dst")]
    result = optimize_plan(plan, registry)
    assert len(result) == 1
    assert result[0]["tool"] == "move_files"


def test_optimize_removes_readonly_unreferenced_output_before_action(registry):
    # find_files declares $found but move_files never uses it.
    plan = [
        _step("find_files", output="$found", path="."),
        _step("move_files", src=".", dst="dst"),
    ]
    result = optimize_plan(plan, registry)
    assert len(result) == 1
    assert result[0]["tool"] == "move_files"


def test_optimize_keeps_readonly_when_output_referenced(registry):
    # find_files output $found is used by move_files src arg → must be kept.
    plan = [
        _step("find_files", output="$found", path="."),
        _step("move_files", src="$found", dst="dst"),
    ]
    result = optimize_plan(plan, registry)
    assert len(result) == 2
    assert result[0]["tool"] == "find_files"


def test_optimize_keeps_readonly_when_dotted_output_referenced(registry):
    # $market.exchange is a dotted reference to $market → find step must be kept.
    plan = [
        _step("find_files", output="$market", path="."),
        _step("move_files", src="$market.exchange", dst="dst"),
    ]
    result = optimize_plan(plan, registry)
    assert len(result) == 2


def test_optimize_never_removes_action_steps(registry):
    plan = [_step("move_files", src=".", dst="dst")]
    assert optimize_plan(plan, registry) == plan


def test_optimize_empty_plan_unchanged(registry):
    assert optimize_plan([], registry) == []


# ── summarize_plan ────────────────────────────────────────────────────────────


def test_summarize_empty_plan():
    assert summarize_plan([]) == "(empty plan)"


def test_summarize_unknown_tool_fallback_no_exception():
    plan = [{"tool": "foo", "args": {"x": 1, "y": "z"}}]
    result = summarize_plan(plan)
    assert result.startswith("Will ")
    assert "foo" in result
    assert "x=1" in result
    assert "y=z" in result


def test_summarize_with_registered_summarizer():
    plan = [{"tool": "convert_video", "args": {"inputs": "clip1.mov", "container": "mp4"}}]
    summarizers = {
        "convert_video": lambda a: f"convert {a['inputs']} to {a['container']}",
    }
    assert summarize_plan(plan, summarizers) == "Will convert clip1.mov to mp4."


def test_summarize_two_steps_joined_with_then():
    plan = [
        {"tool": "convert_video", "args": {"inputs": "a.mov", "container": "mp4"}},
        {"tool": "extract_audio", "args": {"inputs": "a.mov", "fmt": "mp3"}},
    ]
    summarizers = {
        "convert_video": lambda a: f"convert {a['inputs']} to {a['container']}",
        "extract_audio": lambda a: f"extract audio from {a['inputs']} as {a['fmt']}",
    }
    expected = "Will convert a.mov to mp4, then extract audio from a.mov as mp3."
    assert summarize_plan(plan, summarizers) == expected


def test_summarize_terminal_tools_excluded():
    plan = [
        {"tool": "convert_video", "args": {"inputs": "a.mov", "container": "mp4"}},
        {"tool": "done", "args": {}},
        {"tool": "clarify", "args": {"question": "x"}},
        {"tool": "reject", "args": {"reason": "x"}},
    ]
    summarizers = {"convert_video": lambda a: f"convert {a['inputs']} to {a['container']}"}
    assert summarize_plan(plan, summarizers) == "Will convert a.mov to mp4."


def test_summarize_falls_back_when_summarizer_raises():
    plan = [{"tool": "convert_video", "args": {"container": "mp4"}}]

    def broken(_a):
        raise KeyError("inputs")

    result = summarize_plan(plan, {"convert_video": broken})
    assert "convert_video" in result
    assert "container=mp4" in result


def test_summarize_falls_back_when_summarizer_returns_empty():
    plan = [{"tool": "convert_video", "args": {"container": "mp4"}}]
    result = summarize_plan(plan, {"convert_video": lambda _a: ""})
    assert "convert_video" in result
    assert "container=mp4" in result


def test_summarize_kwargs_forwarded_to_summarizer():
    """Extra kwargs passed to summarize_plan reach the summarizer when it accepts them."""
    received: dict = {}

    def rich_summarizer(a: dict, **kw: object) -> str:
        received.update(kw)
        return "done"

    summarize_plan(
        [{"tool": "t", "args": {}}],
        {"t": rich_summarizer},
        skill_dir="/some/path",
        extra="hello",
    )
    assert received == {"skill_dir": "/some/path", "extra": "hello"}


def test_summarize_kwargs_not_forwarded_to_legacy_summarizer():
    """A summarizer that does not accept **kwargs is still called successfully."""
    plan = [{"tool": "convert_video", "args": {"inputs": "a.mov", "container": "mp4"}}]
    # Old-style lambda with a single positional arg — must not raise or fall back.
    summarizers = {"convert_video": lambda a: f"convert {a['inputs']} to {a['container']}"}
    result = summarize_plan(plan, summarizers, skill_dir="/ignored")
    assert result == "Will convert a.mov to mp4."


# ── summarize_plan_steps ──────────────────────────────────────────────────────


def test_summarize_plan_steps_returns_one_clause_per_intent():
    plan = [
        {"tool": "convert_video", "args": {"inputs": "a.mov", "container": "mp4"}},
        {"tool": "extract_audio", "args": {"inputs": "a.mov", "fmt": "mp3"}},
    ]
    summarizers = {
        "convert_video": lambda a: f"convert {a['inputs']} to {a['container']}",
        "extract_audio": lambda a: f"extract audio from {a['inputs']} as {a['fmt']}",
    }
    clauses = summarize_plan_steps(plan, summarizers)
    assert clauses == [
        "convert a.mov to mp4",
        "extract audio from a.mov as mp3",
    ]


def test_summarize_plan_steps_excludes_terminal_tools():
    plan = [
        {"tool": "convert_video", "args": {"inputs": "a.mov", "container": "mp4"}},
        {"tool": "reject", "args": {"reason": "x"}},
    ]
    clauses = summarize_plan_steps(plan, {"convert_video": lambda a: "convert"})
    assert clauses == ["convert"]


def test_summarize_plan_steps_empty_plan():
    assert summarize_plan_steps([]) == []


def test_summarize_plan_is_consistent_with_steps():
    """summarize_plan must produce 'Will <steps joined>, then ....'"""
    plan = [
        {"tool": "a", "args": {}},
        {"tool": "b", "args": {}},
    ]
    summarizers = {"a": lambda _: "do A", "b": lambda _: "do B"}
    clauses = summarize_plan_steps(plan, summarizers)
    combined = summarize_plan(plan, summarizers)
    assert combined == "Will " + ", then ".join(clauses) + "."


# ── normalize_plan ───────────────────────────────────────────────────────────


def test_normalize_promotes_var_output_from_args():
    """args['output'] = '$var' should be lifted to step-level output."""
    payload = {
        "plan": [
            {
                "tool": "trim_video",
                "args": {"input": "a.mov", "start": "00:00:02", "output": "$trimmed"},
            },
        ]
    }
    normalize_plan(payload)
    step = payload["plan"][0]
    assert step.get("output") == "$trimmed"
    assert "output" not in step["args"]


def test_normalize_leaves_filename_output_in_args():
    """args['output'] = 'file.mp4' (not a $var) must NOT be promoted."""
    payload = {
        "plan": [
            {"tool": "concat_video", "args": {"base": "a.mov", "output": "out.mp4"}},
        ]
    }
    normalize_plan(payload)
    step = payload["plan"][0]
    assert "output" not in step  # no top-level output field added
    assert step["args"]["output"] == "out.mp4"


def test_normalize_does_not_overwrite_existing_step_output():
    """If step already has a top-level output, args['output'] is left as-is."""
    payload = {
        "plan": [
            {
                "tool": "trim_video",
                "args": {"input": "a.mov", "output": "$trimmed"},
                "output": "$already_set",
            }
        ]
    }
    normalize_plan(payload)
    step = payload["plan"][0]
    assert step["output"] == "$already_set"
    assert step["args"]["output"] == "$trimmed"  # not removed


def _make_scalar_input_registry() -> dict:
    """Minimal registry with scalar-input and plural-input tools for coercion tests."""
    from knaif.registry import ToolDef

    return {
        "extract_frame": ToolDef(
            name="extract_frame",
            description="Extract a frame",
            required_args=("input",),
            optional_args=("at_time", "image_format", "scale"),
        ),
        "create_thumbnail": ToolDef(
            name="create_thumbnail",
            description="Create a thumbnail",
            required_args=("input",),
            optional_args=("at_time", "image_format", "scale"),
        ),
        "trim_video": ToolDef(
            name="trim_video",
            description="Trim a video",
            required_args=("input",),
            optional_args=("start", "end", "duration", "output", "preview"),
        ),
        "convert_video": ToolDef(
            name="convert_video",
            description="Convert videos",
            required_args=("inputs",),
            optional_args=("container", "video_codec", "audio_codec", "quality", "preview"),
        ),
        "adjust_speed": ToolDef(
            name="adjust_speed",
            description="Change playback speed",
            required_args=("inputs", "speed"),
            optional_args=("quality", "preview"),
        ),
    }


def test_normalize_coerces_inputs_list_to_input_for_scalar_tool():
    """Single-element inputs list is coerced to scalar input for extract_frame."""
    reg = _make_scalar_input_registry()
    payload = {
        "plan": [{"tool": "extract_frame", "args": {"inputs": ["clip.mp4"], "at_time": "00:00:03"}}]
    }
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["input"] == "clip.mp4"
    assert "inputs" not in step["args"]


def test_normalize_coerces_inputs_string_to_input_for_scalar_tool():
    """Bare-string inputs is coerced to scalar input for extract_frame."""
    reg = _make_scalar_input_registry()
    payload = {
        "plan": [{"tool": "extract_frame", "args": {"inputs": "clip.mp4", "at_time": "00:00:05"}}]
    }
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["input"] == "clip.mp4"
    assert "inputs" not in step["args"]


def test_normalize_coerces_inputs_for_create_thumbnail():
    """create_thumbnail (scalar input tool) also gets inputs coerced."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"]}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["input"] == "clip.mp4"
    assert "inputs" not in step["args"]


def test_normalize_coerces_inputs_for_trim_video():
    """trim_video (scalar input tool) also gets inputs coerced."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "trim_video", "args": {"inputs": ["clip.mp4"]}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["input"] == "clip.mp4"
    assert "inputs" not in step["args"]


def test_normalize_leaves_plural_input_tool_untouched():
    """convert_video (inputs tool) must NOT have inputs rewritten."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "convert_video", "args": {"inputs": ["a.mp4", "b.mp4"]}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["inputs"] == ["a.mp4", "b.mp4"]
    assert "input" not in step["args"]


def test_normalize_leaves_multi_element_inputs_for_scalar_tool():
    """Multi-file inputs on a scalar tool stays untouched — let validation reject it."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "extract_frame", "args": {"inputs": ["a.mp4", "b.mp4"]}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["inputs"] == ["a.mp4", "b.mp4"]
    assert "input" not in step["args"]


# ── mirror direction: scalar input → plural inputs ────────────────────────────


def test_normalize_coerces_input_string_to_inputs_for_plural_tool():
    """adjust_speed (plural-inputs tool) given scalar input string → wrapped in a list."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "adjust_speed", "args": {"input": "clip.mp4", "speed": 2.0}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["inputs"] == ["clip.mp4"]
    assert "input" not in step["args"]


def test_normalize_coerces_input_for_convert_video():
    """convert_video (plural-inputs tool) given scalar input → wrapped in a list."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "convert_video", "args": {"input": "clip.mp4"}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["inputs"] == ["clip.mp4"]
    assert "input" not in step["args"]


def test_normalize_input_to_inputs_moves_list_value():
    """If the scalar 'input' key holds a list, move it wholesale to 'inputs'."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "convert_video", "args": {"input": ["a.mp4", "b.mp4"]}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["inputs"] == ["a.mp4", "b.mp4"]
    assert "input" not in step["args"]


def test_normalize_leaves_scalar_tool_input_as_input():
    """A scalar-input tool (extract_frame) with scalar input must NOT be rewritten to inputs."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "extract_frame", "args": {"input": "clip.mp4"}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    assert step["args"]["input"] == "clip.mp4"
    assert "inputs" not in step["args"]


def test_normalize_does_not_clobber_existing_inputs_with_input():
    """If both input and inputs are present on a plural tool, leave them for validation."""
    reg = _make_scalar_input_registry()
    payload = {"plan": [{"tool": "convert_video", "args": {"input": "a.mp4", "inputs": ["b.mp4"]}}]}
    normalize_plan(payload, reg)
    step = payload["plan"][0]
    # No coercion when inputs already present — don't silently merge/overwrite.
    assert step["args"]["inputs"] == ["b.mp4"]


def test_normalize_input_to_inputs_skipped_when_no_registry():
    """Without a registry, the mirror coercion is not attempted."""
    payload = {"plan": [{"tool": "adjust_speed", "args": {"input": "clip.mp4", "speed": 2.0}}]}
    normalize_plan(payload)  # no registry
    step = payload["plan"][0]
    assert step["args"]["input"] == "clip.mp4"
    assert "inputs" not in step["args"]


def test_normalize_coercion_skipped_when_no_registry():
    """Without a registry, inputs coercion is not attempted (no crash, no mutation)."""
    payload = {"plan": [{"tool": "extract_frame", "args": {"inputs": ["clip.mp4"]}}]}
    normalize_plan(payload)  # no registry
    step = payload["plan"][0]
    assert step["args"]["inputs"] == ["clip.mp4"]
    assert "input" not in step["args"]


def test_normalize_plan_coerces_enum_aliases_and_case():
    """Schema-declared enum aliases + case-insensitive enum match are coerced
    before validation, so model synonyms like 'markdown'→'md' don't get rejected."""
    from knaif.registry import ToolDef

    reg = {
        "convert_document": ToolDef(
            name="convert_document",
            description="convert",
            required_args=("input", "to_format"),
            arg_schemas={
                "to_format": ArgSchema(
                    type="enum",
                    enum=("pdf", "txt", "md", "png", "jpg"),
                    aliases={"markdown": "md", "text": "txt", "jpeg": "jpg"},
                )
            },
        ),
    }

    def _run(value):
        p = {"plan": [{"tool": "convert_document", "args": {"input": "a.txt", "to_format": value}}]}
        normalize_plan(p, reg)
        return p["plan"][0]["args"]["to_format"]

    assert _run("markdown") == "md"  # explicit alias
    assert _run("MD") == "md"  # case-insensitive enum match
    assert _run("Markdown") == "md"  # alias + case
    assert _run("pdf") == "pdf"  # already valid, untouched
    assert _run("xyz") == "xyz"  # unknown left for validation to reject


def test_normalize_plan_arg_key_alias_and_type_coercion():
    """Per-tool arg-key aliases (model uses a sibling key) and int→str coercion
    for string-typed args are applied before validation — gemma's wrong-key /
    wrong-type modes (e.g. split_pdf given `pages` not `ranges`, or pages=2)."""
    from knaif.registry import ToolDef

    reg = {
        "split_pdf": ToolDef(
            name="split_pdf",
            description="split",
            required_args=("input", "ranges"),
            arg_aliases={"pages": "ranges"},
            arg_schemas={"ranges": ArgSchema(type="string")},
        ),
        "rotate_pages": ToolDef(
            name="rotate_pages",
            description="rotate",
            required_args=("input", "degrees"),
            optional_args=("pages",),
            arg_schemas={"pages": ArgSchema(type="string"), "degrees": ArgSchema(type="integer")},
        ),
    }

    # arg-key alias: pages → ranges (only for split_pdf)
    p = {"plan": [{"tool": "split_pdf", "args": {"input": "a.pdf", "pages": "2-3"}}]}
    normalize_plan(p, reg)
    assert p["plan"][0]["args"] == {"input": "a.pdf", "ranges": "2-3"}

    # int → str for a string-typed arg; integer-typed arg left alone
    p = {"plan": [{"tool": "rotate_pages", "args": {"input": "a.pdf", "degrees": 180, "pages": 2}}]}
    normalize_plan(p, reg)
    assert p["plan"][0]["args"]["pages"] == "2"
    assert p["plan"][0]["args"]["degrees"] == 180

    # alias must not clobber an explicitly-supplied canonical key
    p = {"plan": [{"tool": "split_pdf", "args": {"input": "a.pdf", "pages": "1", "ranges": "2"}}]}
    normalize_plan(p, reg)
    assert p["plan"][0]["args"]["ranges"] == "2"


def test_normalize_plan_enum_separator_insensitive():
    """Models emit snake_case or spaced forms of hyphenated enums (e.g. watermark
    position 'bottom_center' for 'bottom-center'). Normalize separators (_ / space
    → -) alongside case before validation so the value is accepted rather than
    rejected for a cosmetic difference."""
    from knaif.registry import ToolDef

    reg = {
        "watermark": ToolDef(
            name="watermark",
            description="watermark",
            required_args=("input", "text"),
            optional_args=("position",),
            arg_schemas={
                "position": ArgSchema(
                    type="enum",
                    enum=("top-left", "top-center", "center", "bottom-center", "bottom-right"),
                )
            },
        ),
    }

    def _run(value):
        p = {
            "plan": [
                {"tool": "watermark", "args": {"input": "a.pdf", "text": "X", "position": value}}
            ]
        }
        normalize_plan(p, reg)
        return p["plan"][0]["args"]["position"]

    assert _run("bottom_center") == "bottom-center"  # snake_case → hyphen
    assert _run("bottom center") == "bottom-center"  # space → hyphen
    assert _run("BOTTOM_CENTER") == "bottom-center"  # case + separator
    assert _run("center") == "center"  # already valid, untouched
    assert _run("nowhere") == "nowhere"  # unknown left for validation


def test_normalize_plan_numeric_string_to_number():
    """A numeric string for an integer/number-typed arg is coerced to the numeric
    type before validation (qwen sometimes quotes degrees: '180'). Non-numeric
    strings and variable references are left untouched for later stages."""
    from knaif.registry import ToolDef

    reg = {
        "rotate_pages": ToolDef(
            name="rotate_pages",
            description="rotate",
            required_args=("input", "degrees"),
            optional_args=("scale",),
            arg_schemas={"degrees": ArgSchema(type="integer"), "scale": ArgSchema(type="number")},
        ),
    }

    def _run(args):
        p = {"plan": [{"tool": "rotate_pages", "args": {"input": "a.pdf", **args}}]}
        normalize_plan(p, reg)
        return p["plan"][0]["args"]

    assert _run({"degrees": "180"})["degrees"] == 180  # int string → int
    assert _run({"degrees": 90})["degrees"] == 90  # already int, untouched
    assert _run({"degrees": 90, "scale": "1.5"})["scale"] == 1.5  # float string → number
    assert _run({"degrees": "ninety"})["degrees"] == "ninety"  # non-numeric left as-is
    assert _run({"degrees": "$deg"})["degrees"] == "$deg"  # var ref left as-is
    assert _run({"degrees": "1.5"})["degrees"] == "1.5"  # float string not forced to int


def test_summarize_no_skill_imports_in_planner():  # noqa: E302 (kept near end by convention)
    """Core summarize_plan must not import or reference any skill by name."""
    from pathlib import Path

    src = Path("python") / "core" / "knaif" / "planner.py"
    text = src.read_text(encoding="utf-8")
    for needle in ("ffmpeg", "prepare_for_platform", "compress_video"):
        assert needle not in text, f"skill name {needle!r} leaked into core planner.py"


# ── apply_defaults ────────────────────────────────────────────────────────────


def _make_registry_with_defaults(defaults: dict) -> dict:
    """Return a minimal registry where 'my_tool' has the given defaults."""
    from knaif.registry import ToolDef

    return {
        "my_tool": ToolDef(
            name="my_tool",
            description="A tool",
            required_args=("input",),
            optional_args=("output",),
            defaults=defaults,
        )
    }


def test_apply_defaults_fills_missing_arg():
    from knaif.planner import apply_defaults

    reg = _make_registry_with_defaults({"output": "combined.mp4"})
    plan = {"plan": [{"tool": "my_tool", "args": {"input": "clip.mp4"}}]}
    apply_defaults(plan, reg)
    assert plan["plan"][0]["args"]["output"] == "combined.mp4"


def test_apply_defaults_does_not_override_explicit_value():
    from knaif.planner import apply_defaults

    reg = _make_registry_with_defaults({"output": "combined.mp4"})
    plan = {"plan": [{"tool": "my_tool", "args": {"input": "clip.mp4", "output": "custom.mp4"}}]}
    apply_defaults(plan, reg)
    assert plan["plan"][0]["args"]["output"] == "custom.mp4"


def test_apply_defaults_noop_for_tool_without_defaults():
    from knaif.planner import apply_defaults

    reg = _make_registry_with_defaults({})
    plan = {"plan": [{"tool": "my_tool", "args": {"input": "clip.mp4"}}]}
    apply_defaults(plan, reg)
    assert "output" not in plan["plan"][0]["args"]


def test_apply_defaults_noop_for_unknown_tool():
    from knaif.planner import apply_defaults

    reg = _make_registry_with_defaults({"output": "combined.mp4"})
    plan = {"plan": [{"tool": "unknown_tool", "args": {}}]}
    apply_defaults(plan, reg)  # must not raise
    assert plan["plan"][0]["args"] == {}


# ── resolve_stems ─────────────────────────────────────────────────────────────


def test_resolve_stems_substitutes_matching_stem(tmp_path):
    (tmp_path / "clip_4k.mp4").touch()
    result = resolve_stems({"inputs": ["clip_4k"]}, tmp_path)
    assert result["inputs"] == ["clip_4k.mp4"]


def test_resolve_stems_scalar_input_key(tmp_path):
    (tmp_path / "clip_4k.mp4").touch()
    result = resolve_stems({"input": "clip_4k"}, tmp_path)
    assert result["input"] == "clip_4k.mp4"


def test_resolve_stems_files_key(tmp_path):
    (tmp_path / "clip_4k.mp4").touch()
    result = resolve_stems({"files": ["clip_4k"]}, tmp_path)
    assert result["files"] == ["clip_4k.mp4"]


def test_resolve_stems_not_found_raises(tmp_path):
    with pytest.raises(StemNotFoundError, match="clip_missing"):
        resolve_stems({"inputs": ["clip_missing"]}, tmp_path)


def test_resolve_stems_ambiguous_raises(tmp_path):
    (tmp_path / "clip_4k.mp4").touch()
    (tmp_path / "clip_4k.mov").touch()
    with pytest.raises(StemAmbiguousError, match="clip_4k"):
        resolve_stems({"inputs": ["clip_4k"]}, tmp_path)


def test_resolve_stems_ignores_exact_filename(tmp_path):
    # Value already has an extension — unchanged even if no file exists.
    args = {"inputs": ["clip.mp4"]}
    assert resolve_stems(args, tmp_path) == args


def test_resolve_stems_ignores_glob(tmp_path):
    args = {"inputs": ["*.mp4"]}
    assert resolve_stems(args, tmp_path) == args


def test_resolve_stems_ignores_chain_ref(tmp_path):
    args = {"inputs": ["$prev"]}
    assert resolve_stems(args, tmp_path) == args


def test_resolve_stems_ignores_non_path_key(tmp_path):
    # "container" is not a path-bearing key — not touched.
    args = {"container": "mp4", "quality": "high"}
    assert resolve_stems(args, tmp_path) == args


def test_resolve_stems_ignores_short_word_no_structure(tmp_path):
    # "mov" has a digit? No. "mov" = letters only, no underscore/hyphen/digit → not a stem.
    args = {"inputs": ["mov"]}
    assert resolve_stems(args, tmp_path) == args


def test_resolve_stems_list_mixed_exact_and_stem(tmp_path):
    (tmp_path / "clip_4k.mp4").touch()
    result = resolve_stems({"inputs": ["clip.mp4", "clip_4k"]}, tmp_path)
    assert result["inputs"] == ["clip.mp4", "clip_4k.mp4"]


def test_resolve_stems_does_not_mutate_input(tmp_path):
    (tmp_path / "clip_4k.mp4").touch()
    original = {"inputs": ["clip_4k"], "container": "mp4"}
    resolve_stems(original, tmp_path)
    assert original["inputs"] == ["clip_4k"]  # original unchanged


# ── validate_arg_by_schema ────────────────────────────────────────────────────


def test_validate_arg_string_ok():
    validate_arg_by_schema("name", "hello", ArgSchema(type="string"))


def test_validate_arg_string_wrong_type():
    with pytest.raises(ValueError, match="'name' must be a string"):
        validate_arg_by_schema("name", 42, ArgSchema(type="string"))


def test_validate_arg_integer_ok():
    validate_arg_by_schema("count", 5, ArgSchema(type="integer"))


def test_validate_arg_integer_wrong_type():
    with pytest.raises(ValueError, match="'count' must be an integer"):
        validate_arg_by_schema("count", 5.0, ArgSchema(type="integer"))


def test_validate_arg_boolean_wrong_type():
    with pytest.raises(ValueError, match="'flag' must be a boolean"):
        validate_arg_by_schema("flag", "yes", ArgSchema(type="boolean"))


def test_validate_arg_boolean_ok():
    validate_arg_by_schema("flag", True, ArgSchema(type="boolean"))


def test_validate_arg_number_ok():
    validate_arg_by_schema("ratio", 0.5, ArgSchema(type="number"))


def test_validate_arg_number_wrong_type():
    with pytest.raises(ValueError, match="'ratio' must be a number"):
        validate_arg_by_schema("ratio", "0.5", ArgSchema(type="number"))


def test_validate_arg_enum_ok():
    validate_arg_by_schema("fmt", "iso", ArgSchema(type="enum", enum=("iso", "unix", "human")))


def test_validate_arg_enum_bad_value():
    with pytest.raises(ValueError, match="'fmt' must be one of"):
        validate_arg_by_schema("fmt", "xml", ArgSchema(type="enum", enum=("iso", "unix", "human")))


def test_validate_arg_enum_no_values_skips():
    # enum with no values defined — no error
    validate_arg_by_schema("x", "anything", ArgSchema(type="enum", enum=None))


def test_validate_arg_integer_min():
    with pytest.raises(ValueError, match=">= 1"):
        validate_arg_by_schema("n", 0, ArgSchema(type="integer", min=1))


def test_validate_arg_integer_max():
    with pytest.raises(ValueError, match="<= 10"):
        validate_arg_by_schema("n", 11, ArgSchema(type="integer", max=10))


def test_validate_arg_var_ref_passes():
    # $var references should pass through without type-checking
    validate_arg_by_schema("name", "$my_var", ArgSchema(type="integer"))


def test_validate_arg_array_ok():
    validate_arg_by_schema("items", ["a", "b"], ArgSchema(type="array", items="string"))


def test_validate_arg_array_wrong_type():
    with pytest.raises(ValueError, match="'items' must be an array"):
        validate_arg_by_schema("items", "not_a_list", ArgSchema(type="array"))


# ── validate_step uses arg_schemas ────────────────────────────────────────────


def test_validate_step_schema_enum_ok(tmp_path):
    from knaif.registry import ToolDef

    td = ToolDef(
        name="clock_now",
        description="Current time",
        required_args=("fmt",),
        arg_schemas={"fmt": ArgSchema(type="enum", enum=("iso", "unix", "human"))},
    )
    reg = {"clock_now": td}
    step = {"tool": "clock_now", "args": {"fmt": "iso"}}
    validate_step(step, reg, tmp_path)  # must not raise


def test_validate_step_schema_enum_bad(tmp_path):
    from knaif.registry import ToolDef

    td = ToolDef(
        name="clock_now",
        description="Current time",
        required_args=("fmt",),
        arg_schemas={"fmt": ArgSchema(type="enum", enum=("iso", "unix", "human"))},
    )
    reg = {"clock_now": td}
    step = {"tool": "clock_now", "args": {"fmt": "xml"}}
    with pytest.raises(ValueError, match="must be one of"):
        validate_step(step, reg, tmp_path)


def test_validate_step_schema_integer_bounds(tmp_path):
    from knaif.registry import ToolDef

    td = ToolDef(
        name="repeat",
        description="Repeat",
        required_args=("count",),
        arg_schemas={"count": ArgSchema(type="integer", min=1, max=10)},
    )
    reg = {"repeat": td}
    step = {"tool": "repeat", "args": {"count": 0}}
    with pytest.raises(ValueError, match=">= 1"):
        validate_step(step, reg, tmp_path)
