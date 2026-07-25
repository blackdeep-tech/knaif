"""Tool registry: ToolDef dataclass, YAML loader, and tool retriever."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ALWAYS_INCLUDE = frozenset({"clarify", "reject", "done"})

# Maximal runs of non-space-delimited script (CJK ideographs + kana + Hangul).
# Whitespace tokenization can't split these, so a query like "将clip压缩" is one
# token that never equals the keyword "压缩". We segment such runs into character
# n-grams so keywords up to _CJK_MAX_NGRAM chars match by containment.
_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
_CJK_MAX_NGRAM = 4
# A keyword may be shared by tools, but one claimed by more than this many is too
# generic to discriminate and is rejected at load time as a curation mistake.
_MAX_KEYWORD_TOOLS = 4


@dataclass
class ArgSchema:
    """Typed metadata for a single tool argument.

    Used by validate_arg_by_schema in planner.py and by the knaif.cli SDK
    to build registries from Python type hints.
    """

    type: str = "string"  # string | integer | number | boolean | array | enum
    items: str | None = None  # element type hint for array args
    enum: tuple[str, ...] | None = None  # allowed values when type="enum"
    aliases: dict[str, str] | None = None  # synonym → canonical enum value (e.g. markdown→md)
    min: float | None = None  # inclusive lower bound (integer/number)
    max: float | None = None  # inclusive upper bound (integer/number)
    path_role: str | None = None  # input | output | sandbox (SDK path handling)
    help: str | None = None  # human-readable description


def _normalize(text: str) -> str:
    """Lowercase and strip combining diacritics (e.g. é→e) while preserving non-Latin scripts."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


def _query_tokens(text: str) -> set[str]:
    """Token set for keyword/description matching.

    Whitespace word tokens (ASCII + space-delimited scripts) **plus** character
    n-grams (length 1..``_CJK_MAX_NGRAM``) drawn from any CJK/kana/Hangul run, so a
    CJK keyword matches by containment. For text with no CJK run this returns
    exactly the old whitespace token set — pure-ASCII/Latin scoring is unchanged.
    """
    norm = _normalize(text)
    tokens = set(norm.replace("_", " ").split())
    for run in _CJK_RUN.findall(norm):
        for n in range(1, _CJK_MAX_NGRAM + 1):
            for i in range(len(run) - n + 1):
                tokens.add(run[i : i + n])
    return tokens


@dataclass
class ToolDef:
    name: str
    description: str
    required_args: tuple[str, ...]
    optional_args: tuple[str, ...] = field(default_factory=tuple)
    any_of_args: tuple[str, ...] = field(default_factory=tuple)
    # Args whose value must be grounded in (literally present in) the user's
    # utterance — e.g. a password. The model cannot invent these; if the value
    # is not found in the utterance, the NL clarify gate downgrades to clarify.
    grounded_args: tuple[str, ...] = field(default_factory=tuple)
    safety_category: str = "safe"
    keywords: tuple[str, ...] = field(default_factory=tuple)
    readonly: bool = False
    mock_args: dict[str, Any] = field(default_factory=dict)
    internal: bool = False
    defaults: dict[str, Any] = field(default_factory=dict)
    arg_schemas: dict[str, ArgSchema] = field(default_factory=dict)
    # Sibling-key aliases: model arg key → this tool's canonical key
    # (e.g. split_pdf {pages: ranges}). Applied in normalize_plan before validation.
    arg_aliases: dict[str, str] = field(default_factory=dict)


def load_registry(yaml_path: Path | str) -> dict[str, ToolDef]:
    """Load tool definitions from a YAML file and return a name→ToolDef mapping.

    Raises ValueError if any keyword is claimed by more than one tool.
    """
    path = Path(yaml_path)
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        raise ValueError(f"tools YAML must be a mapping, got {type(data).__name__}")

    registry: dict[str, ToolDef] = {}
    for name, cfg in data.items():
        if not isinstance(cfg, dict):
            continue  # skip top-level non-tool entries
        if "description" not in cfg:
            continue
        raw_schemas = cfg.get("arg_schemas") or {}
        arg_schemas: dict[str, ArgSchema] = {}
        for arg_name, sc in raw_schemas.items():
            if not isinstance(sc, dict):
                continue
            raw_enum = sc.get("enum")
            raw_aliases = sc.get("aliases")
            arg_schemas[arg_name] = ArgSchema(
                type=sc.get("type", "string"),
                items=sc.get("items"),
                enum=tuple(raw_enum) if raw_enum is not None else None,
                aliases=dict(raw_aliases) if isinstance(raw_aliases, dict) else None,
                min=sc.get("min"),
                max=sc.get("max"),
                path_role=sc.get("path_role"),
                help=sc.get("help"),
            )
        registry[name] = ToolDef(
            name=name,
            description=cfg["description"],
            required_args=tuple(cfg.get("required_args") or []),
            optional_args=tuple(cfg.get("optional_args") or []),
            any_of_args=tuple(cfg.get("any_of_args") or []),
            grounded_args=tuple(cfg.get("grounded_args") or []),
            safety_category=cfg.get("safety_category", "safe"),
            keywords=tuple(cfg.get("keywords") or []),
            readonly=bool(cfg.get("readonly", False)),
            mock_args=dict(cfg.get("mock_args") or {}),
            internal=bool(cfg.get("internal", False)),
            defaults=dict(cfg.get("defaults") or {}),
            arg_schemas=arg_schemas,
            arg_aliases=dict(cfg.get("arg_aliases") or {}),
        )

    # Keywords MAY be shared across tools — natural language isn't exclusive
    # ("reduce" / намали / 减小 fit both compress and volume). Retrieval down-weights
    # a shared keyword by its document frequency (see retrieve_tools), so sharing is
    # safe. Guard only against an *egregiously* generic keyword (a curation mistake):
    # one claimed by more than _MAX_KEYWORD_TOOLS tools flattens ranking.
    claims: dict[str, list[str]] = {}
    for name, tool_def in registry.items():
        for kw in tool_def.keywords:
            claims.setdefault(_normalize(kw), []).append(name)
    for kw, owners in claims.items():
        if len(owners) > _MAX_KEYWORD_TOOLS:
            raise ValueError(
                f"Keyword '{kw}' is claimed by {len(owners)} tools ({', '.join(owners)}); "
                f"at most {_MAX_KEYWORD_TOOLS} allowed — it is too generic to discriminate."
            )

    return registry


def retrieve_tools(
    query: str,
    registry: dict[str, ToolDef],
    top_k: int = 5,
    min_score: int = 0,
) -> dict[str, ToolDef]:
    """Return the top_k most relevant tools for *query* plus system tools.

    Scoring: keyword match = 3 points, description/name/arg word match = 1 point.
    Tools in _ALWAYS_INCLUDE are returned unconditionally.
    Internal tools are never surfaced to the model.
    Only tools with score >= min_score are included in the ranked selection.
    """
    tokens = _query_tokens(query)

    # Document frequency: how many tools claim each keyword. A shared keyword
    # contributes 3/df to each claimant, so it still surfaces every candidate but
    # doesn't dominate. Unique keywords (df=1) score exactly 3 — unchanged.
    df: dict[str, int] = {}
    for tool in registry.values():
        if tool.internal:
            continue
        for kw in tool.keywords:
            nk = _normalize(kw)
            df[nk] = df.get(nk, 0) + 1

    scores: list[tuple[float, str]] = []
    for name, tool in registry.items():
        if name in _ALWAYS_INCLUDE or tool.internal:
            continue
        matched = tokens & {_normalize(kw) for kw in tool.keywords}
        kw_score = 3.0 * sum(1.0 / df[k] for k in matched)
        text = (
            f"{name.replace('_', ' ')} {tool.description} "
            f"{' '.join(tool.required_args + tool.optional_args)}"
        )
        desc_score = len(tokens & set(_normalize(text).replace("_", " ").split()))
        scores.append((kw_score + desc_score, name))

    scores.sort(reverse=True)
    selected: dict[str, ToolDef] = {
        name: registry[name] for score, name in scores[:top_k] if score >= min_score
    }
    for name in _ALWAYS_INCLUDE:
        if name in registry:
            selected[name] = registry[name]

    return selected
