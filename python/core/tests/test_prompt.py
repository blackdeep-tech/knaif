"""Tests for knaif.prompt."""

from __future__ import annotations

from knaif.prompt import build_prompt
from knaif.registry import ArgSchema, ToolDef


def test_build_prompt_returns_tuple(agent):
    system, user = agent.build_prompt("list files in reports")
    assert isinstance(system, str)
    assert isinstance(user, str)


def test_user_message_is_utterance(agent):
    _, user = agent.build_prompt("find all pdfs")
    assert user == "find all pdfs"


def test_windows_path_token_is_normalized_to_forward_slashes(agent):
    _, user = agent.build_prompt(r"convert .\clip.mov to mp4")
    assert user == "convert ./clip.mov to mp4"


def test_windows_absolute_path_token_is_normalized(agent):
    _, user = agent.build_prompt(r"compress C:\Users\me\clip.mp4")
    assert user == "compress C:/Users/me/clip.mp4"


def test_non_path_backslash_is_left_alone(agent):
    _, user = agent.build_prompt(r"replace \ with something")
    assert user == r"replace \ with something"


def test_system_contains_tool_names(agent):
    system, _ = agent.build_prompt("x")
    assert "list_files" in system
    assert "find_files" in system
    assert "delete_files" in system
    assert "move_files" in system


def test_system_omits_system_tools(agent):
    # clarify and reject are system tools; they appear in examples but not in
    # the "Available tools:" block as regular entries.
    system, _ = agent.build_prompt("x")
    assert "Available tools:" in system
    # They should appear in the examples section, not as tool definitions.
    tool_block = system.split("Available tools:")[1].split("Examples:")[0]
    assert "clarify" not in tool_block
    assert "reject" not in tool_block


def test_system_contains_examples(agent):
    system, _ = agent.build_prompt("x")
    assert "Examples:" in system
    assert "clarify" in system
    assert "reject" in system


def test_system_contains_copy_into_move_example(agent):
    system, _ = agent.build_prompt("x")
    assert "copy all text files from dir folder into newfolder folder" in system
    assert '"tool": "move_files"' in system
    assert '"file_type": "text"' in system


def test_system_contains_required_args(agent):
    system, _ = agent.build_prompt("x")
    assert "path (required)" in system


def test_system_contains_optional_args(agent):
    system, _ = agent.build_prompt("x")
    assert "pattern (optional)" in system


def test_arg_help_rendered_in_required_args():
    reg = {
        "convert": ToolDef(
            name="convert",
            description="Convert a datetime between timezones.",
            required_args=("value",),
            optional_args=("to_tz",),
            arg_schemas={
                "value": ArgSchema(type="string", help="Input datetime (e.g. 2026-06-20T15:00)."),
            },
        )
    }
    system, _ = build_prompt("convert something", reg)
    assert "value (required, Input datetime" in system


def test_arg_help_rendered_in_optional_args():
    reg = {
        "now": ToolDef(
            name="now",
            description="Return current time.",
            required_args=(),
            optional_args=("fmt",),
            arg_schemas={
                "fmt": ArgSchema(type="enum", enum=("iso", "unix"), help="Output format."),
            },
        )
    }
    system, _ = build_prompt("what time", reg)
    assert "fmt (optional, Output format.)" in system


def test_arg_without_help_renders_without_hint():
    reg = {
        "noop_tool": ToolDef(
            name="noop_tool",
            description="Do nothing.",
            required_args=("path",),
            arg_schemas={},
        )
    }
    system, _ = build_prompt("do nothing", reg)
    assert "path (required)" in system
    assert "path (required," not in system


# ── history ───────────────────────────────────────────────────────────────────


def test_build_prompt_empty_history_no_history_section(agent):
    system, _ = agent.build_prompt("list files", history=[])
    assert "Completed steps" not in system


def test_build_prompt_with_history_injects_block(agent):
    history = [
        {
            "step": {"tool": "list_files", "args": {"path": "reports"}},
            "result": {"count": 2, "files": ["reports/a.txt", "reports/b.txt"]},
        }
    ]
    system, _ = agent.build_prompt("list files", history=history)
    assert "Completed steps" in system
    assert "list_files" in system
    assert "2 files" in system


def test_build_prompt_history_includes_next_step_instruction(agent):
    history = [
        {
            "step": {"tool": "find_files", "args": {"path": "."}},
            "result": {"count": 0, "files": []},
        }
    ]
    system, _ = agent.build_prompt("find files", history=history)
    assert "NEXT" in system or "done" in system


# ── phase 2: chaining prompt ──────────────────────────────────────────────────


def test_system_header_allows_multi_step(agent):
    system, _ = agent.build_prompt("x")
    assert "one step" not in system.lower()


def test_system_header_has_chaining_rule(agent):
    system, _ = agent.build_prompt("x")
    assert '"output"' in system


def test_header_describes_chaining_pattern(agent):
    # The io skill header explains the $varname / $varname.field chaining syntax.
    # We do not use a chaining example in io/prompt.yaml because io tools accept
    # path+file_type args directly, making a find→delete chain redundant per the rules.
    system, _ = agent.build_prompt("x")
    assert "$varname" in system


def test_format_history_annotates_output_var():
    from knaif.prompt import _format_history

    history = [
        {
            "step": {"tool": "find_files", "args": {"path": "."}, "output": "$market"},
            "result": {"count": 2, "files": ["a.txt", "b.txt"]},
        }
    ]
    assert "[→ $market]" in _format_history(history)


def test_format_history_no_annotation_without_output():
    from knaif.prompt import _format_history

    history = [
        {
            "step": {"tool": "list_files", "args": {"path": "."}},
            "result": {"count": 1, "files": ["a.txt"]},
        }
    ]
    assert "[→" not in _format_history(history)


# ── _summarize_result ─────────────────────────────────────────────────────────


def test_summarize_result_deleted_count():
    from knaif.prompt import _summarize_result

    assert _summarize_result({"deleted_count": 3}) == "deleted 3 files"


def test_summarize_result_moved_count():
    from knaif.prompt import _summarize_result

    assert _summarize_result({"moved_count": 2}) == "moved 2 files"


def test_summarize_result_would_delete():
    from knaif.prompt import _summarize_result

    assert _summarize_result({"would_delete_count": 5}) == "would delete 5 files (dry run)"


def test_summarize_result_would_move():
    from knaif.prompt import _summarize_result

    assert _summarize_result({"would_move_count": 1}) == "would move 1 files (dry run)"


def test_summarize_result_question():
    from knaif.prompt import _summarize_result

    assert _summarize_result({"question": "Which folder?"}) == "asked: Which folder?"


def test_summarize_result_reason():
    from knaif.prompt import _summarize_result

    assert _summarize_result({"reason": "Unsafe."}) == "rejected: Unsafe."


def test_summarize_result_fallback():
    from knaif.prompt import _summarize_result

    result = _summarize_result({"custom_key": "custom_val"})
    assert result == str({"custom_key": "custom_val"})


def test_summarize_result_count_many_files():
    from knaif.prompt import _summarize_result

    files = [f"file{i}.txt" for i in range(7)]
    result = _summarize_result({"count": 7, "files": files})
    assert "7 files" in result
    assert "…" in result


def test_summarize_result_count_zero_files():
    from knaif.prompt import _summarize_result

    result = _summarize_result({"count": 0, "files": []})
    assert result == "0 files"
