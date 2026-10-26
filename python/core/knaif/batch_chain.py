"""Run a chain once per file.

The model writes the steps for one file, with the batch glob as the first step's input
("convert all the .mov files here to mp4 and extract the audio" →
``convert_video {inputs: ["*.mov"], output: "clip.mp4"} → extract_audio {inputs: ["clip.mp4"]}``),
and core repeats them for every file the glob matched (owner, 2026-10-14).

* **The head** is the first step, other than a join, with an input the caller expanded to files.
* **The chain** is every later step that reads a chain step's output: by its name (the per-file
  form, ``clip.mp4``) or by its pattern (``rotated/*.mkv``). Core accepts both links. A step
  nothing links to keeps its place, untouched.
* **A join** (``concat_video``, ``merge_pdfs``) reading a chain output reads every file's, in
  input order (fan-in), once, at its own place in the plan.
* **Names** (owner, 2026-10-15): the input's name with the extension the step writes, next to
  the input (``beach.mov`` → ``beach.mp4``); the model's label (``clip``) never reaches a name. A
  pattern's ``*`` is the input's name, in the pattern's folder when it has one. On a clash with a
  batch input or an earlier output of the lift, the label is added (``dog_trimmed.mp4``), then a
  counter. Names are chosen file by file whatever the order, so both orders write the same files.
* **Order**: each file goes through the whole chain before the next. It won the run-order A/B
  against step by step (2026-10-20): the same files and failures, the first file finished
  25-72% sooner on 4 of 5 batches.

A plan with no head comes back unchanged, and so does one whose head nothing reads when the head
takes its files in ``inputs`` (a list): such a single batch step is the skill's own business
(``run_batch``). A lone head on ``input`` (one path) is repeated per file, since its tool cannot
expand a glob ("add page numbers to every pdf here"). Pure: no disk, no registry; the caller expands the globs.
Rust mirror: ``knaif_core::batch_chain``; both read contracts/parity/batch_chain_cases.json.
"""

from __future__ import annotations

import copy
import fnmatch
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The args a step reads its files from: ``input`` (one path) or ``inputs`` (a list).
INPUT_KEYS = ("input", "inputs")

_DIGITS = re.compile(r"[0-9]+")


def natural_key(name: str) -> tuple[str, str]:
    """Sort key that counts numbers as numbers: ``clip1``, ``clip2``, ``clip10`` (owner,
    2026-10-19). Any case; ties (``clip2`` / ``clip02`` / ``Clip2``) fall back to the name as
    written. Rust mirror: ``knaif_core::batch_chain::natural_key``."""
    padded = _DIGITS.sub(lambda m: (m.group().lstrip("0") or "0").zfill(20), name)
    return padded.lower(), name


def list_files(
    folder: Path, pattern: str = "*", *, recursive: bool = False
) -> list[tuple[str, Path]]:
    """The files in *folder* matching *pattern*: ``(path below folder, path)``, natural order.

    The one folder listing (owner, 2026-10-23): core's per-file batch and the fs
    toolkit's ``resolve_inputs`` both list through it. Files only, ordered by their path below
    *folder* (:func:`natural_key`); into subfolders only when *recursive*, the pattern matched
    against the file name. A missing folder lists nothing. Rust mirror:
    ``knaif_core::batch_chain::list_files``; contracts/parity/list_files_cases.json.
    """
    if not folder.is_dir():
        return []
    found = folder.rglob(pattern) if recursive else folder.glob(pattern)
    files = [(c.relative_to(folder).as_posix(), c) for c in found if c.is_file()]
    return sorted(files, key=lambda item: natural_key(item[0]))


def expand_inputs(
    entries: Sequence[str], base: Path, file_kinds: Mapping[str, str] | None = None
) -> dict[str, list[str]]:
    """The files each glob or folder in *entries* names, for :func:`lift_per_file_chain`.

    Relative entries resolve against *base*; each file keeps the model's spelling of its folder
    (``videos/*.mov`` → ``videos/clip1.mov``). Files only, not recursive, in natural order. When
    the skill declares *file_kinds*, only files of a declared kind count, so a bare ``*`` or a
    folder never pulls ``notes.txt`` into a video chain. A literal file is not in the result; a
    glob that matches nothing maps to ``[]``.
    """
    found: dict[str, list[str]] = {}
    for entry in entries:
        path = Path(entry) if Path(entry).is_absolute() else base / entry
        if path.is_dir():
            folder, pattern = entry.rstrip("/\\"), "*"
        elif any(c in entry for c in "*?["):
            folder, pattern = posixpath.split(entry.replace("\\", "/"))
            path = path.parent
        else:
            continue
        names = [rel for rel, _path in list_files(path, pattern)]
        if file_kinds:
            names = [n for n in names if posixpath.splitext(n)[1].lstrip(".").lower() in file_kinds]
        found[entry] = [posixpath.join(folder, n) if folder else n for n in names]
    return found


#: The args a plan's paths sit in, for :func:`drop_invented_folders`.
_PATH_KEYS = ("input", "inputs", "output", "base", "append")


#: A resolution or size ("to 720p", "1920x1080", "4k"): never a folder name, unless slashed.
_SIZE_RE = re.compile(r"^\d+(?:p|k|x\d+)$")


def _names_folder(utterance: str, folder: str) -> bool:
    """Whether *utterance* names *folder*: with a slash, after a place word, or next to the
    word folder; a nested folder only by its whole path, and a resolution never but slashed
    (contracts/parity/invented_folder_cases.json)."""
    low = utterance.lower().replace("\\", "/")
    name = folder.lower()
    if f"{name}/" in low:
        return True
    if _SIZE_RE.match(name):
        return False
    n = re.escape(name)
    return bool(
        re.search(rf"\b(?:in|into|to|from|on|onto|inside)\s+(?:my\s+|the\s+)?{n}\b", low)
        or re.search(rf"\b{n}\s+folder\b|\bfolder\s+{n}\b", low)
    )


def _here_if_invented(value: Any, utterance: str, base: Path) -> Any:
    if not isinstance(value, str) or not any(c in value for c in "*?["):
        return value
    if value.startswith(("$", "~", "/", "\\")) or "://" in value or re.match(r"^[a-zA-Z]:", value):
        return value
    folder, name = posixpath.split(value.replace("\\", "/"))
    while folder.startswith("./"):
        folder = folder[2:]
    if folder in ("", ".") or any(c in folder for c in "*?["):
        return value
    if (base / folder).is_dir() or _names_folder(utterance, folder):
        return value
    return name


def drop_invented_folders(
    plan: Sequence[Mapping[str, Any]], utterance: str, base: Path
) -> list[dict[str, Any]]:
    """*plan* with every glob in a folder the model made up read as here (owner, 2026-10-21).

    A path with a glob in it whose folder does not exist under *base*, in a request that does not
    name that folder, becomes its last part: `videos/*.mov` for "the .mov videos here" is
    `*.mov`. Runs before known-folder resolution, which would otherwise point `videos/` at the
    user's Videos folder. Paths without a glob, absolute, `~`, URL and `$var` paths stay. *plan*
    itself is left as it was. Rust mirror: ``knaif_core::batch_chain::drop_invented_folders``.
    """
    out = [copy.deepcopy(dict(s)) for s in plan]
    for step in out:
        args = step.get("args")
        if not isinstance(args, dict):
            continue
        for key in _PATH_KEYS:
            value = args.get(key)
            if isinstance(value, list):
                args[key] = [_here_if_invented(v, utterance, base) for v in value]
            elif value is not None:
                args[key] = _here_if_invented(value, utterance, base)
    return out


#: A batch glob ``widen_kind_globs`` may widen: ``*.<ext>``, here or in a plain folder.
_KIND_GLOB_RE = re.compile(r"^(?:(?P<folder>[^*?\[]*)/)?\*\.(?P<ext>[A-Za-z0-9]+)$")


def _type_words(utterance: str, file_kinds: Mapping[str, str]) -> set[str]:
    """Extensions the request names as a plural or the start of a hyphenated word ("the mp4s",
    "mp4-Dateien", ".mov-Dateien"), which name the files' type as much as a bare ``mp4`` does."""
    u = utterance.lower()
    found: set[str] = set()
    for ext in file_kinds:
        e = re.escape(ext.lower())
        if re.search(r"(?:(?<![A-Za-z0-9_.-])|(?<!\w)\.)" + e + r"(?:s(?![A-Za-z0-9_])|-\w)", u):
            found.add(ext.lower())
    return found


def widen_kind_globs(
    plan: Sequence[Mapping[str, Any]],
    utterance: str,
    base: Path,
    file_kinds: Mapping[str, str],
    batch_kinds: Sequence[str],
    produces: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """*plan* with a batch glob for "every video" covering every video (owner, 2026-10-21).

    In a request that asks for many files, an input glob ``*.<ext>`` whose extension the request
    does not name, of a kind in *batch_kinds*, is followed by a glob for each other extension of
    that kind found in its folder under *base* (alphabetical, none the request names, none the
    list holds already): ``*.mp4`` for "make all videos here 720p" becomes
    ``["*.mp4", "*.mkv", "*.mov"]``. A one-path ``input`` becomes such a list, which the lift runs
    once per file. An input that is an earlier step's output is a link and stays. *plan* itself
    is left as it was. Rust mirror: ``knaif_core::batch_chain::widen_kind_globs``;
    contracts/parity/kind_glob_cases.json.

    A format named once, as the value of the step's ``produces: {arg}`` argument (*produces*,
    each tool's declaration), is what the step converts *to*, not a name of the files: "convert
    all videos to mp4" covers the mp4s and the other videos alike (owner, 2026-10-22).
    """
    from .agent import _format_mentions, _named_formats
    from .nl_clarify_gate import asks_for_many_files

    out = [copy.deepcopy(dict(s)) for s in plan]
    kinds = set(batch_kinds)
    if not kinds or not asks_for_many_files(utterance):
        return out
    formats = _named_formats(utterance, file_kinds)
    typed = _type_words(utterance, file_kinds)
    excluded = _excluded_formats(utterance, file_kinds)
    # The extensions each step's kind globs take, as written. Steps that share one read the same
    # files ("a thumbnail and a gif of every video") and each widens fully; steps that share none
    # split the kinds between them ("*.mp4 to mp4, *.mov to mov"), and the widening never adds to
    # one what another takes or was given (garden.mkv was converted twice).
    all_outputs = {_norm(o) for s in out for o in [(s.get("args") or {}).get("output")] if o}
    taken: list[set[tuple[str, str]]] = [
        {
            ((m["folder"] or "").lower(), m["ext"].lower())
            for entries in _input_lists(step)
            for entry in entries
            if (m := _KIND_GLOB_RE.match(entry.replace("\\", "/")))
            and _norm(entry) not in all_outputs
        }
        for step in out
    ]
    own = [set(t) for t in taken]
    outputs: set[str] = set()
    for i, step in enumerate(out):
        args = step.get("args")
        if not isinstance(args, dict):
            continue
        claimed = set().union(
            *(taken[j] for j in range(len(out)) if j != i and own[j] and not own[j] & own[i])
        )
        named = formats | typed | excluded
        target = _target_format(step, produces or {})
        if target in formats and target not in typed and _format_mentions(utterance, target) == 1:
            named = named - {target}
        for key in INPUT_KEYS:
            value = args.get(key)
            entries = [value] if isinstance(value, str) else value
            if not isinstance(entries, list):
                continue
            held = {_norm(e) for e in entries if isinstance(e, str)}
            widened: list[Any] = []
            for entry in entries:
                m = (
                    _KIND_GLOB_RE.match(entry.replace("\\", "/"))
                    if isinstance(entry, str)
                    else None
                )
                if m and m["ext"].lower() in excluded and _norm(entry) not in outputs:
                    continue  # "except the mov files": the glob goes, the widening adds none
                widened.append(entry)
                if not m or _norm(entry) in outputs:
                    continue
                ext, folder = m["ext"].lower(), m["folder"] or ""
                if ext in named or file_kinds.get(ext) not in kinds:
                    continue
                where = base / folder
                found = (
                    {p.suffix.lstrip(".").lower() for p in where.iterdir() if p.is_file()}
                    if where.is_dir()
                    else set()
                )
                for other in sorted(found - named - {ext}):
                    glob = posixpath.join(folder, f"*.{other}") if folder else f"*.{other}"
                    if (folder.lower(), other) in claimed:
                        continue  # a step that splits the kinds with this one takes it
                    if file_kinds.get(other) == file_kinds[ext] and _norm(glob) not in held:
                        held.add(_norm(glob))
                        taken[i].add((folder.lower(), other))
                        widened.append(glob)
            if widened != entries:
                args[key] = widened
        output = args.get("output")
        if isinstance(output, str) and output:
            outputs.add(_norm(output))
    return _drop_emptied_steps(plan, out) if excluded else out


#: "except the mov files": an exclusion word, then within three words an extension.
#: "без звук" (without sound) names no extension and excludes nothing.
_EXCLUSION_RE = re.compile(
    r"(?:\b(?:except|excluding|exclude|other\s+than|apart\s+from|but\s+not|außer|ausser|"
    r"ausgenommen|bis\s+auf|excepto|salvo|menos|sauf|hormis|освен|без)|除了)"
    r"([^,;:!?，。；]*)",
    re.IGNORECASE,
)
#: The words after an exclusion: Latin runs, read up to four, stopping at the first other run
#: ("除了mov以外…为mp4" stops at "以外", "без mov файловете" at "файловете").
_EXCLUDED_WORD_RE = re.compile(r"[a-z0-9]+|[^\sa-z0-9.]+")


def _excluded_formats(utterance: str, file_kinds: Mapping[str, str]) -> set[str]:
    """Extensions the request excludes ("every video except the mov files", "без mov файловете",
    "除了mov以外"): an exclusion word, then an extension the skill knows within four words, read
    up to the first word in another script."""
    found: set[str] = set()
    for m in _EXCLUSION_RE.finditer(utterance.lower()):
        for word in _EXCLUDED_WORD_RE.findall(m.group(1))[:4]:
            if not word.isascii():
                break
            for ext in (word, word[:-1] if word.endswith("s") else word):
                if ext in file_kinds:
                    found.add(ext)
    return found


def _input_lists(step: Mapping[str, Any]) -> list[list[str]]:
    """Each input arg of *step* as a list of paths."""
    args = step.get("args") or {}
    lists = []
    for key in INPUT_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            lists.append([value])
        elif isinstance(value, list):
            lists.append([v for v in value if isinstance(v, str)])
    return lists


def _drop_emptied_steps(
    plan: Sequence[Mapping[str, Any]], out: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """*out* without the steps the exclusion left with no input; if a later step reads what such
    a step writes (as the plan was written), the plan stays as written: dropping it would break
    the chain."""
    emptied = [
        i
        for i, (before, after) in enumerate(zip(plan, out, strict=True))
        if any(_input_lists(before)) and not any(entries for entries in _input_lists(after))
    ]
    for i in emptied:
        written = (plan[i].get("args") or {}).get("output")
        later_reads = {_norm(e) for s in plan[i + 1 :] for es in _input_lists(s) for e in es}
        if isinstance(written, str) and _norm(written) in later_reads:
            return [copy.deepcopy(dict(s)) for s in plan]
    return [s for i, s in enumerate(out) if i not in emptied]


def _target_format(step: Mapping[str, Any], produces: Mapping[str, Any]) -> str | None:
    """The format *step* converts to: the value of its tool's ``produces: {arg}`` argument."""
    declared = produces.get(str(step.get("tool")))
    if not isinstance(declared, Mapping):
        return None
    value = (step.get("args") or {}).get(str(declared.get("arg")))
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower().lstrip(".")


def _norm(path: str) -> str:
    """A path as a link compares it: forward slashes, no leading ``./``, any case."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lower()


def _entries(args: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """``(key, paths)`` for each input arg the step carries, a lone string as a one-item list."""
    out: list[tuple[str, list[str]]] = []
    for key in INPUT_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            out.append((key, [value]))
        elif isinstance(value, list):
            out.append((key, [v for v in value if isinstance(v, str)]))
    return out


def _batch_files(args: Mapping[str, Any], expand: Mapping[str, Sequence[str]]) -> list[str]:
    """The files a head step runs over, in input order, each once; empty when it is no head."""
    entries = [p for _, paths in _entries(args) for p in paths]
    if not any(expand.get(p) for p in entries):
        return []
    files: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        for path in expand.get(entry) or ([] if entry in expand else [entry]):
            if _norm(path) not in seen:
                seen.add(_norm(path))
                files.append(path)
    return files


def _reads_one_file(step: Mapping[str, Any]) -> bool:
    """Whether *step* takes its file through ``input`` (one path). Such a tool cannot expand a
    glob, so a lone batch step on it is repeated by core too; a glob in ``inputs`` (a list) stays
    the skill's own batch (``run_batch``). A list there is a batch of globs
    (:func:`widen_kind_globs`), one file each once lifted."""
    return isinstance((step.get("args") or {}).get("input"), (str, list))


def _output_name(model_output: str, batch_file: str, taken: set[str]) -> str:
    """The name *model_output* gets for *batch_file* (rules in the module docstring)."""
    folder, base = posixpath.split(batch_file.replace("\\", "/"))
    stem, file_ext = posixpath.splitext(base)
    model_folder, model_base = posixpath.split(model_output.replace("\\", "/"))
    model_stem, ext = posixpath.splitext(model_base)
    if "*" in model_output:
        name = model_base.replace("*", stem)
        candidate = posixpath.join(model_folder or folder, name)
        folder = model_folder or folder
        stem, ext = posixpath.splitext(name)
        label = model_stem.replace("*", "").strip("_-. ")
    else:
        ext = ext or file_ext
        candidate = posixpath.join(folder, stem + ext)
        label = model_stem
    if _norm(candidate) in taken and label:
        candidate = posixpath.join(folder, f"{stem}_{label}{ext}")
    base_stem = posixpath.splitext(posixpath.basename(candidate))[0]
    n = 2
    while _norm(candidate) in taken:
        candidate = posixpath.join(folder, f"{base_stem}_{n}{ext}")
        n += 1
    taken.add(_norm(candidate))
    return candidate


@dataclass
class BatchLift:
    """A lifted plan and, for each of its steps, the batch file it works on.

    ``file_of[k]`` is the index into ``files`` of step *k*'s file, ``None`` for a step outside
    the chain or a join. ``join_files[k]`` maps each input arg of join step *k* to the file
    each of its entries came from (``None`` for an entry the model named), so a file that fails
    can be left out of the join.
    """

    plan: list[dict[str, Any]]
    file_of: list[int | None] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    join_files: dict[int, dict[str, list[int | None]]] = field(default_factory=dict)
    #: Each file as the user knows it (its place in their folder), for reports.
    shown: list[str] = field(default_factory=list)

    @property
    def lifted(self) -> bool:
        return bool(self.files)


def lift_per_file_chain(
    plan: Sequence[Mapping[str, Any]],
    expand: Mapping[str, Sequence[str]],
    joining_tools: set[str],
    batch_outputs: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """*plan* with its batch chain repeated for every file; *plan* itself is left as it was.

    *expand* maps each glob (or folder) the caller resolved to its files, in input order;
    *joining_tools* are the tools that read many files into one result; *batch_outputs* maps a
    tool that writes a file only in a batch (``batch_output:``) to that file's extension.
    """
    return lift_batch(plan, expand, joining_tools, batch_outputs=batch_outputs).plan


def lift_batch(
    plan: Sequence[Mapping[str, Any]],
    expand: Mapping[str, Sequence[str]],
    joining_tools: set[str],
    *,
    home: Mapping[str, str] | None = None,
    single: bool = False,
    batch_outputs: Mapping[str, str] | None = None,
) -> BatchLift:
    """:func:`lift_per_file_chain`, keeping which file every lifted step works on.

    *home* maps a batch file that lives in the run scratch (a piece of a split routed there) to
    its place in the user's folder: its results are named from that place, and reports show it.
    *single* lifts a head that nothing else reads too: the pieces of a split exist only as a
    set core planned, so no skill could expand them (a folder's glob is the skill's own).
    *batch_outputs*: a lifted step of one of these tools with no output of its own writes
    ``<batch file's name>.<ext>`` next to it ("extract their text" saves a .txt per PDF).
    """
    home = home or {}
    batch_outputs = batch_outputs or {}
    steps = [copy.deepcopy(dict(s)) for s in plan]

    head, files = -1, []
    for i, step in enumerate(steps):
        if step.get("tool") in joining_tools:
            continue
        files = _batch_files(step.get("args") or {}, expand)
        if files:
            head = i
            break
    if head < 0:
        return BatchLift(steps)

    # Which later steps read a chain step's output, by name or by pattern.
    links: dict[str, int] = {}
    chain: list[int] = [head]
    joins: list[int] = []

    def _link(i: int) -> None:
        output = (steps[i].get("args") or {}).get("output")
        if isinstance(output, str) and output:
            links[_norm(output)] = i

    # Every later step on the head's own files ("a thumbnail and a short gif of every video":
    # one step per result, each on the batch glob) starts a chain of its own for each file.
    heads = {head}
    same_files = [_norm(f) for f in files]
    _link(head)
    for i in range(head + 1, len(steps)):
        args = steps[i].get("args") or {}
        if (
            steps[i].get("tool") not in joining_tools
            and [_norm(f) for f in _batch_files(args, expand)] == same_files
        ):
            heads.add(i)
            chain.append(i)
            _link(i)
            continue
        if not any(_norm(p) in links for _, paths in _entries(args) for p in paths):
            continue
        if steps[i].get("tool") in joining_tools:
            joins.append(i)
        else:
            chain.append(i)
            _link(i)
    if len(chain) == 1 and not joins and not single and not _reads_one_file(steps[head]):
        return BatchLift(steps)

    # Every chain step's output for every file, chosen file by file in either order.
    shown = [home.get(f, f) for f in files]
    taken = {_norm(f) for f in [*files, *shown]}
    outputs: dict[tuple[int, int], str] = {}
    for f, batch_file in enumerate(shown):
        for i in chain:
            output = (steps[i].get("args") or {}).get("output")
            if not (isinstance(output, str) and output) and steps[i].get("tool") in batch_outputs:
                output = f"*.{batch_outputs[steps[i]['tool']]}"
            if isinstance(output, str) and output:
                outputs[(i, f)] = _output_name(output, batch_file, taken)

    def _render(i: int, f: int) -> dict[str, Any]:
        step = copy.deepcopy(steps[i])
        args = step.get("args") or {}
        for key, paths in _entries(args):
            if i in heads:
                new = [files[f]]
            else:
                new = [
                    outputs.get((links[_norm(p)], f), p) if _norm(p) in links else p for p in paths
                ]
            args[key] = new[0] if key == "input" or isinstance(args[key], str) else new
        if (i, f) in outputs:
            args["output"] = outputs[(i, f)]
        step["args"] = args
        return step

    def _join(i: int) -> tuple[dict[str, Any], dict[str, list[int | None]]]:
        step = steps[i]
        args = step.get("args") or {}
        sources: dict[str, list[int | None]] = {}
        for key, paths in _entries(args):
            new: list[str] = []
            came_from: list[int | None] = []
            for p in paths:
                if _norm(p) in links:
                    new.extend(outputs[(links[_norm(p)], f)] for f in range(len(files)))
                    came_from.extend(range(len(files)))
                else:
                    new.append(p)
                    came_from.append(None)
            args[key] = new
            sources[key] = came_from
        step["args"] = args
        return step, sources

    block = [(i, f) for f in range(len(files)) for i in chain]

    lift = BatchLift([], files=files, shown=shown)
    for i, step in enumerate(steps):
        if i == head:
            for j, f in block:
                lift.plan.append(_render(j, f))
                lift.file_of.append(f)
        elif i in joins:
            joined, sources = _join(i)
            lift.join_files[len(lift.plan)] = sources
            lift.plan.append(joined)
            lift.file_of.append(None)
        elif i not in chain:
            lift.plan.append(step)
            lift.file_of.append(None)
    return lift


# ── one file into many ───────────────────────────────────────────────────────────────────────
#
# A step whose tool writes many files from one (split_video, extract_frames, create_thumbnail at
# several times, split_pdf every N pages) is told by its skill, before anything runs, the set's
# name and its files (owner, 2026-10-20: the names are known before running). Later steps that
# read the set are pointed at that one name, so scratch routing can move the whole set, and the
# lift then runs them once per file. contracts/parity/set_link_cases.json.


@dataclass(frozen=True)
class PlannedSet:
    """The files one step will write: the set's *name* (what its ``output`` becomes, e.g. the
    sequence pattern ``clip_%03d.mp4``) and the *files*, in order."""

    name: str
    files: Sequence[str]


def _matches_all(glob: str, files: Sequence[str]) -> bool:
    """Whether the glob *glob* matches every one of *files* (any case, ``/``, no ``./``)."""
    pattern = _norm(glob)
    return bool(files) and all(fnmatch.fnmatchcase(_norm(f), pattern) for f in files)


def link_set_readers(
    plan: Sequence[Mapping[str, Any]], sets: Mapping[int, PlannedSet]
) -> list[dict[str, Any]]:
    """*plan* with every step that reads a planned set pointed at the set's name.

    A step reads the set of producer *p* (a key of *sets*) when one of its inputs, after *p*, is
    *p*'s own output name, the set's name, or a glob matching every file of the set. The
    producer's ``output`` becomes the set's name once a step reads it. *plan* is left as it was.
    """
    steps = [copy.deepcopy(dict(s)) for s in plan]
    for p in sorted(sets):
        planned = sets[p]
        output = (steps[p].get("args") or {}).get("output")
        refs = {_norm(planned.name)} | (
            {_norm(output)} if isinstance(output, str) and output else set()
        )
        linked = False
        for i in range(p + 1, len(steps)):
            args = steps[i].get("args") or {}
            for key, paths in _entries(args):
                new = []
                for path in paths:
                    reads = _norm(path) in refs or (
                        any(c in path for c in "*?[") and _matches_all(path, planned.files)
                    )
                    new.append(planned.name if reads else path)
                    linked = linked or reads
                args[key] = new[0] if isinstance(args[key], str) else new
        if linked:
            steps[p].setdefault("args", {})["output"] = planned.name
    return steps


# ── the batch report ─────────────────────────────────────────────────────────────────────────
#
# One failed file does not stop the others; a failure is printed when it happens and the run
# ends with counts, time and the size of what it left (owner, 2026-10-13). Both runtimes print
# this text, on stderr; contracts/parity/batch_report_cases.json pins it.


def first_line(text: str) -> str:
    """The first non-blank line of *text*, trimmed."""
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


def failure_line(file: str, reason: str) -> str:
    """``✗ silent.mov: No audio to extract …``: the file as the plan names it, one line."""
    return f"✗ {file.replace(chr(92), '/')}: {first_line(reason) or 'failed'}"


def format_size(size: int) -> str:
    """``512 B``, ``1.5 KB``, ``35.2 MB``, ``3.0 GB`` (1024 to the step)."""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            break
    return f"{value:.1f} {unit}"


def summary_line(done: int, failed: int, seconds: float, size: int) -> str:
    """``8 done, 2 failed in 12.4s, 35.2 MB``; no failed part when nothing failed."""
    counts = f"{done} done, {failed} failed" if failed else f"{done} done"
    return f"{counts} in {seconds:.1f}s, {format_size(size)}"


@dataclass
class BatchReport:
    """How a batch went: every file, the ones that failed and why, time, size left."""

    files: list[str]
    failures: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0
    size_bytes: int = 0

    @property
    def failed(self) -> int:
        return len(self.failures)

    @property
    def done(self) -> int:
        return len(self.files) - self.failed

    def summary(self) -> str:
        return summary_line(self.done, self.failed, self.seconds, self.size_bytes)
