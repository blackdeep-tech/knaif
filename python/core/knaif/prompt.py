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


def restore_grounded_spelling(value: str, raw_utterance: str) -> str:
    """Undo ``normalize_path_separators`` on a value the user typed, such as a password.

    The model only ever saw the normalized request, so a password typed ``p\\ss`` comes back as
    ``p/ss``. For a path that rewrite is the point; for a secret it silently changes what the
    user asked for. The rewrite is one character for one, so the user's own spelling sits at the
    same offsets in the original token. A value found verbatim in the request is left alone.
    """
    if "/" not in value or "\\" not in raw_utterance or value in raw_utterance:
        return value
    for token in raw_utterance.split(" "):
        normalized = normalize_path_separators(token)
        if normalized == token:
            continue
        at = normalized.find(value)
        if at >= 0:
            return token[at : at + len(value)]
    return value


# ── example selection helpers ─────────────────────────────────────────────────

_TERMINAL_TOOLS = frozenset({"clarify", "reject", "done"})
# Core tools retrieved only by their triggers (core_tools.yaml `triggers:`). An example that uses
# one must not reach a prompt that does not list the tool.
_TRIGGERED_TOOLS = frozenset({"alert_when_done"})


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


def _carries_url(text: str) -> bool:
    lower = text.lower()
    return "http://" in lower or "https://" in lower


def select_examples(
    structured_examples: list[dict],
    retrieved_tool_names: frozenset[str],
    query: str,
    max_tool_examples: int = 3,
    triggered: frozenset[str] = _TRIGGERED_TOOLS,
) -> list[dict]:
    """Select the most relevant examples for *query* given the retrieved tool set.

    Always includes one clarify example and one reject example (when available).
    Fills up to *max_tool_examples* domain examples ranked by:
      1. number of retrieved tools that appear in the example's plan (primary)
      2. Jaccard overlap between query tokens and example request tokens (tiebreaker)
    An example whose request carries an http(s) URL is eligible only when *query* carries one
    too, and then ranks first at equal tool overlap, so adding one changes no other prompt.
    An example whose plan uses a tool retrieved only by its triggers (*triggered*: the core
    ``alert_when_done`` and any skill tool with ``triggers:``) is eligible only when that tool
    was retrieved, and one whose request names a known folder as a
    destination ("save it in Downloads") only when *query* names one, for the same reason, and
    one marked ``when: many`` (the per-file batch examples) only when *query* asks for many
    files (:func:`knaif.nl_clarify_gate.asks_for_many_files`).
    Examples are returned in their original order from *structured_examples*.
    """
    from .known_folders import names_known_folder
    from .nl_clarify_gate import asks_for_many_files

    query_url = _carries_url(query)
    query_folder = names_known_folder(query)
    query_many = asks_for_many_files(query)
    clarify_exs = [e for e in structured_examples if _is_clarify_example(e)]
    reject_exs = [e for e in structured_examples if _is_reject_example(e)]
    domain_exs = [
        e
        for e in structured_examples
        if _example_tool_set(e)
        and (query_url or not _carries_url(e.get("request", "")))
        and (_example_tool_set(e) & triggered) <= retrieved_tool_names
        and (query_folder or not names_known_folder(e.get("request", "")))
        and (query_many or e.get("when") != "many")
    ]

    fixed: list[dict] = []
    if clarify_exs:
        fixed.append(clarify_exs[0])
    if reject_exs:
        fixed.append(reject_exs[0])

    query_tokens = set(query.lower().split())

    def _score(ex: dict) -> tuple[int, int, int]:
        tool_overlap = len(_example_tool_set(ex) & retrieved_tool_names)
        url_match = int(query_url and _carries_url(ex.get("request", "")))
        req_tokens = set(ex.get("request", "").lower().split())
        return (tool_overlap, url_match, len(query_tokens & req_tokens))

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


def apply_prompt_rules(header: str, rules: list[dict], present: set[str], utterance: str) -> str:
    """*header* with each of the skill's prompt rules that applies here joined at its end.

    A rule about tools joins when one of them is in this prompt (*present*); a ``when: many``
    rule when the request asks for many files (the clarify gate's test). Rust mirror:
    ``knaif_core::prompt::apply_prompt_rules``; contracts/parity/prompt_rule_cases.json.
    """
    from .nl_clarify_gate import asks_for_many_files

    many = asks_for_many_files(utterance)
    for rule in rules:
        applies = bool(
            (rule.get("when") == "many" and many) or present & set(rule.get("tools") or [])
        )
        # A rule with `words` also needs one of them in the request: the "last N
        # seconds" hint reaches a request about the end of a clip, not every trim prompt.
        if rule.get("words"):
            applies = bool(not rule.get("tools") or present & set(rule["tools"])) and _says_any(
                utterance, rule["words"]
            )
        if applies:
            header = header.rstrip("\n") + "\n" + rule["text"]
    return header


def _says_any(utterance: str, words: list[str]) -> bool:
    """Whether *utterance* holds one of *words*: a whole word, any case; a CJK word as a
    substring (Chinese is written without spaces)."""
    low = utterance.lower()
    latin = set(re.findall(r"[a-z0-9]+", low))
    unicode_words = set(re.findall(r"\w+", low))
    for word in (w.lower() for w in words):
        if re.match(r"[一-鿿]", word):
            if word in low:
                return True
        elif word in (latin if word.isascii() else unicode_words):
            return True
    return False


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
        # One list, so a tool with only optional args does not start with ", ".
        labels = [_arg_label(a, schemas, "required") for a in tool_def.required_args] + [
            _arg_label(a, schemas, "optional") for a in tool_def.optional_args
        ]
        tool_lines.append(f"  - {tool_name}: {tool_def.description}")
        tool_lines.append(f"    args: {', '.join(labels)}" if labels else "    args:")

    header = system_header if system_header is not None else _SYSTEM_HEADER
    examples = examples_block if examples_block is not None else _EXAMPLES
    # A rendered block starts with "Examples:"; without this it ran on from the last tool line.
    if examples and not examples.startswith("\n"):
        examples = "\n" + examples
    history_block = _format_history(history) if history else ""
    system_msg = header + "\n".join(tool_lines) + examples + history_block
    return system_msg, user_utterance
