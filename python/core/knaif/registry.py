"""Tool registry: ToolDef dataclass, YAML loader, and tool retriever."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .effects import parse_effects

# A tuple, not a set: these are appended to the retrieval result in this order, and a set's
# iteration order depends on PYTHONHASHSEED — the same query returned a different tool order
# in different processes. Harmless inside Python (the prompt listing skips control tools) but
# it makes the order unportable: a runtime cannot be held to a ranking the reference does not
# reproduce. Native declares the same fixed order (`ALWAYS_INCLUDE` in knaif-core/retrieval.rs).
_ALWAYS_INCLUDE = ("clarify", "reject", "done")

# Maximal runs of non-space-delimited script (CJK ideographs + kana + Hangul).
# Whitespace tokenization can't split these, so a query like "将clip压缩" is one
# token that never equals the keyword "压缩". We segment such runs into character
# n-grams so keywords up to _CJK_MAX_NGRAM chars match by containment.
_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
_CJK_MAX_NGRAM = 4
# Leading and trailing non-word characters of a whitespace token.
_EDGE_PUNCT = re.compile(r"^\W+|\W+$")
# A request word matches a keyword it inflects: the keywords are mostly infinitives
# ("comprimir", "komprimieren") and a command is an imperative ("comprime", "komprimiere").
# Native: `keyword_matches` in knaif-core/retrieval.rs.
_STEM_MIN = 6  # letters two words must share to be one stem
_STEM_TAIL = 4  # letters the longer word may have after the shared stem
_SUFFIX_MIN_KEYWORD = 5  # a keyword this long matches a word that only adds letters to it
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


def _words(norm: str) -> set[str]:
    """The whitespace words of normalized text, each without edge punctuation."""
    return {w for w in (_EDGE_PUNCT.sub("", w) for w in norm.replace("_", " ").split()) if w}


def _query_tokens(text: str) -> set[str]:
    """Token set for keyword/description matching.

    Whitespace word tokens (ASCII + space-delimited scripts) **plus** character
    n-grams (length 1..``_CJK_MAX_NGRAM``) drawn from any CJK/kana/Hangul run, so a
    CJK keyword matches by containment.
    """
    norm = _normalize(text)
    words = norm.replace("_", " ").split()
    # Punctuation at a word's edge is not part of it: "1080p." and ".mkv" are "1080p" and
    # "mkv". A dot inside a word stays, so "clip.mp4" is still one token.
    tokens = _words(norm)
    # A hyphenated word also counts as its parts: "compresse-le", "по-малки". The whole word
    # stays, for keywords such as "re-encode".
    tokens |= {
        p for w in words if "-" in w for p in (_EDGE_PUNCT.sub("", x) for x in w.split("-")) if p
    }
    for run in _CJK_RUN.findall(norm):
        for n in range(1, _CJK_MAX_NGRAM + 1):
            for i in range(len(run) - n + 1):
                tokens.add(run[i : i + n])
    return tokens


def _keyword_matches(word: str, keyword: str) -> bool:
    """True when *word* (a normalized query token) is *keyword* or an inflection of it.

    Exact always. Otherwise only for alphabetic words outside CJK (which match by n-gram):
    - the word is the keyword plus up to `_STEM_TAIL` letters ("compresse" for "compress"),
      the keyword at least `_SUFFIX_MIN_KEYWORD` letters ("poster" is not "post");
    - or the two share a stem of at least `_STEM_MIN` letters and the longer one has at most
      `_STEM_TAIL` letters after it ("comprime" and "comprimir"). "extra" is not "extract".
    """
    if word == keyword:
        return True
    if not (word.isalpha() and keyword.isalpha()) or _CJK.search(word) or _CJK.search(keyword):
        return False
    if len(keyword) >= _SUFFIX_MIN_KEYWORD and word.startswith(keyword):
        return len(word) - len(keyword) <= _STEM_TAIL
    shared = 0
    for a, b in zip(word, keyword):  # noqa: B905 - a common prefix stops at the shorter one
        if a != b:
            break
        shared += 1
    return shared >= _STEM_MIN and max(len(word), len(keyword)) - shared <= _STEM_TAIL


def triggered(text: str, triggers: tuple[str, ...]) -> bool:
    """True when one of *triggers* is in *text* as a whole word or phrase, ignoring case and
    accents: "beep" matches "Beep when done" and not "beeper.mp4". A typographic apostrophe
    counts as a plain one, so "when it’s done" matches "when it's done"."""
    norm = " ".join(_normalize(text).replace("’", "'").split())
    for trigger in triggers:
        phrase = " ".join(_normalize(trigger).replace("’", "'").split())
        if not phrase:
            continue
        # A `#` stands for a number ("every # pages"). Chinese, Japanese and Korean write no
        # spaces between words, so a boundary never falls inside a sentence: a trigger in that
        # script matches anywhere (contracts/parity/trigger_cases.json).
        body = re.escape(phrase).replace(r"\#", r"\d+")
        if not _CJK.search(phrase):
            body = rf"(?<!\w){body}(?!\w)"
        if re.search(body, norm):
            return True
    return False


#: Chinese, Japanese and Korean scripts (kana, CJK ideographs and their extension A,
#: compatibility ideographs, Hangul syllables). Rust: `knaif_core::registry::is_cjk`.
_CJK = re.compile("[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")


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
    # Args only the request can state (e.g. a target size): a value the utterance does not hold
    # is left out with a note and the plan runs without it (`drop_unstated_args`), where a
    # grounded arg asks instead.
    stated_args: tuple[str, ...] = field(default_factory=tuple)
    safety_category: str = "safe"
    keywords: tuple[str, ...] = field(default_factory=tuple)
    # Whole words or phrases that bring this tool into a prompt. A tool with triggers is never
    # ranked: it is retrieved only when one is in the request (`retrieve_tools`), so a request
    # without one gets the same tools as if the tool did not exist (core's `alert_when_done`).
    triggers: tuple[str, ...] = field(default_factory=tuple)
    readonly: bool = False
    # Several inputs become ONE output (concat), not one output each (a batch). Read by chain
    # routing: a joining producer's output is a single routable intermediate.
    joins_inputs: bool = False
    # The kind of file the tool writes when a step names no output (tools.yaml `produces:`): a
    # kind from the skill's `file_kinds`, or {"arg": <name>} when the kind follows an argument
    # that names an extension. None: the same kind as its input. Read by chain routing's keep
    # rule (an intermediate every reader turns into another kind is a second result).
    produces: str | dict[str, str] | None = None
    # The tool's output is a NEW thing made from its input (a piece, a page range, a join), not
    # the input improved (tools.yaml `derives: true`). Read by the keep rule: an intermediate
    # every reader derives from stays, as a result the user asked for.
    derives: bool = False
    # The extension of the file the tool writes only when core repeats it over many files
    # (tools.yaml `batch_output:`, e.g. extract_text's "txt"): a single-file step prints, a
    # lifted one saves `<name>.<ext>` next to its file. Such a tool accepts `output`, which the
    # prompt does not list (`batch_chain.lift_batch`).
    batch_output: str | None = None
    # The result field `run --stdout` prints instead of saving a file (tools.yaml `stdout:`, e.g.
    # extract_text's "text"; owner, 2026-10-23). A plan with any other tool refuses
    # the flag.
    stdout: str | None = None
    # What the tool may do to the world (`knaif.effects`): None when tools.yaml declares nothing,
    # which a skill or toolkit bundle refuses; () for a tool with no effects.
    effects: tuple[str, ...] | None = None
    mock_args: dict[str, Any] = field(default_factory=dict)
    internal: bool = False
    defaults: dict[str, Any] = field(default_factory=dict)
    arg_schemas: dict[str, ArgSchema] = field(default_factory=dict)
    # Sibling-key aliases: model arg key → this tool's canonical key
    # (e.g. split_pdf {pages: ranges}). Applied in normalize_plan before validation.
    arg_aliases: dict[str, str] = field(default_factory=dict)


def _parse_produces(name: str, value: Any) -> str | dict[str, str] | None:
    """A tool's `produces:`: a kind name, or `{arg: <name>}`; anything else is a load error."""
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and set(value) == {"arg"} and isinstance(value["arg"], str):
        return {"arg": value["arg"]}
    raise ValueError(f"tool {name!r}: produces must be a kind or {{arg: <name>}}, got {value!r}")


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
            stated_args=tuple(cfg.get("stated_args") or []),
            safety_category=cfg.get("safety_category", "safe"),
            keywords=tuple(cfg.get("keywords") or []),
            triggers=tuple(cfg.get("triggers") or []),
            readonly=bool(cfg.get("readonly", False)),
            joins_inputs=bool(cfg.get("joins_inputs", False)),
            produces=_parse_produces(name, cfg.get("produces")),
            derives=bool(cfg.get("derives", False)),
            batch_output=str(cfg["batch_output"]).lstrip(".") if cfg.get("batch_output") else None,
            stdout=str(cfg["stdout"]) if cfg.get("stdout") else None,
            effects=parse_effects(name, cfg.get("effects")),
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


#: How many tools retrieval surfaces to the model by default. Named rather than inlined
#: because the eval path has to be able to record the value a run actually used, and a
#: default nobody can reference gets copied into three places and drifts.
DEFAULT_TOP_K = 5


def retrieve_tools(
    query: str,
    registry: dict[str, ToolDef],
    top_k: int = DEFAULT_TOP_K,
    min_score: int = 0,
) -> dict[str, ToolDef]:
    """Return the top_k most relevant tools for *query* plus system tools.

    Scoring: keyword match = 3 points, description/name/arg word match = 1 point.
    Tools in _ALWAYS_INCLUDE are returned unconditionally.
    Internal tools are never surfaced to the model.
    Only tools with score >= min_score are included in the ranked selection.
    A tool with ``triggers`` is not ranked: it follows the ranked tools when one of its triggers
    is in *query*, and is absent otherwise.
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
        if name in _ALWAYS_INCLUDE or tool.internal or tool.triggers:
            continue
        # Each word counts once per tool: its own keyword when it is one, else the inflected
        # keyword that weighs most (fewest tools claim it), ties on the keyword.
        tool_kw = {_normalize(kw) for kw in tool.keywords}
        matched: set[str] = set()
        for t in tokens:
            if t in tool_kw:
                matched.add(t)
                continue
            stems = [k for k in tool_kw if _keyword_matches(t, k)]
            if stems:
                matched.add(min(stems, key=lambda k: (df[k], k)))
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
    for name, tool in registry.items():
        if tool.triggers and not tool.internal and triggered(query, tool.triggers):
            selected[name] = tool
    for name in _ALWAYS_INCLUDE:
        if name in registry:
            selected[name] = registry[name]

    return selected
