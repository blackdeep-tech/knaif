"""NL clarify gate — utterance-vs-plan consistency check.

Downgrades a plan to a clarify step when the utterance does not concretely
specify a required file input.  Runs after the stem resolver (stems are already
substituted); never substitutes files itself.

Injection OFF (injected_files=None): clarify if no inline filename and no stem
in the utterance references the input.
Injection ON  (injected_files=set): also try to resolve descriptors against the
host-supplied file listing; allow only when exactly one match.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .input_refs import (
    GLOB_CHARS,
    MEDIA_EXTS,
    STOPWORDS,
    TYPE_KEYWORDS,
    classify_token,
    parse_inline_filenames,
    resolve_input,
)
from .planner import _PATH_ARG_KEYS
from .registry import ToolDef

# ── batch detection ───────────────────────────────────────────────────────────

_BATCH_SIGNALS = frozenset(
    {
        "all",
        "every",
        "each",
        "batch",
        "bulk",
        "folder",
        "directory",
        # translated forms covered in corpus keywords
        "alle",
        "todos",
        "toutes",
        "lote",
        "stapel",
        "lot",
    }
)

# ── helpers ───────────────────────────────────────────────────────────────────

_ARTICLE_RE = re.compile(r"^\s*(the|a|an)\s+", re.IGNORECASE)
_STEM_STRUCTURE_RE = re.compile(r"[_\-]|\d")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _has_qualifier_words(token: str) -> bool:
    """True if the descriptor contains words beyond type keywords and stopwords.

    A pure type descriptor ("the audio file") has no qualifiers and a
    structural-only match is acceptable.  A qualified descriptor ("the 4K
    video") has "4k" as a qualifier; a structural-only match means that
    qualifier didn't hit any filename — not a confident match.
    """
    words = _WORD_RE.findall(token.lower())
    return any(w not in STOPWORDS and w not in TYPE_KEYWORDS and w not in MEDIA_EXTS for w in words)


def _is_batch_utterance(utterance: str) -> bool:
    words = set(re.findall(r"[a-z]+", utterance.lower()))
    return bool(words & _BATCH_SIGNALS)


_OUTPUT_ARG_KEYS = frozenset({"output", "outputs"})


def _input_tokens(step: dict[str, Any]) -> list[str]:
    args = step.get("args") or {}
    tokens: list[str] = []
    for key in _PATH_ARG_KEYS:
        if key not in args:
            continue
        val = args[key]
        if isinstance(val, str):
            tokens.append(val)
        elif isinstance(val, list):
            tokens.extend(str(v) for v in val if isinstance(v, str))
    return tokens


def _output_tokens(step: dict[str, Any]) -> set[str]:
    args = step.get("args") or {}
    tokens: set[str] = set()
    for key in _OUTPUT_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str):
            tokens.add(val)
        elif isinstance(val, list):
            tokens.update(str(v) for v in val if isinstance(v, str))
    return tokens


def _has_matching_stem_in_utterance(token: str, utterance: str) -> bool:
    """True if the utterance contains a stem token matching the token's filename stem.

    Covers the case where the stem resolver already substituted the extension
    (e.g. "clip_4k" in utterance → "clip_4k.mp4" in plan).
    """
    try:
        file_stem = Path(token).stem.lower()
    except Exception:  # noqa: BLE001
        return False
    for word in re.findall(r"\b[a-zA-Z0-9][a-zA-Z0-9_\-]*\b", utterance):
        if _STEM_STRUCTURE_RE.search(word) and word.lower() == file_stem:
            return True
    return False


def _is_concretely_specified(
    token: str,
    utterance: str,
    inline_files: set[str],
    injected_files: set[str] | None,
) -> bool:
    available = inline_files | (injected_files or set())
    cls = classify_token(token, available)

    if cls in ("glob", "chain"):
        return True
    if token in inline_files:
        return True  # user wrote it out in the utterance
    if _has_matching_stem_in_utterance(token, utterance):
        return True  # stem form appears in utterance (resolver substituted extension)
    if injected_files is not None and token in injected_files:
        return True  # injection ON: model was given this filename in the listing
    if cls == "descriptor" and injected_files is not None:
        res = resolve_input(token, injected_files)
        if res.label != "resolves_unique":
            return False
        # A structural-only match means the type cue matched but any qualifier
        # words in the descriptor ("4K", "promo", etc.) did NOT match the
        # filename.  Treat that as under-specified.
        if res.mode == "structural" and _has_qualifier_words(token):
            return False
        return True
    return False


def _is_value_grounded(value: Any, utterance: str) -> bool:
    """True if a grounded-arg value literally appears in the utterance.

    Used for secret/unguessable args (e.g. passwords): the model must not
    invent them. A real value the user typed appears in the utterance; a
    hallucinated placeholder ("your_password") does not.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    return value.strip().lower() in utterance.lower()


def _grounded_clarify_question(arg: str) -> str:
    return f"What {arg} should I use?"


def _clarify_question(token: str, tool: str) -> str:
    body = _ARTICLE_RE.sub("", token).strip()
    if body:
        return f"Which {body} did you mean?"
    verb = tool.replace("_", " ")
    return f"Which file would you like to {verb}?"


_TERMINAL_TOOLS = frozenset({"clarify", "reject", "done", "wait_for_confirmation"})


def required_args_clarify(
    intent_plan: list[dict[str, Any]],
    registry: dict[str, ToolDef] | None,
) -> list[dict[str, Any]] | None:
    """Clarify (instead of hard-erroring) when a step omits an arg the user must
    supply — a missing required arg, or a tool with ``any_of_args`` where none is
    present. Runs before structural validation. Returns a clarify step list, or
    None to proceed.

    Only fires on *absent* args, so it never changes a plan whose required args
    are all present (e.g. every well-formed qwen plan) — it converts the
    "model omitted a required arg" validation error into a clarification.
    """
    if registry is None:
        return None
    for step in intent_plan:
        tool = step.get("tool", "")
        if tool in _TERMINAL_TOOLS:
            continue
        tool_def = registry.get(tool)
        if tool_def is None:  # unknown tool → leave for validation to reject
            continue
        args = step.get("args") or {}
        for arg in tool_def.required_args:
            val = args.get(arg)
            if arg not in args or val is None or (isinstance(val, str) and not val.strip()):
                return [{"tool": "clarify", "args": {"question": _grounded_clarify_question(arg)}}]
        if tool_def.any_of_args and not any(a in args for a in tool_def.any_of_args):
            opts = " or ".join(tool_def.any_of_args)
            return [{"tool": "clarify", "args": {"question": f"What {opts} should I use?"}}]
    return None


# ── public API ────────────────────────────────────────────────────────────────


def nl_clarify_gate(
    utterance: str,
    intent_plan: list[dict[str, Any]],
    *,
    injected_files: set[str] | None = None,
    registry: dict[str, ToolDef] | None = None,
) -> list[dict[str, Any]]:
    """Return *intent_plan* unchanged, or a single clarify step.

    Clarifies when the utterance under-specifies a required file input and
    injection (if ON) cannot resolve it unambiguously, **or** when a tool's
    declared ``grounded_args`` (e.g. a password) hold a value that does not
    appear in the utterance — i.e. the model invented it.

    Parameters
    ----------
    utterance:
        The original user utterance, before prompt construction.
    intent_plan:
        The model's proposed plan steps (post stem-resolution).
    injected_files:
        None → injection OFF (no host-supplied listing).
        set  → injection ON (filenames the host made available, e.g. dropped
               files in the UI).
    registry:
        Tool registry. When supplied, enforces each tool's ``grounded_args``;
        when None, grounding is not checked (backward-compatible).
    """
    # Grounded-arg check runs regardless of batch phrasing: a batch utterance
    # ("protect all PDFs") still must not carry an invented password.
    if registry is not None:
        for step in intent_plan:
            tool_def = registry.get(step.get("tool", ""))
            if tool_def is None or not tool_def.grounded_args:
                continue
            args = step.get("args") or {}
            for arg in tool_def.grounded_args:
                if arg not in args:
                    continue
                if not _is_value_grounded(args[arg], utterance):
                    question = _grounded_clarify_question(arg)
                    return [{"tool": "clarify", "args": {"question": question}}]

    if _is_batch_utterance(utterance):
        return intent_plan

    inline_files = parse_inline_filenames(utterance)
    plan_outputs: set[str] = set()

    for step in intent_plan:
        tool = step.get("tool", "")
        tokens = _input_tokens(step)
        for token in tokens:
            if any(c in token for c in GLOB_CHARS):
                continue  # glob token: exempt individually
            if "/" in token or "\\" in token:
                continue  # directory/absolute path — not a media file reference
            if token in plan_outputs:
                continue  # intermediate output produced by a previous step
            if not _is_concretely_specified(token, utterance, inline_files, injected_files):
                question = _clarify_question(token, tool)
                return [{"tool": "clarify", "args": {"question": question}}]
        plan_outputs |= _output_tokens(step)

    return intent_plan
