"""Prompt builder for the command planner."""

from __future__ import annotations

import json as _json
import re

from .registry import ToolDef

# ── utterance normalization ───────────────────────────────────────────────────

# A token made only of path characters, with at least one alphanumeric so a bare
# "\" stays literal. No spaces, so a quoted path containing spaces is left alone
# (it is not a single token anyway).
_PATH_TOKEN_RE = re.compile(r"[\w\-.:\\/]*[a-zA-Z0-9][\w\-.:\\/]*", re.ASCII)


def normalize_path_separators(utterance: str) -> str:
    """Rewrite Windows-style path tokens (``.\\clip.mov``) to forward slashes.

    A backslash inside a JSON string must be escaped, and small models get this
    wrong: given ``.\\clip.mov`` they emit ``"\\clip.mov"`` — the dot is lost and
    the path becomes drive-root-relative, so it resolves to ``C:\\clip.mov`` and
    the file is reported missing. Forward slashes need no escaping and work on
    every platform, so hand the model ``./clip.mov`` instead.

    The native runtime already does this in ``knaif-cli``'s ``build_plan``
    (``normalize_path_separators`` in ``apps/cli/src/main.rs``); this is
    the Python counterpart. Native rewrites every backslash in the utterance,
    Python only path-shaped tokens — same result on any file-path utterance.
    """
    if "\\" not in utterance:
        return utterance
    return " ".join(
        token.replace("\\", "/") if "\\" in token and _PATH_TOKEN_RE.fullmatch(token) else token
        for token in utterance.split(" ")
    )


# ── example selection helpers ─────────────────────────────────────────────────

_TERMINAL_TOOLS = frozenset({"clarify", "reject", "done"})


def _example_tool_set(example: dict) -> frozenset[str]:
    """Return the set of non-terminal tools referenced in *example*'s plan."""
    output = example.get("output") or {}
    plan = output.get("plan") or []
    return frozenset(
        step.get("tool", "")
        for step in plan
        if isinstance(step, dict) and step.get("tool") and step.get("tool") not in _TERMINAL_TOOLS
    )


def _is_clarify_example(example: dict) -> bool:
    output = example.get("output") or {}
    plan = output.get("plan") or []
    tools = {step.get("tool") for step in plan if isinstance(step, dict) and step.get("tool")}
    return bool(tools) and "clarify" in tools and not _example_tool_set(example)


def _is_reject_example(example: dict) -> bool:
    output = example.get("output") or {}
    plan = output.get("plan") or []
    tools = {step.get("tool") for step in plan if isinstance(step, dict) and step.get("tool")}
    return bool(tools) and "reject" in tools and not _example_tool_set(example)


def select_examples(
    structured_examples: list[dict],
    retrieved_tool_names: frozenset[str],
    query: str,
    max_tool_examples: int = 3,
) -> list[dict]:
    """Select the most relevant examples for *query* given the retrieved tool set.

    Always includes one clarify example and one reject example (when available).
    Fills up to *max_tool_examples* domain examples ranked by:
      1. number of retrieved tools that appear in the example's plan (primary)
      2. Jaccard overlap between query tokens and example request tokens (tiebreaker)
    Examples are returned in their original order from *structured_examples*.
    """
    clarify_exs = [e for e in structured_examples if _is_clarify_example(e)]
    reject_exs = [e for e in structured_examples if _is_reject_example(e)]
    domain_exs = [e for e in structured_examples if _example_tool_set(e)]

    fixed: list[dict] = []
    if clarify_exs:
        fixed.append(clarify_exs[0])
    if reject_exs:
        fixed.append(reject_exs[0])

    query_tokens = set(query.lower().split())

    def _score(ex: dict) -> tuple[int, int]:
        tool_overlap = len(_example_tool_set(ex) & retrieved_tool_names)
        req_tokens = set(ex.get("request", "").lower().split())
        return (tool_overlap, len(query_tokens & req_tokens))

    ranked_domain = sorted(domain_exs, key=_score, reverse=True)
    selected_domain = ranked_domain[:max_tool_examples]

    # Re-emit in original order, deduplicating
    fixed_ids = {id(e) for e in fixed}
    selected_ids = {id(e) for e in selected_domain}
    result: list[dict] = []
    for ex in structured_examples:
        eid = id(ex)
        if eid in fixed_ids or eid in selected_ids:
            result.append(ex)
            fixed_ids.discard(eid)
            selected_ids.discard(eid)
    return result


def render_examples_block(examples: list[dict]) -> str:
    """Render a list of example dicts to the 'Examples:' string block."""
    lines = ["Examples:"]
    for ex in examples:
        request = ex.get("request", "")
        output = ex.get("output")
        lines.append(f'  request: "{request}"')
        if output is not None:
            lines.append(f"  output:  {_json.dumps(output, separators=(', ', ': '))}")
        lines.append("")
    return "\n".join(lines)


_SYSTEM_HEADER = """\
You are a command planner. Output ONLY a JSON object — no explanation.
{ "plan": [ { "tool": "<name>", "args": { <params> }, "output": "$var" }, ... ] }

Rules:
- Emit ONLY the JSON object.
- Use clarify if a required parameter is missing or the request is ambiguous.
- File paths are sandbox-relative; use "." for the sandbox root.
- Use move_files for requests to move, copy, transfer, or place files into a destination folder.
- Preserve explicit file filters such as "text files" with file_type when the action tool supports it.
- When a later step needs a value produced by an earlier step, add "output": "$varname" to the earlier step and reference "$varname" (or "$varname.field") in the later step's args.
- Do NOT include a find/list step if the following action step already accepts the same path/pattern/file_type args — it is redundant.
- Output {"plan":[{"tool":"done","args":{}}]} when the task is fully complete.
"""

_EXAMPLES = """
Examples:
  request: "list text files in reports"
  output:  { "plan": [ { "tool": "list_files", "args": { "path": "reports", "file_type": "text" } } ] }

  request: "find executable files"
  output:  { "plan": [ { "tool": "find_files", "args": { "path": ".", "file_type": "executable" } } ] }

  request: "delete tmp files"
  output:  { "plan": [ { "tool": "clarify", "args": { "question": "Which folder contains the tmp files?" } } ] }

  request: "move all files to src folder"
  output:  { "plan": [ { "tool": "move_files", "args": { "src": ".", "dst": "src" } } ] }

    request: "copy all text files from dir folder into newfolder folder"
    output:  { "plan": [ { "tool": "move_files", "args": { "src": "dir", "dst": "newfolder", "file_type": "text" } } ] }

  request: "nuke the server"
  output:  { "plan": [ { "tool": "reject", "args": { "reason": "Request is unsafe." } } ] }

  request: "find text files and move them to archive"
  completed: "1. find_files(path=\".\", file_type=\"text\") → 3 files: a.txt, b.txt, c.txt"
  output:  { "plan": [ { "tool": "move_files", "args": { "src": ".", "dst": "archive", "file_type": "text" } } ] }

  request: "find text files and move them to archive"
  completed: "1. find_files(...) → 3 files: a.txt  2. move_files(...) → moved 3 files"
  output:  { "plan": [ { "tool": "done", "args": {} } ] }

  request: "find the exchange with the lowest BTC price and buy 2 BTC"
  output:  { "plan": [ { "tool": "find_lowest_btc_exchange", "args": {}, "output": "$market" }, { "tool": "buy_btc", "args": { "exchange": "$market.exchange", "amount": 2 } } ] }
"""

_SYSTEM_TOOLS = {"clarify", "reject", "done", "noop"}


def _summarize_result(result: dict) -> str:
    if "count" in result:
        files = result.get("files", [])
        names = [p.replace("\\", "/").split("/")[-1] for p in files[:5]]
        suffix = "…" if len(files) > 5 else ""
        return (
            f"{result['count']} files: {', '.join(names)}{suffix}"
            if names
            else f"{result['count']} files"
        )
    if "deleted_count" in result:
        return f"deleted {result['deleted_count']} files"
    if "moved_count" in result:
        return f"moved {result['moved_count']} files"
    if "would_delete_count" in result:
        return f"would delete {result['would_delete_count']} files (dry run)"
    if "would_move_count" in result:
        return f"would move {result['would_move_count']} files (dry run)"
    if "question" in result:
        return f"asked: {result['question']}"
    if "reason" in result:
        return f"rejected: {result['reason']}"
    return str(result)


def _format_history(history: list[dict]) -> str:
    lines = ["Completed steps:"]
    for i, entry in enumerate(history, 1):
        step = entry["step"]
        result = entry["result"]
        args_str = ", ".join(f'{k}="{v}"' for k, v in step.get("args", {}).items())
        output_ann = f"  [→ {step['output']}]" if step.get("output") else ""
        lines.append(f"  {i}. {step['tool']}({args_str}) → {_summarize_result(result)}{output_ann}")
    lines.append('Output the NEXT step, or {"plan":[{"tool":"done","args":{}}]} if complete.')
    return "\n".join(lines) + "\n\n"


def build_prompt(
    user_utterance: str,
    registry: dict[str, ToolDef],
    history: list[dict] | None = None,
    system_header: str | None = None,
    examples_block: str | None = None,
) -> tuple[str, str]:
    """Return *(system_message, user_message)* for chat completion."""
    user_utterance = normalize_path_separators(user_utterance)

    def _arg_label(name: str, schemas: dict, suffix: str) -> str:
        schema = schemas.get(name)
        hint = f", {schema.help}" if schema and schema.help else ""
        return f"{name} ({suffix}{hint})"

    tool_lines: list[str] = ["Available tools:"]
    for tool_name, tool_def in registry.items():
        if tool_name in _SYSTEM_TOOLS or tool_def.internal:
            continue
        schemas = tool_def.arg_schemas
        req = ", ".join(_arg_label(a, schemas, "required") for a in tool_def.required_args)
        opt = (
            ", " + ", ".join(_arg_label(a, schemas, "optional") for a in tool_def.optional_args)
            if tool_def.optional_args
            else ""
        )
        tool_lines.append(f"  - {tool_name}: {tool_def.description}")
        tool_lines.append(f"    args: {req}{opt}")

    header = system_header if system_header is not None else _SYSTEM_HEADER
    examples = examples_block if examples_block is not None else _EXAMPLES
    history_block = _format_history(history) if history else ""
    system_msg = header + "\n".join(tool_lines) + examples + history_block
    return system_msg, user_utterance
