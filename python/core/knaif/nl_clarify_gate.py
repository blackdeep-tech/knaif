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
from collections.abc import Collection, Mapping
from pathlib import Path, PurePosixPath
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
from .planner import _PATH_ARG_KEYS, _is_stem_candidate
from .prompt import restore_grounded_spelling
from .registry import ToolDef

#: A plain stem: one ASCII word. Native `PLAIN_STEM`.
_PLAIN_STEM_RE = re.compile(r"^[a-z]+$", re.IGNORECASE)

#: Words for a kind of file: "the video", "the clip" mean any one, so they never name a file
#: (contracts/parity/clarify_gate_cases.json). Native `GENERIC_FILE_WORDS`.
GENERIC_FILE_WORDS = frozenset(
    {
        "video", "clip", "file", "movie", "film", "recording", "song", "track", "audio", "sound",
        "music", "voice", "image", "photo", "picture", "pic", "thumbnail", "frame", "gif",
        "document", "doc", "pdf", "page", "scan", "text", "report", "folder", "output", "input",
        "result", "copy", "mp4", "mov", "mkv", "avi", "webm", "mp3", "wav", "jpg", "png",
    }
)  # fmt: skip


def _named_by_stem(value: str, u_lower: str, known: Collection[str]) -> bool:
    """Did the user name this file by its stem, and is the stem's file really there?

    Both halves are required — see :meth:`CommandAgent._hallucinated_filename` for why
    either alone admits a hallucination. ``_is_stem_candidate`` is reused rather than
    re-derived so "what counts as a stem" has one definition in the codebase: the
    resolver and the guard must agree, or the guard passes a name the resolver then
    refuses. Both gates use it: the guard and :func:`nl_clarify_gate`.
    """
    name = PurePosixPath(value.replace("\\", "/")).name
    stem = name.rsplit(".", 1)[0]
    if not stem or name.lower() not in known:
        return False
    if _is_stem_candidate(stem):
        return stem.lower() in u_lower
    # A plain word names the file too when it is the whole stem, said as a word, and not a word
    # for a kind of file (owner, 2026-10-24: "convert beach and city from mov to mkv").
    low = stem.lower()
    return (
        bool(_PLAIN_STEM_RE.match(stem))
        and low not in GENERIC_FILE_WORDS
        and re.search(rf"(?<![a-z0-9_]){re.escape(low)}(?![a-z0-9_])", u_lower) is not None
    )


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
        # The corpora's languages (de, es, fr), each and folder
        "jede",
        "jeder",
        "jedes",
        "jeden",
        "jedem",
        "ordner",
        "verzeichnis",
        "cada",
        "carpeta",
        "chaque",
        "dossier",
        "tous",
    }
)

#: Batch words outside ASCII, matched as whole Unicode words (bg: all, each, in a batch, folder).
#: Kept apart from `_BATCH_SIGNALS` so English matching stays the `[a-z]+` runs it always was.
_BATCH_SIGNALS_UNICODE = frozenset(
    {
        "всички",
        "всичките",
        "всеки",
        "всяко",
        "всяка",
        "пакетно",
        "папка",
        "папката",
        "директория",
        "директорията",
    }
)

#: Chinese batch words, matched as substrings (no spaces between words): all, each, in a batch,
#: folder, the whole lot.
_BATCH_SIGNALS_CJK = ("所有", "每个", "每個", "批量", "文件夹", "資料夾", "全部")

#: Plural file words: a request for many files without a batch word ("convert the .mov videos
#: here"). They let a glob through, and nothing else: an invented name still asks.
_PLURAL_FILE_WORDS = frozenset(
    {
        "videos",
        "clips",
        "files",
        "movies",
        "recordings",
        "songs",
        "tracks",
        "images",
        "photos",
        "pictures",
        "documents",
        "pdfs",
        "mp4s",
        "movs",
        "mkvs",
        "mp3s",
        "wavs",
        "gifs",
        "jpgs",
        "pngs",
        "dateien",
        "bilder",
        "dokumente",
        "archivos",
        "fichiers",
    }
)
_PLURAL_FILE_WORDS_UNICODE = frozenset(
    {
        "vídeos",
        "vidéos",
        "видеа",
        "видеата",
        "файлове",
        "файловете",
        "клипове",
        "клиповете",
        "снимки",
        "снимките",
        "документи",
        "документите",
    }
)
_PLURAL_FILE_WORDS_CJK = ("这些", "這些")

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
    low = utterance.lower()
    return (
        bool(set(re.findall(r"[a-z]+", low)) & _BATCH_SIGNALS)
        or bool(set(re.findall(r"\w+", low)) & _BATCH_SIGNALS_UNICODE)
        or any(w in low for w in _BATCH_SIGNALS_CJK)
    )


def _asks_for_many(utterance: str, glob: str) -> bool:
    """Whether a request without a batch word still asks for many files: a plural file word,
    or the glob written in the request itself (``convert *.mov to mp4``)."""
    low = utterance.lower()
    return (
        glob.lower() in low
        or bool(set(re.findall(r"[a-z0-9]+", low)) & _PLURAL_FILE_WORDS)
        or bool(set(re.findall(r"\w+", low)) & _PLURAL_FILE_WORDS_UNICODE)
        or any(w in low for w in _PLURAL_FILE_WORDS_CJK)
    )


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# No `\b` around it: a CJK character is a word character, so "把report.pdf拆分" has none.
_NAMED_FILE_RE = re.compile(r"[\w-]+\.([a-z0-9]{2,5})(?:[^a-z0-9]|$)", re.IGNORECASE)
_LETTER_RE = re.compile(r"[a-z]", re.IGNORECASE)


#: A name the request gives an output ("call it preview.gif", "save them as out.mp4", "als",
#: "comme", "като", "保存为"): not a file the request is about (2026-10-23).
_OUTPUT_NAME_RE = re.compile(
    r"(?:\b(?:call(?:ed)?\s+(?:it|them)|name[ds]?\s+(?:it|them)|named|save(?:d)?\s+(?:it\s+|them\s+)?as"
    r"|as|into|als|nach|namens|como|llamad[oa]s?|comme|vers|appel[ée]e?s?|като)"
    r"|(?:命名为|保存为|另存为|存为))\s*[\w-]+\.[a-z0-9]{2,5}\b",
    re.IGNORECASE,
)


def _without_output_names(text: str) -> str:
    return _OUTPUT_NAME_RE.sub(" ", text)


def _names_a_file(text: str) -> bool:
    """A name with an extension holding a letter (``clip.mp4``, ``x.3gp``); a codec or a number
    (``H.264``, ``1.25``) names no file."""
    return any(_LETTER_RE.search(m.group(1)) for m in _NAMED_FILE_RE.finditer(text))


def asks_for_many_files(utterance: str) -> bool:
    """Whether *utterance* asks for many files: a batch word, a plural file word, or a glob
    written in it. The clarify gate's own test, shared with the prompt: the per-file batch rule
    and its examples join only such a prompt (owner, 2026-10-20). What names a place
    is left out first: a URL (its host can read ``files.``), and, when the request names a file,
    a known folder after in/into/to/on/onto/from, which is then where that file goes ("put
    clip.mov in my Downloads folder"); with no file named the folder may be the batch
    ("everything in my Videos folder"). A request that names a file is about that file, so a
    batch word alone ("every page of sample.pdf", "at all") is not enough there; a glob or a
    plural file word still is. Rust mirror: ``knaif_core::nl_clarify_gate::asks_for_many_files``;
    contracts/parity/many_files_cases.json."""
    from .known_folders import without_folder_places

    text = _URL_RE.sub(" ", utterance)
    if _names_a_file(_without_output_names(utterance)):  # a URL names a file too
        return _asks_for_many(without_folder_places(text), "*")
    return _is_batch_utterance(text) or _asks_for_many(text, "*")


#: Number words of the corpora's languages, for :func:`count_question`. "both" and
#: its translations are two. German "ein" is left out: it is also the article.
_COUNT_WORDS: dict[str, int] = {
    **dict(
        zip("one two three four five six seven eight nine ten".split(), range(1, 11), strict=True)
    ),
    "both": 2,
    **dict(
        zip("zwei drei vier fünf sechs sieben acht neun zehn".split(), range(2, 11), strict=True)
    ),
    "beide": 2,
    "beiden": 2,
    **dict(
        zip("dos tres cuatro cinco seis siete ocho nueve diez".split(), range(2, 11), strict=True)
    ),
    "ambos": 2,
    "ambas": 2,
    **dict(zip("deux trois quatre cinq six sept huit neuf dix".split(), range(2, 11), strict=True)),
    **dict(zip("три четири пет шест седем осем девет десет".split(), range(3, 11), strict=True)),
    "два": 2,
    "две": 2,
    "двата": 2,
    "двете": 2,
    "трите": 3,
}
#: A count in Chinese sits right before the file word, without spaces ("两个视频"); a digit that
#: follows a letter is part of a name ("mp4文件"), not a count.
_CJK_COUNT_RE = re.compile(
    r"([两兩二三四五六七八九十]|(?<![a-z0-9])\d+)\s*[个個部支份张張]?\s*"
    r"(?:视频|視頻|影片|文件|檔案|档案|图片|圖片|片段|剪辑|剪輯)"
)
_CJK_NUMBERS = dict(zip("两兩二三四五六七八九十", (2, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10), strict=True))
#: How the count question names the files, by their kind.
_KIND_NOUNS = {
    "video": "videos",
    "audio": "audio files",
    "document": "documents",
    "image": "images",
    "text": "text files",
}


def _requested_count(utterance: str) -> int | None:
    """The number of files *utterance* asks for: a digit or a number word at most two words
    before a plural file word ("2 of the videos", "both mov files", "2 от клиповете"), or a
    Chinese count before a file word ("两个视频"). None when there is none."""
    low = _URL_RE.sub(" ", utterance.lower())
    words = re.findall(r"\w+", low)
    file_words = _PLURAL_FILE_WORDS | _PLURAL_FILE_WORDS_UNICODE
    for i, word in enumerate(words):
        n = int(word) if word.isascii() and word.isdigit() else _COUNT_WORDS.get(word)
        if n is None:
            continue
        for later in words[i + 1 : i + 4]:
            if later in file_words:
                return n
            if later.isdigit() or later in _COUNT_WORDS:
                break
    m = _CJK_COUNT_RE.search(low)
    if m:
        raw = m.group(1)
        return int(raw) if raw.isdigit() else _CJK_NUMBERS[raw]
    return None


def count_question(
    utterance: str,
    plan: list[dict[str, Any]],
    base: Path,
    file_kinds: Mapping[str, str] | None,
) -> str | None:
    """The question to ask when *utterance* asks for a number of files the plan does not touch.

    "конвертирай 2 от видеата в mp4" converted every video (owner, 2026-10-22). The
    files touched are the plan's folder and glob inputs, listed as the per-file batch lists them
    (:func:`knaif.batch_chain.expand_inputs`), plus any file named beside them; an earlier step's
    output is not counted again. Only a plan with a folder or a glob is checked: files named one
    by one are the request's own choice. None when there is nothing to ask. Rust mirror:
    ``knaif_core::nl_clarify_gate::count_question``; contracts/parity/count_cases.json.
    """
    from .batch_chain import INPUT_KEYS, expand_inputs

    wanted = _requested_count(utterance)
    if wanted is None:
        return None
    touched: dict[str, str] = {}
    outputs: set[str] = set()
    listed = False
    for step in plan:
        args = step.get("args") or {}
        for key in INPUT_KEYS:
            value = args.get(key)
            entries = [value] if isinstance(value, str) else value
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, str) or entry.startswith("$") or "://" in entry:
                    continue
                if entry.lower() in outputs:
                    continue
                found = expand_inputs([entry], base, file_kinds)
                if entry in found:
                    listed = True
                    touched.update((f.lower(), f) for f in found[entry])
                else:
                    touched[entry.lower()] = entry
        output = args.get("output")
        if isinstance(output, str):
            outputs.add(output.lower())
    have = len(touched)
    if not listed or have == wanted or have == 0:
        return None
    kinds = {(file_kinds or {}).get(Path(f).suffix.lstrip(".").lower()) for f in touched.values()}
    kind = kinds.pop() if len(kinds) == 1 else None
    noun = _KIND_NOUNS.get(kind, "files") if kind else "files"
    if have > wanted:
        return f"Which {wanted} of the {have} {noun}? Name them."
    return f"I found {have} {noun} here, not {wanted}. Which ones do you mean?"


def _glob_question(glob: str) -> str:
    """``Did you mean all the .mp3 files here? …``: a glob the request never asked for."""
    ext = Path(glob.replace("\\", "/")).suffix
    what = f"{ext.lower()} files" if ext and not any(c in ext for c in GLOB_CHARS) else "files"
    return f'Did you mean all the {what} here? Name the file, or say "all" to use every one.'


_OUTPUT_ARG_KEYS = frozenset({"output", "outputs"})

#: `_PATH_ARG_KEYS` in a fixed order. The planner's set is a frozenset, whose iteration order
#: changes from one process to the next, so with two unnamed inputs under different keys the
#: question named either one at random. The native port walks this same order
#: (contracts/parity/nl_clarify_gate_cases.json).
_INPUT_ARG_ORDER = ("inputs", "input", "files", "src", "dst", "path", "base", "append")
assert set(_INPUT_ARG_ORDER) == _PATH_ARG_KEYS


def _input_tokens(step: dict[str, Any]) -> list[str]:
    args = step.get("args") or {}
    tokens: list[str] = []
    for key in _INPUT_ARG_ORDER:
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
    known_files: Collection[str] = frozenset(),
) -> bool:
    available = inline_files | (injected_files or set())
    cls = classify_token(token, available)

    if cls in ("glob", "chain"):
        return True
    if token in inline_files:
        return True  # user wrote it out in the utterance
    if _has_matching_stem_in_utterance(token, utterance):
        return True  # stem form appears in utterance (resolver substituted extension)
    if _named_by_stem(token, utterance.lower(), known_files):
        return True  # a plain word for a file that is there, as the guard reads it
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


def restore_grounded_args(
    intent_plan: list[dict[str, Any]],
    raw_utterance: str,
    registry: dict[str, ToolDef] | None,
) -> list[dict[str, Any]]:
    """Give each ``grounded_args`` value back the spelling the user typed.

    Runs after the grounded check, which compares against the normalized request the model saw;
    see ``restore_grounded_spelling``. Only grounded args change: paths keep their forward
    slashes. Returns a new plan; the model's plan is not mutated.
    """
    if registry is None or "\\" not in raw_utterance:
        return intent_plan
    out: list[dict[str, Any]] = []
    for step in intent_plan:
        tool_def = registry.get(step.get("tool", ""))
        args = step.get("args") or {}
        if tool_def is None or not tool_def.grounded_args:
            out.append(step)
            continue
        restored = {
            k: (
                restore_grounded_spelling(v, raw_utterance)
                if k in tool_def.grounded_args and isinstance(v, str)
                else v
            )
            for k, v in args.items()
        }
        out.append({**step, "args": restored})
    return out


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


def unsupported_args_clarify(
    intent_plan: list[dict[str, Any]],
    registry: dict[str, ToolDef] | None,
) -> list[dict[str, Any]] | None:
    """Clarify (instead of hard-erroring) when a step puts an arg on a *known*
    tool that the tool does not declare. Runs before structural validation.
    Returns a clarify step list, or None to proceed.

    This is the inventory-gap case of the reject/clarify taxonomy
    (docs/plans/2026-09-11-reject-clarify-taxonomy.md): the user asked for
    something real that no tool can express ("...and lower the sample rate to
    22050 Hz"), and the model wrote it down as an arg because that was the
    closest it could get. Refusing with a validator string tells the user
    nothing; saying it is not supported does.

    Deliberately narrow, so it stays a taxonomy fix and not a way for bugs to
    hide behind a polite question:

    * **Known tools only.** An unknown tool is still a hard validation error.
    * **After normalization.** ``normalize_plan``'s alias and input/inputs passes
      run first, so a merely-misnamed key is renamed rather than clarified.
    * **NL path only** (the caller gates on having an utterance), and before
      expansion — an *expanded* plan carrying an undeclared arg is a skill bug
      and must keep failing loudly.
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
        args = step.get("args")
        if not isinstance(args, dict):
            continue
        allowed = set(tool_def.required_args) | set(tool_def.optional_args)
        extra = [k for k in args if k not in allowed]
        if extra:
            verb = tool.replace("_", " ")
            return [
                {
                    "tool": "clarify",
                    "args": {
                        "question": (
                            f"I can't {verb} that way — that isn't supported. "
                            "Could you rephrase?"
                        )
                    },
                }
            ]
    return None


# ── public API ────────────────────────────────────────────────────────────────


def nl_clarify_gate(
    utterance: str,
    intent_plan: list[dict[str, Any]],
    *,
    injected_files: set[str] | None = None,
    registry: dict[str, ToolDef] | None = None,
    known_files: Collection[str] = frozenset(),
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
    known_files:
        Lowercased names of the files in the working folder. A plain word names one of them
        when it is its whole stem (the hallucinated-filename guard's rule); empty keeps
        the strict rule.
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
                # A glob is many files: fine when the request asks for many, else ask. Without
                # this, "MP3 nach WAV konvertieren" ran over every mp3 here.
                if _asks_for_many(utterance, token):
                    continue
                return [{"tool": "clarify", "args": {"question": _glob_question(token)}}]
            if "/" in token or "\\" in token:
                continue  # directory/absolute path — not a media file reference
            if token in plan_outputs:
                continue  # intermediate output produced by a previous step
            if not _is_concretely_specified(
                token, utterance, inline_files, injected_files, known_files
            ):
                question = _clarify_question(token, tool)
                return [{"tool": "clarify", "args": {"question": question}}]
        plan_outputs |= _output_tokens(step)

    return intent_plan
