"""CommandAgent – main entry point for the local command agent."""

from __future__ import annotations

import copy
import json
import os
import re
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .batch_chain import (
    BatchLift,
    BatchReport,
    PlannedSet,
    failure_line,
    first_line,
    lift_batch,
    link_set_readers,
)
from .core_tools import CORE_TOOL_DEFS
from .evaluator import compute_metrics, run_eval
from .executor import HANDLERS as _EXECUTOR_HANDLERS
from .handler_api import HandlerContext
from .nl_clarify_gate import (
    _named_by_stem,
    count_question,
    nl_clarify_gate,
    required_args_clarify,
    restore_grounded_args,
    unsupported_args_clarify,
)
from .planner import (
    _VALID_FILE_TYPES,
    StemAmbiguousError,
    StemNotFoundError,
    _resolve_path,
    alert_last,
    apply_defaults,
    classify_preflight_errors,
    normalize_plan,
    optimize_plan,
    parse_plan,
    resolve_args,
    resolve_stems,
    split_alert,
    summarize_plan_steps,
    validate_arg_by_schema,
    validate_plan,
)
from .prompt import build_prompt, normalize_path_separators
from .registry import ToolDef, load_registry, retrieve_tools
from .result_set import Result, leaves_line, result_set, same_for_line
from .scratch import RunScratch, display, wants_everything_kept
from .skill import Skill

_TERMINAL_TOOLS = frozenset({"done", "clarify", "reject"})
_FILENAME_RE = re.compile(r"\.[a-z][a-z0-9]{1,4}$", re.IGNORECASE)


def _step_failed(result: Any) -> bool:
    """Return True if a handler result signals a non-zero exit code.

    Checks the top-level ``returncode`` (run_concat pattern) and per-output
    ``returncode`` entries (run_batch pattern).
    """
    if not isinstance(result, dict):
        return False
    if result.get("status") == "failed":
        return True
    rc = result.get("returncode")
    if rc is not None and rc != 0:
        return True
    for out in result.get("outputs") or []:
        if isinstance(out, dict):
            out_rc = out.get("returncode")
            if out_rc is not None and out_rc != 0:
                return True
    return False


def _failure_reason(result: Any) -> str:
    """Why a failed step failed, for the batch report: its error, else the last line a failed
    command wrote (where ffmpeg puts the cause)."""
    if not isinstance(result, dict):
        return ""
    if result.get("error"):
        return str(result["error"])
    tails = [str(result.get("stderr") or "")]
    tails += [
        str(o.get("stderr_tail") or o.get("stderr") or "")
        for o in result.get("outputs") or []
        if isinstance(o, dict) and o.get("returncode") not in (None, 0)
    ]
    for tail in tails:
        lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
        if lines:
            return lines[-1]
    return ""


def _size_left(results: list[dict[str, Any]], base: Path, scratch: Path) -> int:
    """Bytes of the files the run's steps report writing (``output``, ``outputs``, ``files``)
    that are still there after it, outside the run scratch, each counted once. The fs toolkit's
    ``resolve_inputs`` reports what a step reads under ``files``: those are inputs, not left."""
    paths: set[Path] = set()
    for r in results:
        res = r.get("result")
        if not isinstance(res, dict) or r.get("tool") == "resolve_inputs":
            continue
        named: list[Any] = [res.get("output"), *(res.get("files") or [])]
        for o in res.get("outputs") or []:
            named.append(o.get("output") if isinstance(o, dict) else o)
        for value in named:
            if isinstance(value, str) and value:
                p = Path(value) if Path(value).is_absolute() else base / value
                paths.add(p.resolve())
    work = scratch.resolve()
    return sum(p.stat().st_size for p in paths if p.is_file() and not p.is_relative_to(work))


def _without_folder(text: str, base: Path) -> str:
    """*text* with the folder the run works in left out of every path in it: a reason names
    ``clip.mov``, never the user's folders."""
    for folder in {str(base), str(base.resolve())}:
        for spelling in {folder, folder.replace("\\", "/")}:
            text = text.replace(spelling + "\\", "").replace(spelling + "/", "")
            text = text.replace(spelling, ".")
    return text


class PlanWouldFail(ValueError):
    """The rehearsal before a real run found a step that fails, so nothing ran (owner,
    2026-10-24). *where* says which step (native ``rehearsal_failure_context``), *cause*
    why; the CLI prints them as native's error chain."""

    def __init__(self, where: str, cause: str) -> None:
        super().__init__(f"{where}: {cause}")
        self.where = where
        self.cause = cause


def rehearsal_failure_context(idx: int, total: int) -> str:
    """Which step the rehearsal stopped at; nothing had run. Native ``rehearsal_failure_context``."""
    return f"step {idx + 1} of {total} would fail, so nothing ran"


#: Set on an error raised while a step ran: `(index, steps)`, the step it stopped at. The
#: CLI says where it stopped from it, as native's `chain_failure_context` does.
STEP_ATTR = "knaif_step"

#: When every file of a batch fails the rehearsal. Native `EVERY_FILE_WOULD_FAIL`.
EVERY_FILE_WOULD_FAIL = "every file would fail, so nothing ran"


class _FileTally:
    """How each file of a step over many files ended (``ctx.file_finished``), intent by intent.

    An intent's reports are held until it has reported a second file: one file is no batch, and
    its failure takes the usual error path. From then on each failure line goes to *emit* as
    it comes. Names are shown relative to *base* when inside it, else as the bare file name.
    """

    def __init__(self, emit: Callable[[str], None] | None, base: Path) -> None:
        self.emit = emit
        self.base = base
        self.entries: list[tuple[str, str | None]] = []
        self._intent: list[tuple[str, str | None]] = []

    def start_intent(self) -> None:
        self._intent = []

    def record(self, file: str, failure: str | None) -> None:
        reason = (
            None if failure is None else first_line(_without_folder(display(failure), self.base))
        )
        entry = (self._name(file), reason)
        self._intent.append(entry)
        if len(self._intent) == 2:
            for held in self._intent:
                self._keep(*held)
        elif len(self._intent) > 2:
            self._keep(*entry)

    def partial(self) -> bool:
        """This intent is a batch in which at least one file is done: a failed command among
        them does not stop the run."""
        return len(self._intent) >= 2 and any(r is None for _, r in self._intent)

    def _keep(self, name: str, reason: str | None) -> None:
        self.entries.append((name, reason))
        if reason is not None and self.emit is not None:
            self.emit(failure_line(name, reason or ""))

    def _name(self, file: str) -> str:
        p = Path(file)
        if not p.is_absolute():
            return file.replace("\\", "/")
        try:
            return p.resolve().relative_to(self.base.resolve()).as_posix()
        except ValueError:
            return p.name


def _iter_string_values(args: dict[str, Any]) -> Iterator[str]:
    for value in args.values():
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, str):
                    yield v


def _basename(value: str) -> str:
    """Last path component of *value*, splitting on either separator."""
    return re.split(r"[\\/]", value)[-1]


def _extension(value: str) -> str:
    """Lower-cased extension of *value*'s basename, without the dot ('' when none)."""
    name = _basename(value)
    dot = name.rfind(".")
    return name[dot + 1 :].lower() if dot > 0 else ""


def _names_output(args: Mapping[str, Any]) -> bool:
    """True when a step's *args* name the file it writes (a non-empty ``output``)."""
    out = args.get("output")
    return isinstance(out, str) and bool(out.strip())


def _written_kind(
    step: Mapping[str, Any],
    file_kinds: Mapping[str, str],
    produces: Mapping[str, Any],
) -> str | None:
    """The kind of file *step* writes: its tool's ``produces:``, else its explicit output's.

    The declaration is the skill's word and the output extension is the model's, so the
    declaration decides when both exist (owner, 2026-10-15): ``extract_audio`` writing
    ``clip_audio.mp4`` still writes audio, and the wrong extension cannot delete a second
    result. A ``{"arg": name}`` declaration reads that argument; without a usable value, and
    for a tool declaring nothing, the output's extension decides.
    """
    args = step.get("args") or {}
    declared = produces.get(str(step.get("tool")))
    if isinstance(declared, str):
        return declared
    if isinstance(declared, Mapping):
        value = args.get(str(declared.get("arg")))
        if isinstance(value, str) and value.strip():
            kind = file_kinds.get(value.lower().lstrip("."))
            if kind is not None:
                return kind
    out = args.get("output")
    if isinstance(out, str) and _extension(out):
        return file_kinds.get(_extension(out))
    return None


def _written_extension(step: Mapping[str, Any], produces: Mapping[str, Any]) -> str | None:
    """The extension *step* writes, for the request signal: its explicit output's, else the value
    of its ``produces: {arg}`` argument; None when unknown."""
    args = step.get("args") or {}
    out = args.get("output")
    if isinstance(out, str) and _extension(out):
        return _extension(out)
    declared = produces.get(str(step.get("tool")))
    if isinstance(declared, Mapping):
        value = args.get(str(declared.get("arg")))
        if isinstance(value, str) and value.strip():
            return value.strip().lower().lstrip(".")
    return None


def _named_formats(utterance: str, file_kinds: Mapping[str, str]) -> set[str]:
    """Extensions the request names as bare words: ``to mp4``, ``as .gif``, or Chinese without
    spaces (``转换为mp4``); never the extension inside a filename (``clip.mp4``). Extensions
    are ASCII in every language, so this needs no word lists. The boundaries are those of
    :func:`_mention_count`; a leading dot counts when nothing name-like precedes it."""
    return {ext.lower() for ext in file_kinds if _format_mentions(utterance, ext)}


def _format_mentions(utterance: str, ext: str) -> int:
    """How many times the request names the format *ext* as a word (:func:`_named_formats`)."""
    u, e = utterance.lower(), re.escape(ext.lower())
    after = r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_])"
    bare = r"(?<![A-Za-z0-9_.-])" + e + after
    dotted = r"(?<!\w)\." + e + after
    return len(re.findall(bare, u)) + len(re.findall(dotted, u))


def _asked_for(
    intermediate: str,
    producer: Mapping[str, Any],
    later: list[dict[str, Any]],
    target: str,
    key: Callable[[str], str],
    utterance: str,
    file_kinds: Mapping[str, str] | None,
    produces: Mapping[str, Any] | None,
    derives: Collection[str] | None,
    joining: Collection[str] = (),
) -> bool:
    """True when the user asked for *intermediate* although they never named the file.

    The keep rule (owner, 2026-10-15): one rule, two deterministic signals, no tool named in core.

    * **Tool signal:** every later step reading it makes a *new thing* from it: a file of
      another kind (``file_kinds``; what a reader writes per :func:`_written_kind`), or a tool
      declaring ``derives: true`` (a piece, a page range, a join). "Compress report.pdf and
      extract pages 1-3" keeps the compressed whole. **Except** when every reader is a join
      (*joining*): pieces made for a join are on the way (owner, 2026-10-20), so "compress all
      PDFs here and merge them into one" leaves only the merged file.
    * **Request signal:** the user named its format as a bare word (:func:`_named_formats`),
      the format is not the producer's own input extension ("cut the mp4 … and make a gif"
      names the source), and no reader writes that format again. "Convert clip.mov to mp4 and
      make a gif preview" keeps the mp4; "convert to mp4 and make it smaller" does not.

    Otherwise a reader refines it, and it goes to the scratch. Readers stop at a later step
    that writes the same file again.
    """
    kinds, made = file_kinds or {}, produces or {}
    deriving = set(derives or ())
    own_ext = _extension(intermediate)
    own_kind = kinds.get(own_ext)
    readers: list[dict[str, Any]] = []
    for step in later:
        if step.get("tool") in _TERMINAL_TOOLS:
            continue
        args = step.get("args") or {}
        reads = any(
            isinstance(v, str) and v and key(v) == target
            for k, raw in args.items()
            if k != "output"
            for v in (raw if isinstance(raw, list) else [raw])
        )
        if reads:
            readers.append(step)
        out = args.get("output")
        if isinstance(out, str) and out and key(out) == target:
            break  # a later step writing the same file again ends this file's readers
    if not readers:
        return False

    def makes_new_thing(step: Mapping[str, Any]) -> bool:
        if step.get("tool") in deriving:
            return True
        kind = _written_kind(step, kinds, made)
        return own_kind is not None and kind is not None and kind != own_kind

    only_joins = all(s.get("tool") in joining for s in readers)
    if not only_joins and all(makes_new_thing(s) for s in readers):
        return True
    if own_ext and own_ext in _named_formats(utterance, kinds):
        source_exts = {
            _extension(v)
            for v in _iter_string_values(
                {k: v for k, v in (producer.get("args") or {}).items() if k != "output"}
            )
        }
        if own_ext not in source_exts and all(
            (w := _written_extension(s, made)) is not None and w != own_ext for s in readers
        ):
            return True
    return False


def _mention_count(utterance: str, name: str) -> int:
    """How many times the filename *name* appears in *utterance*, case-insensitively.

    A match must stand alone as a filename: ``clip.mp4`` inside ``myclip.mp4`` or
    ``clip.mp4.bak`` is not a mention, while a ``./`` prefix or trailing punctuation
    (``clip.mp4,``) is. Only ASCII letters, digits, ``_``, ``.`` and ``-`` extend a name:
    Chinese and Japanese are written without spaces ("为clip.mp4生成"), so a Unicode
    word-character boundary would hide every mention there. Pass a basename; the
    utterance is expected already normalized.
    """
    pattern = r"(?<![A-Za-z0-9_.-])" + re.escape(name.lower()) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_])"
    return len(re.findall(pattern, utterance.lower()))


def _intermediate_name(src: str, taken: set[str]) -> str:
    """Derive a chain-intermediate filename from *src*, avoiding *taken* basenames.

    Preserves *src*'s directory prefix and extension and inserts a ``-chained``
    marker (``report.pdf`` → ``report-chained.pdf``). *taken* holds already-used
    output basenames (lower-cased) so repeated transforms of one source don't
    collide.
    """
    name = _basename(src)
    prefix = src[: len(src) - len(name)]
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    n = 1
    while True:
        marker = "-chained" if n == 1 else f"-chained{n}"
        candidate = f"{prefix}{stem}{marker}{ext}"
        if _basename(candidate).lower() not in taken:
            return candidate
        n += 1


if TYPE_CHECKING:
    from .orchestrator import InferenceOrchestrator


def is_unsafe_request(utterance: str, phrases: Sequence[str]) -> bool:
    """Whether every word of one of the skill's unsafe phrases is a word of *utterance*: such a
    request never reaches the model. Native ``knaif_core::is_unsafe_request``; an empty phrase
    matches nothing."""
    tokens = set(utterance.lower().split())
    return any(
        words and all(word in tokens for word in words)
        for words in (phrase.lower().split() for phrase in phrases)
    )


class CommandAgent:
    """
    Ties together the YAML-driven tool registry, plan validation, execution,
    LLM inference, and evaluation.

    Parameters
    ----------
    tools_yaml_path:
        Path to the YAML file that defines the available tools.
    sandbox:
        Root directory that all file operations are confined to.
    root:
        Workspace root used when resolving relative paths. Defaults to cwd.
    orchestrator:
        Optional :class:`~knaif.orchestrator.InferenceOrchestrator`.
        Required only for real (non-mock) inference.
    """

    def __init__(
        self,
        tools_yaml_path: Path | str | None = None,
        sandbox: Path | str | None = None,
        root: Path | str | None = None,
        orchestrator: InferenceOrchestrator | None = None,
        handlers: dict[str, Any] | None = None,
        skill_dir: Path | str | None = None,
        system_header: str | None = None,
        examples_block: str | None = None,
        arg_value_sets: dict[str, frozenset[str]] | None = None,
        expanders: dict[str, Any] | None = None,
        confirmer: Any | None = None,
        unsafe_phrases: tuple[str, ...] = (),
        file_kinds: dict[str, str] | None = None,
        batch_kinds: list[str] | None = None,
        scratch_intermediates: bool = False,
        summarizers: dict[str, Any] | None = None,
        preflights: dict[str, Any] | None = None,
        result_formatter: Any | None = None,
        artifact_runner: Any | None = None,
        show_plan: bool = False,
        require_approval: bool = False,
        plan_display: Any | None = None,
        plan_confirmer: Any | None = None,
        intent_completed: Any | None = None,
        _registry: dict[str, ToolDef] | None = None,
    ) -> None:
        if _registry is not None:
            self.registry = dict(_registry)
        elif tools_yaml_path is not None:
            self.registry = load_registry(tools_yaml_path)
        else:
            raise ValueError("Either tools_yaml_path or _registry must be provided.")
        _merge_core_tool_defs(self.registry)
        # Tools that accept an `output` arg — the registry is static for the
        # agent's lifetime, so compute the set once instead of per inference.
        self._output_capable: set[str] = {
            name
            for name, td in self.registry.items()
            if "output" in set(td.required_args) | set(td.optional_args)
        }
        self.unsafe_phrases: tuple[str, ...] = unsafe_phrases
        # Extension -> kind, from the skill's `file_kinds:`; chain threading never crosses kinds.
        self.file_kinds: dict[str, str] = file_kinds or {}
        # From the skill's `batch_kinds:`: a batch glob of these kinds covers the whole kind.
        self.batch_kinds: list[str] = list(batch_kinds or [])
        # From the skill's `scratch_intermediates:`; see _route_intermediates_to_scratch.
        self.scratch_intermediates = scratch_intermediates
        # `--keep-intermediates`: every step's output stays where the plan put it,
        # nothing goes to the scratch. Request words ("keep the intermediate files") do the same
        # (`knaif.scratch.wants_everything_kept`).
        self.keep_intermediates: bool = False
        # Called with each failure line of a batch chain as it happens (the CLI prints it on
        # stderr); the run's BatchReport is `last_batch_report`, None when nothing was lifted.
        self.batch_failed: Callable[[str], None] | None = None
        self.last_batch_report: BatchReport | None = None
        self._file_tally: _FileTally | None = None
        self._isolated_failure: str | None = None
        # What the last plan leaves (knaif.result_set), computed before it runs.
        self.last_result_set: list[Result] = []
        # URL inputs (`url_inputs:` in skill.yaml, set by from_skill): None means the skill
        # works on local files only. Nothing is downloaded unless `allow_download` (the CLI's
        # `--allow download`) or `download_confirmer(url, max_bytes)` says yes, once per URL.
        self.url_inputs: Any | None = None
        self.allow_download: bool = False
        self.download_confirmer: Any | None = None
        # Only the executing eval sets this: its dry run stands in for a real run (the plan is
        # replayed for real afterwards), so the preview reads the downloaded bytes. A user's
        # dry run never makes a request.
        self.fetch_in_preview: bool = False
        self.last_downloads: list[dict[str, Any]] = []
        self.sandbox: Path | None = Path(sandbox).resolve() if sandbox else None
        self.root: Path = Path(root).resolve() if root else Path.cwd()
        self.orchestrator = orchestrator
        self.handlers: dict[str, Any] = handlers if handlers is not None else _EXECUTOR_HANDLERS
        self.skill_dir: Path = Path(skill_dir).resolve() if skill_dir else self.root
        self.system_header: str | None = system_header
        self.examples_block: str | None = examples_block
        self.arg_value_sets: dict[str, frozenset[str]] = arg_value_sets or {}
        self.expanders: dict[str, Any] = expanders or {}
        self.confirmer: Any | None = confirmer
        self.summarizers: dict[str, Any] = summarizers or {}
        self.preflights: dict[str, Any] = preflights or {}
        self.result_formatter: Any | None = result_formatter
        self.artifact_runner: Any | None = artifact_runner
        self.show_plan: bool = show_plan
        self.require_approval: bool = require_approval
        self.plan_display: Any | None = plan_display
        self.plan_confirmer: Any | None = plan_confirmer
        self.intent_completed: Any | None = intent_completed
        # The run's shape for a CLI's display, as native's views draw it:
        # `plan_ready(steps, notes)` once before anything runs (a batch's first file's steps;
        # "the same for N more files" and what the plan leaves), then `intent_started(index,
        # total, step)`, 1-based, before each step (`Step i/N · tool`, `step i of N:`).
        self.plan_ready: Any | None = None
        self.intent_started: Any | None = None
        # `item_shown(item)`: a step's result line as it happens (`ctx.show`), e.g. each file of
        # a batch as it is done, as native prints it. A step leaves those out of its report.
        self.item_shown: Callable[[dict[str, Any]], None] | None = None
        # `run --stdout`: handlers that declare `stdout:` return their text and
        # save nothing (`HandlerContext.to_stdout`).
        self.to_stdout: bool = False
        self.prompt_examples: list[dict] = []
        # `rules:` from prompt.yaml, each added to the header only when one of its tools is in
        # the prompt, so a prompt without them is unchanged byte for byte.
        self.prompt_rules: list[dict] = []
        self.last_thinking: str = ""
        self.last_plan_summary: str = ""
        self.last_parse_error: str | None = None
        self.last_validation_error: str | None = None
        self.last_retried: bool = False
        # The plan as the model emitted it (parsed), before `infer` links chain intermediates
        # and threads reused sources in place. None when no model plan exists for the last call.
        self.last_model_plan: dict[str, Any] | None = None
        # Whether the last executed plan asked for an alert (`alert_when_done`), taken out of the
        # plan before execution.
        self.last_alert: bool = False
        # Set when a step other than a control tool ran in the current execute_plan: an alert
        # reports on work, never on a request answered before anything ran.
        self._work_started: bool = False
        self._intent_declined: bool = False
        # One corrective re-prompt on a parse/validation failure (validator-
        # feedback retry). Toggle off to measure the without-retry baseline.
        self.repair_invalid_plans: bool = True
        # Populated by from_skill(): the tool objects and the skill's hooks.
        self.tool_map: dict[str, Any] | None = None
        self.skill_instance: Any | None = None

    @classmethod
    def from_skill(
        cls,
        skill_dir: Path | str,
        sandbox: Path | str | None = None,
        root: Path | str | None = None,
        orchestrator: InferenceOrchestrator | None = None,
        confirmer: Any | None = None,
        show_plan: bool = False,
        require_approval: bool = False,
        plan_display: Any | None = None,
        plan_confirmer: Any | None = None,
        intent_completed: Any | None = None,
    ) -> CommandAgent:
        """Load a skill package and return a configured CommandAgent."""
        from .executor import CORE_HANDLERS

        skill = Skill.load(skill_dir)
        merged_handlers = {**CORE_HANDLERS, **skill.handlers}
        agent = cls(
            _registry=skill.registry,
            sandbox=sandbox,
            root=root,
            orchestrator=orchestrator,
            handlers=merged_handlers,
            skill_dir=skill.skill_dir,
            system_header=skill.system_header,
            examples_block=skill.examples_block,
            arg_value_sets=skill.arg_value_sets,
            expanders=skill.expanders,
            confirmer=confirmer,
            unsafe_phrases=skill.unsafe_phrases,
            file_kinds=skill.file_kinds,
            batch_kinds=skill.batch_kinds,
            scratch_intermediates=skill.scratch_intermediates,
            summarizers=skill.summarizers,
            preflights=skill.preflights,
            result_formatter=skill.result_formatter,
            artifact_runner=skill.artifact_runner,
            show_plan=show_plan,
            require_approval=require_approval,
            plan_display=plan_display,
            plan_confirmer=plan_confirmer,
            intent_completed=intent_completed,
        )
        agent.prompt_examples = skill.prompt_examples
        agent.prompt_rules = skill.prompt_rules
        agent.url_inputs = skill.url_inputs

        if skill.tool_map is not None:
            # OOP path: attach tool_map and skill_instance; build summarizer adapters
            from .tool import Intent

            agent.tool_map = skill.tool_map
            agent.skill_instance = skill.skill_instance
            # Adapters let summarize_plan_steps work unchanged (planner.py untouched)
            for t_name, tool_obj in skill.tool_map.items():
                if isinstance(tool_obj, Intent) and t_name not in agent.summarizers:
                    agent.summarizers[t_name] = lambda args, _t=tool_obj, **kw: _t.summarize(
                        args, **kw
                    )

        return agent

    @classmethod
    def from_registry(
        cls,
        registry: dict[str, ToolDef],
        tool_map: dict[str, Any],
        sandbox: Path | str | None = None,
        root: Path | str | None = None,
        orchestrator: InferenceOrchestrator | None = None,
        system_header: str | None = None,
        confirmer: Any | None = None,
        show_plan: bool = False,
        require_approval: bool = False,
        plan_display: Any | None = None,
        plan_confirmer: Any | None = None,
        intent_completed: Any | None = None,
    ) -> CommandAgent:
        """Build an agent from an already-constructed registry and tool_map.

        This is the in-memory seam used by ``knaif.cli`` SDK to build agents
        from Python callables without any YAML files on disk.
        """
        from .executor import CORE_HANDLERS
        from .tool import Intent

        agent = cls(
            _registry=registry,
            sandbox=sandbox,
            root=root,
            orchestrator=orchestrator,
            handlers=CORE_HANDLERS,
            system_header=system_header,
            confirmer=confirmer,
            show_plan=show_plan,
            require_approval=require_approval,
            plan_display=plan_display,
            plan_confirmer=plan_confirmer,
            intent_completed=intent_completed,
        )
        agent.tool_map = tool_map

        for t_name, tool_obj in tool_map.items():
            if isinstance(tool_obj, Intent) and t_name not in agent.summarizers:
                agent.summarizers[t_name] = lambda args, _t=tool_obj, **kw: _t.summarize(args, **kw)

        return agent

    # ── plan handling ─────────────────────────────────────────────────────────

    def parse_plan(self, json_text: str) -> dict[str, Any]:
        """Parse *json_text* and return a plan payload dict."""
        return parse_plan(json_text)

    def validate_plan(
        self,
        payload: dict[str, Any],
        *,
        allow_internal: bool = False,
        scratch: Path | None = None,
    ) -> None:
        """Validate every step in *payload['plan']*; raises ValueError on failure.

        *allow_internal* must stay False for anything taken from the model or an
        outside caller — pass True only for an already-expanded sub-plan (see
        ``planner.validate_step`` and docs/audits/2026-09-07-core-principles-and-
        rtx5080.md, F1).
        """
        validate_plan(
            payload,
            self.registry,
            self.root,
            self.sandbox,
            allow_internal=allow_internal,
            scratch=scratch,
        )

    def execute_plan(
        self,
        payload: dict[str, Any],
        *,
        utterance: str | None = None,
        injected_files: set[str] | None = None,
        dry_run: bool = True,
        confirmed: bool = False,
        show_plan: bool | None = None,
        require_approval: bool | None = None,
        skip_execution: bool = False,
    ) -> list[dict[str, Any]]:
        """Validate, expand, and execute *payload* (see ``_execute_plan``).

        A stage that needs the run scratch before execution (URL downloads, which must land
        before preflight) enters it on *early*; closing the stack removes it on every exit.

        When the plan asked for an alert (``alert_when_done``), it fires once the run has
        ended, on success or failure (``knaif.alert``); never in a dry run, never when nothing
        ran, and not when the run is interrupted (Ctrl+C: the user is there).
        """
        from contextlib import ExitStack

        started = time.perf_counter()
        self.last_alert = False
        self.last_batch_report = None
        self._work_started = False
        self._intent_declined = False
        results: list[dict[str, Any]] = []
        error: Exception | None = None
        ended = False
        try:
            with ExitStack() as early:
                results = self._execute_plan(
                    payload,
                    utterance=utterance,
                    injected_files=injected_files,
                    dry_run=dry_run,
                    confirmed=confirmed,
                    show_plan=show_plan,
                    require_approval=require_approval,
                    skip_execution=skip_execution,
                    early=early,
                )
            ended = True
            return results
        except Exception as exc:
            error = exc
            ended = True
            raise
        finally:
            if ended and self.last_alert and not (dry_run or skip_execution):
                self._alert_at_end(results, error, utterance, time.perf_counter() - started)

    def _alert_at_end(
        self,
        results: list[dict[str, Any]],
        error: Exception | None,
        utterance: str | None,
        seconds: float,
    ) -> None:
        from .alert import alert_for, fire, run_outcome

        downloaded = any(not d.get("dry_run") for d in getattr(self, "last_downloads", []) or [])
        outcome = run_outcome(results, error, self._work_started or downloaded)
        if outcome is None:
            return
        if outcome == "done" and self._intent_declined:
            outcome = "stopped"
        reason = " ".join(str(error).split())[:80] if error is not None else None
        fire(alert_for(outcome, request=utterance, seconds=seconds, reason=reason))

    def _execute_plan(
        self,
        payload: dict[str, Any],
        *,
        utterance: str | None = None,
        injected_files: set[str] | None = None,
        dry_run: bool = True,
        confirmed: bool = False,
        show_plan: bool | None = None,
        require_approval: bool | None = None,
        skip_execution: bool = False,
        early: Any = None,
    ) -> list[dict[str, Any]]:
        """
        Validate, expand, and execute *payload*.

        Destructive tools (delete_files, move_files, intent-level ffmpeg ops)
        require either ``dry_run=True`` or ``confirmed=True``. Steps that pass
        their pre-validation are then handed to skill-provided expanders
        (if any) before execution; expanded plans are re-validated.

        Execution stops as soon as a ``wait_for_confirmation`` step returns a
        ``"declined"`` status, or a ``clarify`` / ``reject`` step runs.

        Optional hooks (off by default):
        - ``show_plan``: render the per-intent summary via ``self.plan_display``
          (or ``print`` as fallback) before each intent's sub-plan executes.
        - ``require_approval``: pause and ask ``self.plan_confirmer`` to approve
          each intent before its sub-plan executes. Declining any intent stops
          execution at that point; previously-executed intents are kept in the
          returned results. Implies ``show_plan``.
        - ``intent_completed``: callback ``(sub_results, *, dry_run)`` fired
          after each non-terminal intent's sub-plan finishes. Lets callers
          render per-intent output between intents.
        """
        # Match the normalization infer() applied before prompting, so the NL
        # clarify gate grounds plan paths against the text the model was shown.
        # The raw text is kept to give grounded values (a password) back the
        # spelling the user typed once the gate has passed.
        raw_utterance = utterance
        if utterance is not None:
            utterance = normalize_path_separators(utterance)

        normalize_plan(payload, self.registry)
        apply_defaults(payload, self.registry)
        # The alert is a run option: it leaves the plan here, before any gate or step sees it.
        payload, self.last_alert = split_alert(payload)
        # A glob in a folder the model made up for "here" reads as here, before the gates and
        # known-folder resolution see it (`videos/*.mov` would become the user's Videos folder).
        # Then "every video" written as `*.mp4` covers the other videos beside it too.
        if utterance is not None and isinstance(payload.get("plan"), list):
            from .batch_chain import drop_invented_folders, widen_kind_globs

            here = Path(self.sandbox if self.sandbox is not None else self.root)
            plan = drop_invented_folders(payload["plan"], utterance, here)
            plan = widen_kind_globs(
                plan, utterance, here, self.file_kinds, self.batch_kinds, self.route_kinds()[1]
            )
            payload = {**payload, "plan": plan}

        # Arg-shape clarify gates (before structural validation): when the model
        # omits a required arg the user must supply, or puts an arg on a known
        # tool that the tool cannot express, ask rather than hard-error. Both
        # fire only on plans that could never validate, so well-formed plans are
        # untouched. Needs an utterance (NL path); direct execute_plan calls in
        # tests with deliberately-partial args still hit normal validation.
        if utterance is not None:
            plan_steps = payload.get("plan", [])
            gated = required_args_clarify(plan_steps, self.registry) or unsupported_args_clarify(
                plan_steps, self.registry
            )
            if gated is not None:
                q = gated[0]["args"]["question"]
                return [
                    {
                        "tool": "clarify",
                        "args": {"question": q},
                        "result": {"status": "clarification_needed", "question": q},
                        "output": None,
                        "duration_ms": 0.0,
                    }
                ]

        self.validate_plan(payload)

        # "save it in Downloads": an output that names a known folder goes into the user's real
        # folder, resolved here and never by the model; a folder of that name next to the input
        # makes it a question instead (knaif.known_folders).
        from .known_folders import SameNameFolder, rewrite_outputs

        try:
            payload = {
                **payload,
                "plan": rewrite_outputs(
                    payload["plan"], self.registry, cwd=Path(self.root), sandbox=self.sandbox
                ),
            }
        except SameNameFolder as ask:
            question = str(ask)
            return [
                {
                    "tool": "clarify",
                    "args": {"question": question},
                    "result": {"status": "clarification_needed", "question": question},
                    "output": None,
                    "duration_ms": 0.0,
                }
            ]

        intent_plan = payload["plan"]
        # This run's scratch folder. The path is fixed now so the plan can name it; the folder
        # is created only when execution starts, and never in a dry run (knaif.scratch).
        scratch = RunScratch(dry_run=dry_run or skip_execution)

        # Stem resolution on intent-level args, before expanders run.
        # Expanders receive the intent args and forward them to internal steps,
        # so resolving here means every expanded step sees the full filename.
        # Terminal tools (clarify/reject/done) carry no file paths — skipped.
        # Open/CLI mode resolves too, against root (= cwd). Gating this on a sandbox left
        # `knaif run ffmpeg "compress clip_4k"` rendering `-i clip_4k` verbatim, and paired
        # with an empty guard listing it refused the file instead. Native never had the gap.
        resolution_dir = self._resolution_dir()
        clean_intents: list[dict[str, Any]] = []
        for intent_step in intent_plan:
            if intent_step.get("tool") in _TERMINAL_TOOLS:
                clean_intents.append(intent_step)
            else:
                try:
                    resolved_args = resolve_stems(intent_step.get("args", {}), resolution_dir)
                    clean_intents.append({**intent_step, "args": resolved_args})
                except (StemNotFoundError, StemAmbiguousError) as exc:
                    q = str(exc)
                    return [
                        {
                            "tool": "clarify",
                            "args": {"question": q},
                            "result": {"status": "clarification_needed", "question": q},
                            "output": None,
                            "duration_ms": 0.0,
                        }
                    ]
        intent_plan = clean_intents

        # NL clarify gate: check that every file-bearing input was concretely
        # named (or stem-referenced) in the utterance.  Runs after stem
        # resolution so guessed stems that resolved are still caught when the
        # stem doesn't appear in the utterance.  Skipped when no utterance is
        # available (e.g. direct execute_plan calls in tests).
        if utterance is not None:
            gated = nl_clarify_gate(
                utterance,
                intent_plan,
                injected_files=injected_files,
                registry=self.registry,
                known_files=self._listed_filenames(),
            )
            if gated is intent_plan:
                # A count of files ("2 of the videos") must match what the plan touches.
                here = Path(self.sandbox if self.sandbox is not None else self.root)
                asked = count_question(utterance, intent_plan, here, self.file_kinds)
                if asked is not None:
                    gated = [{"tool": "clarify", "args": {"question": asked}}]
            if gated is not intent_plan:
                # Gate fired — return the clarify step immediately.
                q = gated[0]["args"].get("question", "Which file did you mean?")
                return [
                    {
                        "tool": "clarify",
                        "args": {"question": q},
                        "result": {"status": "clarification_needed", "question": q},
                        "output": None,
                        "duration_ms": 0.0,
                    }
                ]
            # `raw_utterance` is set whenever `utterance` is (same branch above).
            intent_plan = restore_grounded_args(intent_plan, raw_utterance or "", self.registry)

        # URL inputs: downloaded into the run scratch (or, in a dry run, named there), so every
        # later stage sees a local file and never a URL. Before routing, collision naming and
        # preflight, which all reason about files.
        intent_plan, stopped = self._fetch_url_inputs(
            intent_plan, scratch, early, dry_run=dry_run or skip_execution, confirmed=confirmed
        )
        if stopped is not None:
            return stopped

        # A chain over many files ("convert all the .mov files to mp4 and make them smaller")
        # runs once per file (knaif.batch_chain). Before routing, so the keep rule sees each
        # file's chain as an ordinary one-file chain; a plan with no batch chain is unchanged.
        intent_plan, batch, expand = self._lift_batch_chain(intent_plan, resolution_dir)

        # One file into many (split_video, extract_frames, …): the skill says which files a step
        # will write before anything runs, and the steps reading them are pointed at that set,
        # so routing can move it and the lift below runs them once per file.
        sets: dict[int, PlannedSet] = {}
        if batch is None:
            sets = self._planned_sets(intent_plan, scratch.path, dry_run, confirmed, skip_execution)
            if sets:
                intent_plan = link_set_readers(intent_plan, sets)

        # Chain intermediates go to the run scratch, so only deliverables reach the user's
        # folder. Opt-in per skill while skills move onto scratch one at a time; skipped when
        # the user asked to keep every step's output (the flag, or words in the request).
        if (
            self.scratch_intermediates
            and not self.keep_intermediates
            and not wants_everything_kept(utterance)
        ):
            intent_plan = self._route_intermediates_to_scratch(
                intent_plan,
                scratch.path,
                utterance,
                resolution_dir,
                {name for name, td in self.registry.items() if td.joins_inputs},
                *self.route_kinds(),
            )

        # Give the skill a chance to rewrite outputs that would destroy a file, and to
        # rebind the steps that referred to them. Here rather than deeper because a
        # collision is a property of the whole plan plus what is on disk, and neither is
        # visible from inside a single step's handler. Default is a no-op
        # (`Skill.resolve_output_collisions`); the naming policy stays in the skill.
        if self.skill_instance is not None:
            rebound = self.skill_instance.resolve_output_collisions(
                intent_plan, sandbox=self.sandbox
            )
            if isinstance(rebound, list):
                intent_plan = rebound

        # Routing and the skill's collision naming rewrite args, never the number of steps;
        # should one ever change it, a failed file stops the run as before.
        if batch is not None and len(intent_plan) != len(batch.file_of):
            batch = None

        # The set's files as they will be written now (routing may have put them in the
        # scratch), then the readers run once per file.
        planned: dict[str, list[str]] = {}
        planned_steps: dict[int, list[str]] = {}  # by producer; the lift keeps its place
        if sets:
            planned_now = self._planned_sets(
                intent_plan, scratch.path, dry_run, confirmed, skip_execution
            )
            for i, ps in planned_now.items():
                out = (intent_plan[i].get("args") or {}).get("output")
                planned[out if isinstance(out, str) and out else ps.name] = list(ps.files)
                planned_steps[i] = list(ps.files)
            intent_plan, batch = self._lift_sets(intent_plan, planned, scratch.path)

        # What the plan will leave (knaif.result_set); every preview ends with it.
        self.last_result_set = result_set(
            intent_plan,
            scratch=scratch.path,
            base=resolution_dir,
            expand=expand,
            readonly={name for name, td in self.registry.items() if td.readonly},
            joining={name for name, td in self.registry.items() if td.joins_inputs},
            produces=self.route_kinds()[1],
            file_kinds=self.file_kinds,
            sets=planned_steps,
        )
        leaves = leaves_line(self.last_result_set)

        # Expand each intent independently, preserving intent boundaries.
        intent_blocks: list[tuple[dict[str, Any], list[dict[str, Any]]]] = [
            (intent_step, self._expand_intent(intent_step)) for intent_step in intent_plan
        ]

        # Re-validate the union of expanded steps so structural errors surface
        # before any user-facing prompt. allow_internal=True: these steps came
        # from Intent.expand() (trusted, deterministic), not from the model.
        any_expanded = any(block != [orig] for orig, block in intent_blocks)
        if any_expanded:
            self.validate_plan(
                {"plan": [s for _, b in intent_blocks for s in b]},
                allow_internal=True,
                scratch=scratch.path,
            )

        # Optimize each intent's sub-plan independently. Optimizing across
        # intent boundaries strips readonly summary steps (e.g. generate_report)
        # whose outputs aren't referenced by later intents — keeping the
        # optimization local preserves each intent's full sub-plan.
        intent_blocks = [(orig, optimize_plan(b, self.registry)) for orig, b in intent_blocks]

        # Pre-flight: run skill-registered checks before any prompt or execution.
        # File existence, path validity, etc. are domain-specific and cannot be
        # caught by structural validate_plan. Skipped in dry_run because dry_run
        # is for testing the pipeline without real files.
        if (self.preflights or self.tool_map) and not dry_run:
            from .tool import Handler as _Step
            from .tool import Intent as _Intent

            # Files that earlier intents will produce — don't fail preflight for them.
            planned_output_names: set[str] = set()
            preflight_errors: list[str] = []
            for k, (orig_intent, block) in enumerate(intent_blocks):
                for step in block:
                    tool = step.get("tool", "")
                    if tool in _TERMINAL_TOOLS:
                        continue
                    step_args = step.get("args") or {}
                    kw: dict[str, Any] = {
                        "root": self.root,
                        "sandbox": self.sandbox,
                        "planned_output_names": planned_output_names,
                        "work_dir": scratch.path,
                    }
                    try:
                        tool_obj = self.tool_map.get(tool) if self.tool_map else None
                        if tool_obj is not None:
                            # OOP path: prefer tool-level preflight if overridden
                            base_pf = (
                                _Step.preflight
                                if isinstance(tool_obj, _Step)
                                else _Intent.preflight
                            )
                            if type(tool_obj).preflight is not base_pf:
                                errs = tool_obj.preflight(step_args, **kw)
                            elif self.skill_instance is not None:
                                errs = self.skill_instance.preflight(tool, step_args, **kw)
                            else:
                                errs = []
                            # Legacy preflights dict is consulted as a secondary layer even
                            # for OOP tools, so post-hoc injection (e.g. in tests) still works.
                            if self.preflights:
                                fn = self.preflights.get(tool) or self.preflights.get("*")
                                if fn is not None:
                                    errs = list(errs) + list(fn(step_args, **kw) or [])
                        else:
                            fn = self.preflights.get(tool) or self.preflights.get("*")
                            if fn is None:
                                continue
                            errs = fn(step_args, **kw)
                        preflight_errors.extend(errs or [])
                    except Exception as exc:  # noqa: BLE001
                        preflight_errors.append(str(exc))
                # After checking this intent, register its output so later intents can use it.
                output_arg = orig_intent.get("args", {}).get("output")
                if isinstance(output_arg, str) and not output_arg.startswith("$"):
                    planned_output_names.add(Path(output_arg).name)
                # A step writing many files from one: every file it will write (knaif.batch_chain).
                planned_output_names.update(Path(f).name for f in planned_steps.get(k, []))
            if preflight_errors:
                kind = classify_preflight_errors(preflight_errors)
                combined = "; ".join(preflight_errors)
                if kind == "reject":
                    term_args: dict[str, Any] = {"reason": combined}
                    term_result: dict[str, Any] = {"status": "rejected", "reason": combined}
                else:
                    term_args = {"question": combined}
                    term_result = {"status": "clarification_needed", "question": combined}
                return [
                    {
                        "tool": kind,
                        "args": term_args,
                        "result": term_result,
                        "output": None,
                        "duration_ms": 0.0,
                    }
                ]

        _show = show_plan if show_plan is not None else self.show_plan
        _approve = require_approval if require_approval is not None else self.require_approval
        if _approve:
            _show = True  # cannot approve what you cannot see

        # Terminal-only plans (reject/clarify/done) carry no actionable steps —
        # skip display and approval so the user isn't prompted to approve a refusal.
        all_terminal = all(s.get("tool") in _TERMINAL_TOOLS for _, b in intent_blocks for s in b)
        if all_terminal:
            _show = False
            _approve = False

        # Summarise the original intent plan (summarizers are keyed to intent
        # tool names, not expanded step names). Each non-terminal intent gets
        # its own display call and, when approval is required, its own prompt.
        step_summaries: list[str] = []
        if _show or _approve:
            step_summaries = summarize_plan_steps(
                intent_plan, self.summarizers, skill_dir=self.skill_dir
            )
        self.last_plan_summary = (
            "Will " + ", then ".join(step_summaries) + "." if step_summaries else ""
        )

        # Each non-terminal intent's clause, by its place in the plan. A batch previews its
        # first file's clauses, then "the same for N more files", then what the plan leaves,
        # and is approved once; any other plan ends its clauses with what it leaves (owner,
        # 2026-10-20).
        clause_of: dict[int, str] = {}
        clauses = iter(step_summaries)
        for k, (orig, _) in enumerate(intent_blocks):
            if orig.get("tool") not in _TERMINAL_TOOLS:
                clause_of[k] = next(clauses, "")
        last_clause = max(clause_of, default=-1)
        batch_shown = False

        # A plan with a question or a refusal in it is no plan to show (native skips it too).
        if self.plan_ready is not None and not any(
            s.get("tool") in _TERMINAL_TOOLS for s in intent_plan
        ):
            notes: list[str] = []
            preview = list(intent_plan)
            if batch is not None:
                preview = [
                    s for s, f in zip(intent_plan, batch.file_of, strict=False) if f in (None, 0)
                ]
                if len(batch.shown) > 1:
                    notes.append(same_for_line(batch.shown[1:]))
            if leaves:
                notes.append(leaves)
            self.plan_ready(preview, notes)

        def show(line: str) -> None:
            if not _show:
                return
            if self.plan_display is not None:
                self.plan_display(line)
            else:
                print(line)

        def approved(line: str) -> bool:
            if not _approve:
                return True
            return bool(self.plan_confirmer(line)) if self.plan_confirmer else confirmed

        context: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        # A batch chain (knaif.batch_chain): a file whose step fails is out, with its reason,
        # and its later steps are skipped; the other files go on (owner, 2026-10-13).
        failed: dict[int, str] = {}
        batch_started = time.perf_counter()
        # A step over many files without a lift reports each file itself (ctx.file_finished).
        self._file_tally = (
            _FileTally(self.batch_failed, self._resolution_dir()) if batch is None else None
        )

        # One scratch folder for the whole run, every intent included: a chain intermediate
        # written by one intent is read by the next. Removed on any exit (knaif.scratch).
        with scratch:
            # Every step is checked before any runs (owner, 2026-10-24): a file of a batch
            # that would fail is out before the others run, a chain that would fail runs nothing.
            if not dry_run and not skip_execution:
                for out, reason in self._rehearse(
                    intent_blocks, batch, scratch.path, confirmed
                ).items():
                    failed[out] = reason
                    if self.batch_failed is not None and batch is not None:
                        self.batch_failed(failure_line(batch.shown[out], reason))
            for k, (orig_intent, sub_plan) in enumerate(intent_blocks):
                is_terminal = orig_intent.get("tool") in _TERMINAL_TOOLS
                file_idx = batch.file_of[k] if batch is not None else None
                skip = file_idx is not None and file_idx in failed
                if batch is not None and k in batch.join_files and failed and not skip:
                    joined = self._join_without_failed(
                        orig_intent, batch.join_files[k], failed, scratch.path
                    )
                    if joined is None:
                        skip = True
                    else:
                        orig_intent, sub_plan = joined
                if skip:
                    continue
                if self._file_tally is not None:
                    self._file_tally.start_intent()
                intent_tool_name = orig_intent.get("tool")
                orig_tool_def = (
                    self.registry.get(intent_tool_name)
                    if isinstance(intent_tool_name, str)
                    else None
                )
                # The destructive check below must key off the *originating intent's*
                # safety_category, not just each expanded leaf's — a destructive intent
                # (e.g. strip_audio) can expand entirely into `safe` leaves (its internal
                # run/verify steps), and that must not erase the requirement to confirm.
                # See docs/audits/2026-09-07-core-principles-and-rtx5080.md, F2.
                intent_destructive = bool(
                    orig_tool_def and orig_tool_def.safety_category == "destructive"
                )

                if batch is not None and file_idx is not None:
                    if not batch_shown and (_show or _approve):
                        batch_shown = True
                        lines = [
                            clause_of[j]
                            for j, f in enumerate(batch.file_of)
                            if f == 0 and j in clause_of
                        ]
                        if len(batch.shown) > 1:
                            lines.append(same_for_line(batch.shown[1:]))
                        if leaves:
                            lines.append(leaves)
                        for line in lines:
                            show(line)
                        if not approved(lines[-1]):
                            self._intent_declined = True
                            break  # decline the batch → nothing of it runs
                elif not is_terminal and (_show or _approve):
                    clause = clause_of.get(k, "")
                    show(clause)
                    if k == last_clause and leaves and not batch_shown:
                        show(leaves)
                        clause = leaves
                    if not approved(clause):
                        self._intent_declined = True
                        break  # decline this intent → stop, keep prior results

                if self.intent_started is not None and not is_terminal:
                    self.intent_started(k + 1, len(intent_blocks), orig_intent)
                self._isolated_failure = None
                try:
                    sub_results, should_stop = self._execute_steps(
                        sub_plan,
                        context,
                        dry_run,
                        confirmed,
                        skip_execution,
                        intent_tool=intent_tool_name,
                        intent_destructive=intent_destructive,
                        work_dir=scratch.path,
                        isolate=file_idx is not None,
                    )
                except Exception as exc:
                    # Which step it stopped at, for the CLI's error; the error itself is
                    # unchanged for every other caller.
                    if not hasattr(exc, STEP_ATTR):
                        setattr(exc, STEP_ATTR, (k, len(intent_blocks)))
                    raise
                results.extend(sub_results)

                if (
                    batch is not None
                    and file_idx is not None
                    and should_stop
                    and sub_results
                    and _step_failed(sub_results[-1].get("result"))
                ):
                    reason = first_line(
                        _without_folder(
                            display(
                                self._isolated_failure or _failure_reason(sub_results[-1]["result"])
                            ),
                            self._resolution_dir(),
                        )
                    )
                    failed[file_idx] = reason
                    if self.batch_failed is not None:
                        self.batch_failed(failure_line(batch.shown[file_idx], reason))
                    continue

                # Fire the per-intent callback so callers can render output between
                # intents. Terminal steps (reject/clarify/done) are reported via the
                # normal results list but skip the callback.
                if not is_terminal and self.intent_completed is not None and sub_results:
                    try:
                        self.intent_completed(sub_results, dry_run=dry_run)
                    except Exception:  # noqa: BLE001
                        pass

                if should_stop:
                    break

        tally, self._file_tally = self._file_tally, None
        if batch is not None:
            self.last_batch_report = BatchReport(
                files=list(batch.shown),
                failures=[(batch.shown[f], why) for f, why in failed.items()],
                seconds=time.perf_counter() - batch_started,
                size_bytes=_size_left(results, self._resolution_dir(), scratch.path),
            )
        elif tally is not None and tally.entries:
            self.last_batch_report = BatchReport(
                files=[name for name, _ in tally.entries],
                failures=[(name, why) for name, why in tally.entries if why is not None],
                seconds=time.perf_counter() - batch_started,
                size_bytes=_size_left(results, self._resolution_dir(), scratch.path),
            )
        return results

    def _note_isolated_failure(self, file: str, failure: str | None) -> None:
        """``ctx.file_finished`` inside one file's step of a lift: keep the step's reason."""
        if failure is not None and self._isolated_failure is None:
            self._isolated_failure = failure

    def _expand_intent(self, intent_step: dict[str, Any]) -> list[dict[str, Any]]:
        """The steps *intent_step* runs as: its ``Intent.expand()`` (or legacy expander), else
        itself. An intent's ``output`` var lands on its last step."""
        tool = intent_step.get("tool")
        sub_plan: list[dict[str, Any]]

        # OOP path: dispatch expansion via Intent.expand()
        tool_obj = self.tool_map.get(tool) if (self.tool_map and tool) else None
        if tool_obj is not None:
            from .tool import Intent as _Intent

            if not isinstance(tool_obj, _Intent):
                return [intent_step]
            sub_plan = tool_obj.expand(intent_step.get("args", {}))
        else:
            expander = self.expanders.get(tool) if tool else None
            if expander is None:
                return [intent_step]
            sub_plan = expander(intent_step.get("args", {}))
        if not isinstance(sub_plan, list):
            raise ValueError(f"Expander for {tool!r} must return a list of steps.")
        output_var = intent_step.get("output")
        if output_var and sub_plan:
            last = dict(sub_plan[-1])
            last["output"] = output_var
            sub_plan = list(sub_plan[:-1]) + [last]
        return sub_plan

    def _rehearse(
        self,
        intent_blocks: list[tuple[dict[str, Any], list[dict[str, Any]]]],
        batch: Any,
        work_dir: Path,
        confirmed: bool,
    ) -> dict[int, str]:
        """Rehearse the plan as a dry run before it really runs (owner, 2026-10-24).

        Returns the files of a batch that would fail, each with its reason. Raises
        ``PlanWouldFail`` when a step of a plan without a batch would fail, or every file of a
        batch would. A refused step (a destructive one unconfirmed, an arg that fails
        validation) raises as it does when it runs. One step on one file is not rehearsed:
        nothing can run before it fails.
        """
        runnable = [
            k for k, (o, _) in enumerate(intent_blocks) if o.get("tool") not in _TERMINAL_TOOLS
        ]
        if batch is None and len(runnable) < 2:
            return {}
        saved = (self._file_tally, self._isolated_failure, self._work_started)
        self._file_tally = None
        failed: dict[int, str] = {}
        context: dict[str, Any] = {}
        try:
            for k, (orig_intent, sub_plan) in enumerate(intent_blocks):
                if orig_intent.get("tool") in _TERMINAL_TOOLS:
                    continue
                file_idx = batch.file_of[k] if batch is not None else None
                if file_idx is not None and file_idx in failed:
                    continue
                self._isolated_failure = None
                # Isolated: a handler's own error is a failed result, so what escapes is a refusal.
                sub_results, _ = self._execute_steps(
                    sub_plan,
                    context,
                    True,
                    confirmed,
                    False,
                    intent_tool=orig_intent.get("tool"),
                    intent_destructive=bool(
                        (tool_def := self.registry.get(str(orig_intent.get("tool"))))
                        and tool_def.safety_category == "destructive"
                    ),
                    work_dir=work_dir,
                    isolate=True,
                    rehearsal=True,
                )
                if not sub_results or not _step_failed(sub_results[-1].get("result")):
                    continue
                cause = display(
                    self._isolated_failure or _failure_reason(sub_results[-1]["result"])
                )
                if file_idx is None:
                    raise PlanWouldFail(rehearsal_failure_context(k, len(intent_blocks)), cause)
                failed[file_idx] = first_line(_without_folder(cause, self._resolution_dir()))
        finally:
            self._file_tally, self._isolated_failure, self._work_started = saved
        if batch is not None and failed and len(failed) == len(batch.shown):
            raise PlanWouldFail(EVERY_FILE_WOULD_FAIL, next(iter(failed.values())))
        return failed

    def _execute_steps(
        self,
        sub_plan: list[dict[str, Any]],
        context: dict[str, Any],
        dry_run: bool,
        confirmed: bool,
        skip_execution: bool,
        *,
        intent_tool: str | None = None,
        intent_destructive: bool = False,
        work_dir: Path | None = None,
        isolate: bool = False,
        rehearsal: bool = False,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Execute *sub_plan* sequentially against the shared *context*.

        *rehearsal* (with *dry_run*): the check before a real run (``_rehearse``). Handlers run
        as a dry run, but a destructive step still needs *confirmed*, as it will when it runs.

        *intent_tool* / *intent_destructive* carry the originating intent's identity
        and safety_category through expansion, so a destructive intent that expands
        into safe leaves still requires confirmation (F2).

        *isolate* (one file's step in a batch chain): a handler that raises becomes a failed
        step (``{"status": "failed", "error": ...}``) instead of ending the run, so the batch
        goes on with the other files. Only the handler's own error: a refused step (a
        destructive one unconfirmed, an arg that fails validation) still raises.

        Returns ``(results, should_stop)`` where ``should_stop`` is True if a
        terminal/declined step was encountered, signalling that no further
        intents should be processed.
        """
        results: list[dict[str, Any]] = []
        should_stop = False

        for step in sub_plan:
            tool: str = step["tool"]
            args: dict[str, Any] = resolve_args(step["args"], context)

            # Re-validate path args against sandbox after resolution.
            if self.sandbox is not None:
                for path_key in ("path", "src", "dst"):
                    if path_key in args and isinstance(args[path_key], str):
                        _resolve_path(args[path_key], self.root, self.sandbox, work_dir)
            if "file_type" in args and isinstance(args["file_type"], str):
                valid_types = self.arg_value_sets.get("file_type") or _VALID_FILE_TYPES
                if args["file_type"] not in valid_types:
                    raise ValueError(f"Unknown file_type after resolution: {args['file_type']!r}")

            # Post-resolution ArgSchema validation: re-check any schema'd arg now
            # that $var references have been substituted with their actual values.
            _tool_def = self.registry.get(tool)
            if _tool_def is not None and _tool_def.arg_schemas:
                for _arg_name, _value in args.items():
                    _schema = _tool_def.arg_schemas.get(_arg_name)
                    if _schema is not None:
                        try:
                            validate_arg_by_schema(_arg_name, _value, _schema)
                        except ValueError as exc:
                            raise ValueError(
                                f"Tool '{tool}' post-resolution arg validation: {exc}"
                            ) from exc

            exec_tool_obj = self.tool_map.get(tool) if self.tool_map else None
            if exec_tool_obj is None and tool not in self.handlers:
                raise ValueError(f"No handler registered for tool: {tool!r}")

            tool_def = self.registry.get(tool)
            step_destructive = bool(tool_def and tool_def.safety_category == "destructive")
            # A destructive intent's inherited requirement (see intent_destructive above) must
            # not block its own terminal clarify/reject: a destructive intent can deterministically
            # decide it cannot proceed (e.g. prepare_for_platform given an unknown platform) and
            # expand to a clarify leaf instead of a real side effect. clarify/reject perform no
            # action and always `should_stop` the loop immediately below, so nothing destructive
            # can follow one in the same sub_plan regardless of step order — exempting them here
            # cannot reopen the hole this check exists for.
            if (
                tool not in _TERMINAL_TOOLS
                and (intent_destructive or step_destructive)
                and (not dry_run or rehearsal)
                and not confirmed
            ):
                if intent_destructive and not step_destructive:
                    raise ValueError(
                        f"{intent_tool!r} is a destructive intent; requires "
                        f"confirmed=True when dry_run=False (blocked before "
                        f"executing {tool!r})."
                    )
                raise ValueError(f"{tool!r} requires confirmed=True when dry_run=False.")

            # One file's step of a lift: the lift reports the file, with the reason the step
            # gave (`_isolated_failure`); otherwise a step over many files reports each one.
            tally = None if isolate else self._file_tally
            reporter: Callable[[str, str | None], None] | None
            if isolate:
                reporter = self._note_isolated_failure
            else:
                reporter = tally.record if tally is not None else None
            ctx = self._handler_context(
                dry_run,
                confirmed,
                skip_execution,
                work_dir,
                tool=tool,
                file_reporter=reporter,
            )
            if tool not in _TERMINAL_TOOLS and tool != "wait_for_confirmation":
                self._work_started = True
            _t0 = time.perf_counter()
            try:
                if exec_tool_obj is not None:
                    result = exec_tool_obj.handle(args, ctx)
                else:
                    result = self.handlers[tool](args, ctx)
            except Exception as exc:
                if not isolate:
                    raise
                result = {"status": "failed", "error": str(exc)}
            _duration_ms = (time.perf_counter() - _t0) * 1000

            output_var = step.get("output")
            if output_var:
                context[output_var.lstrip("$")] = result

            results.append(
                {
                    "tool": tool,
                    "args": args,
                    "result": result,
                    "output": step.get("output"),
                    "duration_ms": _duration_ms,
                }
            )

            # In a step over many files, a failed command with other files done is the
            # batch's business (reported and counted), not the end of the run.
            if _step_failed(result) and not (tally is not None and tally.partial()):
                should_stop = True
                break

            if (
                tool == "wait_for_confirmation"
                and isinstance(result, dict)
                and result.get("status") == "declined"
            ):
                should_stop = True
                break
            if tool in ("clarify", "reject"):
                should_stop = True
                break

        return results, should_stop

    def _fetch_url_inputs(
        self,
        intent_plan: list[dict[str, Any]],
        scratch: RunScratch,
        early: Any,
        *,
        dry_run: bool,
        confirmed: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Replace every URL input with a file in the run scratch: ``(plan, stopped)``.

        Each distinct URL is approved once (``allow_download``, else ``download_confirmer``),
        then fetched by the net toolkit's ``download_file`` under the skill's ``url_inputs:``.
        A dry run requests nothing and names the path the download would take. The file sits
        at the top of the scratch, so ``user_side_path`` places outputs derived from it in the
        user's folder. *stopped* is the run's result when it ends here: a skill without URL
        inputs, approval missing or declined.
        """
        from .planner import _CONTROL_TOOLS, _URL_INPUT_KEYS
        from .urls import is_url, url_file_name

        self.last_downloads = []
        urls: list[str] = []
        for step in intent_plan:
            if step.get("tool") in _CONTROL_TOOLS:
                continue
            tool_def = self.registry.get(step.get("tool", ""))
            schemas = getattr(tool_def, "arg_schemas", {}) or {}
            for key, value in (step.get("args") or {}).items():
                if key not in _URL_INPUT_KEYS and getattr(schemas.get(key), "path_role", None) != (
                    "input"
                ):
                    continue
                for item in value if isinstance(value, list) else [value]:
                    if is_url(item) and item not in urls:
                        urls.append(item)
        if not urls:
            return intent_plan, None

        def stop(tool: str, result: dict[str, Any], args: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {"tool": tool, "args": args, "result": result, "output": None, "duration_ms": 0.0}
            ]

        downloader = (self.tool_map or {}).get("download_file")
        if self.url_inputs is None or downloader is None:
            q = (
                f"This skill works on local files and cannot download {urls[0]}. "
                "Download it first, then name the file."
            )
            return intent_plan, stop(
                "clarify", {"status": "clarification_needed", "question": q}, {"question": q}
            )

        cap = int(self.url_inputs.max_bytes)
        local: dict[str, str] = {}
        if dry_run and not self.fetch_in_preview:
            taken: set[str] = set()
            for url in urls:
                name = url_file_name(url)
                stem, dot, ext = name.partition(".")
                n = 0
                while name.lower() in taken:
                    n += 1
                    name = f"{stem}-{n}{dot}{ext}"
                taken.add(name.lower())
                local[url] = str(scratch.path / name)
                self.last_downloads.append({"url": url, "path": local[url], "dry_run": True})
        else:
            for url in urls:
                if self.allow_download:
                    continue
                if self.download_confirmer is None:
                    q = (
                        f"Downloading {url} needs your approval: run this in a terminal to be "
                        "asked, or re-run with --allow download."
                    )
                    return intent_plan, stop(
                        "clarify",
                        {"status": "clarification_needed", "question": q},
                        {"question": q},
                    )
                if not self.download_confirmer(url, cap):
                    prompt = f"Download {url}?"
                    return intent_plan, stop(
                        "wait_for_confirmation",
                        {"status": "declined", "prompt": prompt},
                        {"prompt": prompt},
                    )
            if dry_run:
                # The executing eval's preview (`fetch_in_preview`): a dry-run scratch is never
                # created or removed by RunScratch, so the folder the download makes is
                # removed here when the run ends.
                import shutil

                early.callback(shutil.rmtree, scratch.path, ignore_errors=True)
            else:
                early.enter_context(scratch)
            for url in urls:
                ctx = self._handler_context(
                    False, confirmed, False, scratch.path, tool="download_file"
                )
                got = downloader.handle(
                    {
                        "url": url,
                        "dest_dir": str(scratch.path),
                        "accepted": list(self.url_inputs.accepted),
                        "max_bytes": cap,
                    },
                    ctx,
                )
                local[url] = got["path"]
                entry = {k: got.get(k) for k in ("url", "path", "bytes", "content_type")}
                if dry_run:
                    entry["dry_run"] = True  # the replay fetches it again into its own scratch
                self.last_downloads.append(entry)

        def swap(value: Any) -> Any:
            if isinstance(value, list):
                return [local.get(v, v) if isinstance(v, str) else v for v in value]
            return local.get(value, value) if isinstance(value, str) else value

        rewritten = [
            (
                {**s, "args": {k: swap(v) for k, v in (s.get("args") or {}).items()}}
                if s.get("tool") not in _CONTROL_TOOLS
                else s
            )
            for s in intent_plan
        ]
        return rewritten, None

    def _handler_context(
        self,
        dry_run: bool,
        confirmed: bool,
        skip_execution: bool,
        work_dir: Path | None,
        *,
        tool: str | None = None,
        file_reporter: Callable[[str, str | None], None] | None = None,
    ) -> HandlerContext:
        """The context one step runs with; ``ctx.call_tool`` dispatches within the same run.

        It carries the effects *tool* declares (``knaif.effects``): ``ctx.exec`` and
        ``ctx.call_tool`` refuse anything outside them.
        """
        tool_def = self.registry.get(tool) if tool else None
        declared = getattr(tool_def, "effects", None)
        holder: list[HandlerContext] = []
        ctx = HandlerContext(
            root=self.root,
            dry_run=dry_run,
            confirmed=confirmed,
            skill_dir=self.skill_dir,
            sandbox=self.sandbox,
            confirmer=self.confirmer,
            skip_execution=skip_execution,
            work_dir=work_dir,
            tool_caller=lambda name, args: self._call_tool(name, args, holder[0]),
            tool=tool,
            to_stdout=self.to_stdout,
            effects=frozenset(declared) if declared is not None else None,
            file_reporter=file_reporter,
            item_shower=self.item_shown,
        )
        holder.append(ctx)
        return ctx

    def _call_tool(self, name: str, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        """Run the Handler *name* for a handler that reuses it (``ctx.call_tool``).

        Only a tool this skill has: its own, a declared toolkit's, or core. An Intent cannot be
        called this way, since expanding is the planner's job, not a handler's.
        """
        from .tool import Handler as _Handler

        tool_obj = self.tool_map.get(name) if self.tool_map else None
        if tool_obj is None or name not in self.registry:
            raise ValueError(f"call_tool: {name!r} is not a tool of this skill")
        if not isinstance(tool_obj, _Handler):
            raise ValueError(f"call_tool: {name!r} is an Intent; only a Handler can be called")
        # A step may reuse a tool only for what it declares itself: writing through the fs
        # toolkit needs `fs.write` (`knaif.effects`).
        from .effects import require

        callee = getattr(self.registry[name], "effects", None)
        require(ctx.tool, ctx.effects, set(callee or ()), f"call {name}")
        return tool_obj.handle(args, ctx)

    def _expand_plan(self, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace each expandable step with its skill-provided workflow.

        When an intent step declares ``output: "$var"``, that declaration is
        propagated to the last step of the expanded sub-plan so the variable is
        populated with the workflow result and downstream steps can reference it.
        """
        if not self.expanders:
            return plan
        expanded: list[dict[str, Any]] = []
        changed = False
        for step in plan:
            tool = step.get("tool")
            expander = self.expanders.get(tool) if tool else None
            if expander is None:
                expanded.append(step)
                continue
            sub_plan = expander(step.get("args", {}))
            if not isinstance(sub_plan, list):
                raise ValueError(f"Expander for {tool!r} must return a list of steps.")
            output_var = step.get("output")
            if output_var and sub_plan:
                last = dict(sub_plan[-1])
                last["output"] = output_var
                sub_plan = list(sub_plan[:-1]) + [last]
            expanded.extend(sub_plan)
            changed = True
        return expanded if changed else plan

    # ── inference ─────────────────────────────────────────────────────────────

    def build_prompt(
        self,
        user_utterance: str,
        *,
        history: list[dict] | None = None,
        registry_override: dict[str, ToolDef] | None = None,
    ) -> tuple[str, str]:
        """Return *(system_message, user_message)* for chat completion."""
        registry = registry_override if registry_override is not None else self.registry

        # When structured examples are available and a retrieved registry subset is
        # supplied, filter examples to only those relevant to the retrieved tools.
        # Falls back to the full examples_block for callers that don't pass
        # registry_override (e.g. tests, history-based re-planning).
        examples_block = self.examples_block
        if self.prompt_examples and registry_override is not None:
            from .prompt import render_examples_block, select_examples

            retrieved_tool_names = frozenset(
                name for name, td in registry_override.items() if not td.internal
            )
            triggered = frozenset(n for n, td in self.registry.items() if td.triggers)
            filtered = select_examples(
                self.prompt_examples,
                retrieved_tool_names,
                user_utterance,
                triggered=triggered | {"alert_when_done"},
            )
            if filtered:
                examples_block = render_examples_block(filtered)

        # A rule about tools joins the header only when one of them is in this prompt; a
        # `when: many` rule only when the request asks for many files (the clarify gate's test).
        header = self.system_header
        if header is not None and self.prompt_rules:
            from .prompt import apply_prompt_rules

            present = {name for name, td in registry.items() if not td.internal}
            header = apply_prompt_rules(header, self.prompt_rules, present, user_utterance)

        return build_prompt(
            user_utterance,
            registry,
            history=history or [],
            system_header=header,
            examples_block=examples_block,
        )

    def _extract_json(self, text: str) -> str:
        """Extract the first complete JSON object from raw model output."""
        # Full <think>...</think> block — strip it and capture as thinking.
        think_match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        self.last_thinking = think_match.group(1).strip() if think_match else ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # Partial thinking: some Ollama builds suppress <think> but keep </think>.
        # Everything before </think> is reasoning text; take only what follows it.
        close_idx = text.find("</think>")
        if close_idx != -1:
            if not self.last_thinking:
                self.last_thinking = text[:close_idx].strip()
            text = text[close_idx + len("</think>") :].strip()

        return self._clean_json(text)

    @staticmethod
    def _clean_json(text: str) -> str:
        """Strip markdown fences and extract the first JSON object from *text*."""
        text = re.sub(r"```(?:json)?", "", text).strip()

        start = text.find("{")
        if start == -1:
            return text  # No JSON found; parse_plan will raise a clear error.

        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]

        return text[start:]  # Incomplete; parse_plan will surface the error.

    def _resolve_mock_args(self, mock_args: dict[str, Any], utterance: str) -> dict[str, Any]:
        """Substitute {sandbox} and {utterance_after:<word>} in mock_args values.

        Substitutions apply to plain string values *and* to string elements
        inside list values, so tools with list args (e.g. ``inputs``) work too.
        """
        tokens = utterance.lower().split()

        def _sub(val: str) -> str:
            def _replace_after(m: re.Match[str]) -> str:
                trigger = m.group(1)
                try:
                    idx = tokens.index(trigger)
                    return tokens[idx + 1] if idx + 1 < len(tokens) else trigger
                except ValueError:
                    return trigger

            val = re.sub(r"\{utterance_after:(\w+)\}", _replace_after, val)
            sandbox_str = str(self.sandbox) if self.sandbox is not None else str(self.root)
            return val.replace("{sandbox}", sandbox_str)

        resolved: dict[str, Any] = {}
        for key, val in mock_args.items():
            if isinstance(val, str):
                resolved[key] = _sub(val)
            elif isinstance(val, list):
                resolved[key] = [_sub(v) if isinstance(v, str) else v for v in val]
            else:
                resolved[key] = val
        return resolved

    def _mock_response(self, user_utterance: str, history: list[dict] | None = None) -> str:
        """Return a mock JSON response driven by registry keywords and mock_args.

        ``$KNAIF_LLM_MOCK_RESPONSE``, when set, is the model's output verbatim, as native's mock
        backend takes it: one harness then drives both CLIs on the same plan."""
        canned = os.environ.get("KNAIF_LLM_MOCK_RESPONSE")
        if canned is not None:
            return canned
        u = user_utterance.lower()

        if any(phrase in u for phrase in self.unsafe_phrases):
            args = self._resolve_mock_args(self.registry["reject"].mock_args, user_utterance)
            return json.dumps({"plan": [{"tool": "reject", "args": args}]})

        scored = retrieve_tools(user_utterance, self.registry, min_score=3)

        if history:
            last_tool = history[-1]["step"]["tool"]
            if last_tool in _TERMINAL_TOOLS:
                return json.dumps({"plan": [{"tool": "done", "args": {}}]})
            executed = {entry["step"]["tool"] for entry in history}
            for name in scored:
                if name not in executed and name not in _TERMINAL_TOOLS:
                    args = self._resolve_mock_args(self.registry[name].mock_args, user_utterance)
                    return json.dumps({"plan": [{"tool": name, "args": args}]})
            return json.dumps({"plan": [{"tool": "done", "args": {}}]})

        for name in scored:
            if name not in _TERMINAL_TOOLS:
                args = self._resolve_mock_args(self.registry[name].mock_args, user_utterance)
                return json.dumps({"plan": [{"tool": name, "args": args}]})

        args = self._resolve_mock_args(self.registry["clarify"].mock_args, user_utterance)
        return json.dumps({"plan": [{"tool": "clarify", "args": args}]})

    def infer(
        self,
        user_utterance: str,
        *,
        use_mock: bool = True,
        ollama_model: str = "mistral",
        max_tokens: int = 256,
        history: list[dict] | None = None,
        registry_override: dict[str, ToolDef] | None = None,
    ) -> dict[str, Any]:
        """
        Full pipeline: build prompt → call model → extract JSON → parse.

        Returns the plan payload dict. On parse failure, returns a clarify plan.
        """
        self.last_parse_error = None
        self.last_validation_error = None
        self.last_retried = False
        self.last_model_plan = None
        # Normalize here, not just in build_prompt: the same text grounds the
        # hallucinated-filename guard and chain-intermediate linking below, and
        # those must compare against the paths the model was actually shown.
        user_utterance = normalize_path_separators(user_utterance)
        if is_unsafe_request(user_utterance, self.unsafe_phrases):
            reject_args = self._resolve_mock_args(self.registry["reject"].mock_args, user_utterance)
            return {"plan": [{"tool": "reject", "args": reject_args}]}

        system_msg, user_msg = self.build_prompt(
            user_utterance, history=history, registry_override=registry_override
        )

        if use_mock:
            raw_output = self._mock_response(user_utterance, history=history)
            payload, kind, err = self._parse_and_check(raw_output)
        else:
            if self.orchestrator is None:
                raise RuntimeError(
                    "No orchestrator configured. Pass an InferenceOrchestrator to CommandAgent()."
                )
            raw_output = self._extract_json(
                self.orchestrator.infer(
                    system_msg, user_msg, model_name=ollama_model, max_tokens=max_tokens
                )
            )
            payload, kind, err = self._parse_and_check(raw_output)

            # Validator-feedback retry: on a parse or structural-validation
            # failure, re-prompt the model once with the concrete error injected.
            # The error strings already exist (parse_plan / validate_plan), so
            # feeding them back is a cheap accuracy win. Purely additive — a
            # persistent failure falls through to the same behaviour as no retry.
            if kind is not None and self.repair_invalid_plans:
                retry_raw = self._extract_json(
                    self.orchestrator.infer(
                        system_msg,
                        self._validator_feedback_prompt(user_msg, raw_output, err or ""),
                        model_name=ollama_model,
                        max_tokens=max_tokens,
                    )
                )
                self.last_retried = True
                r_payload, r_kind, r_err = self._parse_and_check(retry_raw)
                if r_kind is None:
                    payload, kind, err = r_payload, None, None

        if kind == "parse":
            self.last_parse_error = err
            return {
                "plan": [
                    {
                        "tool": "clarify",
                        "args": {"question": "Could not parse your request. Please rephrase."},
                    }
                ]
            }
        if kind == "validate":
            # Parses but fails structural validation. Preserve prior behaviour:
            # fall through so execute_plan surfaces the error (no silent clarify).
            self.last_validation_error = err

        # Only the "parse" kind yields a None payload, and it returned above.
        assert payload is not None
        # Kept before the rewrites below, which mutate `payload` in place: without it nothing
        # downstream can tell a model error from a correct plan that core changed.
        self.last_model_plan = copy.deepcopy(payload)
        alert_last(payload)
        self._link_chain_intermediates(
            payload.get("plan") or [], user_utterance, self._output_capable, self.file_kinds
        )
        hallucinated = self._hallucinated_filename(
            payload.get("plan") or [], user_utterance, self._listed_filenames()
        )
        if hallucinated:
            return {
                "plan": [
                    {
                        "tool": "clarify",
                        "args": {
                            "question": (
                                f"You didn't mention {hallucinated!r} in your request — "
                                "which file should I work on?"
                            )
                        },
                    }
                ]
            }

        return payload

    def _parse_and_check(
        self, raw_output: str
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """Parse *raw_output* and structurally validate the resulting plan.

        Returns ``(payload, kind, error)`` where *kind* is ``None`` (clean),
        ``"parse"`` (invalid JSON/plan syntax — *payload* is ``None``), or
        ``"validate"`` (parses but fails structural validation — *payload* holds
        the parsed-but-invalid plan). The validation probe mirrors
        ``execute_plan``'s normalize → defaults → validate order on a deep copy,
        so the forgiving coercions don't trigger spurious retries and the
        returned payload keeps the model's raw shape.
        """
        try:
            payload = self.parse_plan(raw_output)
        except ValueError as exc:
            return None, "parse", str(exc)

        # The plan the pipeline will run: the alert is a run option and not checked as a step,
        # so an arg the model gives it never sends the model back.
        probe, _ = split_alert(copy.deepcopy(payload))
        try:
            normalize_plan(probe, self.registry)
            apply_defaults(probe, self.registry)
            # Missing-required-arg failures are owned by execute_plan's clarify
            # gate — retrying there risks the model hallucinating the value the
            # user never gave, turning a correct clarify into a wrong plan. Defer
            # to that gate by reporting "clean" so no retry fires.
            if required_args_clarify(probe.get("plan", []), self.registry) is not None:
                return payload, None, None
            self.validate_plan(probe)
        except ValueError as exc:
            return payload, "validate", str(exc)
        return payload, None, None

    @staticmethod
    def _validator_feedback_prompt(user_msg: str, raw_output: str, error: str) -> str:
        """Build a corrective re-prompt that injects the validator error."""
        return (
            f"{user_msg}\n\n"
            "Your previous response was rejected because it was invalid:\n"
            f"{raw_output}\n\n"
            f"Validation error: {error}\n\n"
            "Return a corrected JSON plan that fixes this error. "
            "Respond with ONLY the JSON object."
        )

    @staticmethod
    def _link_chain_intermediates(
        plan: list[dict[str, Any]],
        utterance: str,
        output_capable: set[str] | None = None,
        file_kinds: Mapping[str, str] | None = None,
    ) -> None:
        """Bind undeclared chain intermediates to the producing step's ``output``.

        The model often emits a multi-step chain where a later step consumes an
        intermediate filename (e.g. ``clip_resized.mp4``) but the earlier step
        that should produce it omits its ``output``. Left alone the intermediate
        looks like a hallucinated input (see :meth:`_hallucinated_filename`) and
        the chain is not executable. When a non-first step consumes a
        filename-like value that is absent from the utterance and not yet
        produced by an explicit earlier ``output``, assign it as the ``output``
        of the nearest preceding non-terminal step that has none — making the
        chain explicit, executable, and exempt from the hallucination guard.

        Only steps whose tool actually accepts an ``output`` arg are eligible
        producers (*output_capable*); writing ``output`` to a tool that does not
        declare it would fail schema validation. When *output_capable* is None
        (unit tests), any non-terminal step is eligible. Mutates *plan* in place;
        a single-step plan is left untouched so genuine hallucinations are still
        caught downstream.
        """
        u_lower = utterance.lower()
        produced: set[str] = set()
        for idx, step in enumerate(plan):
            if step.get("tool") in _TERMINAL_TOOLS:
                continue
            args = step.get("args") or {}
            inputs = {k: v for k, v in args.items() if k != "output"}
            for value in _iter_string_values(inputs):
                if any(c in value for c in "*?/\\"):
                    continue
                if not _FILENAME_RE.search(value):
                    continue
                if value.lower() in produced or value.lower() in u_lower:
                    continue
                # Undeclared intermediate: let the nearest earlier non-terminal
                # step that has no output and can accept one produce it.
                for prev in range(idx - 1, -1, -1):
                    pstep = plan[prev]
                    if pstep.get("tool") in _TERMINAL_TOOLS:
                        continue
                    if output_capable is not None and pstep.get("tool") not in output_capable:
                        continue
                    # A single `output` filename can only name one deliverable, so a
                    # producer that fans out to many (multiple input files, or a glob)
                    # is not a safe target — leave the intermediate for the
                    # hallucination guard rather than collide its batch outputs.
                    pargs = pstep.setdefault("args", {})
                    prod_files = [
                        v
                        for v in _iter_string_values(
                            {k: v for k, v in pargs.items() if k != "output"}
                        )
                        if _FILENAME_RE.search(v) or any(c in v for c in "*?")
                    ]
                    if len(prod_files) > 1 or any(any(c in f for c in "*?") for f in prod_files):
                        continue
                    if not pargs.get("output"):
                        pargs["output"] = value
                        produced.add(value.lower())
                        break
            out = (step.get("args") or {}).get("output")
            if isinstance(out, str):
                produced.add(out.lower())

        CommandAgent._forward_thread_reused_sources(plan, utterance, output_capable, file_kinds)

    @staticmethod
    def _forward_thread_reused_sources(
        plan: list[dict[str, Any]],
        utterance: str,
        output_capable: set[str] | None = None,
        file_kinds: Mapping[str, str] | None = None,
    ) -> None:
        """Thread a reused source filename onto the transforming step's output.

        The model sometimes emits a correct multi-step chain but points a later
        step at the ORIGINAL source filename instead of the file an earlier step
        produced from it (e.g. ``unlock_pdf sample.pdf`` then
        ``find_in_document sample.pdf`` — the search runs on the still-locked
        original and fails). When an earlier *output-capable* producer consumes a
        single source file and a later step reuses that same file, rewrite the
        later reference to the producer's output — assigning the producer an
        explicit intermediate ``output`` (same directory + extension as the
        source) when it declares none. This mirrors the undeclared-intermediate
        linker; the produced file then legitimately feeds the downstream step.

        Only *output_capable* tools are eligible producers — read-only tools
        (inspect / find / extract) declare no ``output`` and do not transform the
        file, so a later reuse of their input is left untouched. When
        *output_capable* is None (unit tests) any non-terminal step is eligible.
        A producer that fans out to many files (multiple inputs or a glob) is not
        a safe single-``output`` target and is skipped. Mutates *plan* in place.

        **Named-once rule.** A source the user wrote more than once in *utterance* is
        left alone: repeating the name makes each later step's input the user's choice,
        and a fan-out (several steps reading one file) must reach execution as written.
        A name said once ("…and check if *it* contains beta") or never (the model invented
        the intermediate) is still threaded.

        **Kind rule.** A producer whose declared ``output`` is a different kind of file from
        its source (a thumbnail of a video) is not threaded onto: the later step keeps the
        source. Kinds come from the skill's ``file_kinds:`` (*file_kinds*, extension ->
        kind); an extension it does not list is unrestricted, and a minted intermediate
        keeps the source's extension. See docs/plans/2026-09-23-chain-source-threading.md.

        **Sibling rule.** A later step that runs the *same tool* on the same source and names
        its own ``output`` is a second result from that source, not the next link of a chain
        ("cut 1-3 s of clip.mp4 as 1.mp4 and the rest as 2.mp4": both trims read clip.mp4).
        It keeps the source even when the user named it once. The same tool again *without* an
        output of its own is still threaded ("compress it again").
        """
        kinds = file_kinds or {}
        produced: set[str] = set()
        for step in plan:
            out = (step.get("args") or {}).get("output")
            if isinstance(out, str):
                produced.add(_basename(out).lower())

        for idx, step in enumerate(plan):
            if step.get("tool") in _TERMINAL_TOOLS:
                continue
            if output_capable is not None and step.get("tool") not in output_capable:
                continue
            args = step.get("args") or {}
            sources = [
                v
                for v in _iter_string_values({k: v for k, v in args.items() if k != "output"})
                if _FILENAME_RE.search(v) and not any(c in v for c in "*?")
            ]
            if len(sources) != 1:
                continue  # batch / glob producer — one output can't name many files
            src_base = _basename(sources[0]).lower()
            if _mention_count(utterance, src_base) > 1:
                continue  # the user named it again: later references are theirs

            # Collect later references to the same source file.
            targets: list[tuple[Any, Any]] = []
            for later in plan[idx + 1 :]:
                if later.get("tool") in _TERMINAL_TOOLS:
                    continue
                largs = later.get("args") or {}
                if later.get("tool") == step.get("tool") and _names_output(largs):
                    continue  # the same operation again, into its own file: a second result

                for key, value in largs.items():
                    if key == "output":
                        continue
                    if isinstance(value, str):
                        if _FILENAME_RE.search(value) and _basename(value).lower() == src_base:
                            targets.append((largs, key))
                    elif isinstance(value, list):
                        for li, item in enumerate(value):
                            if (
                                isinstance(item, str)
                                and _FILENAME_RE.search(item)
                                and _basename(item).lower() == src_base
                            ):
                                targets.append((value, li))
            if not targets:
                continue

            out = args.get("output")
            if isinstance(out, str) and out:
                src_kind = kinds.get(_extension(sources[0]))
                out_kind = kinds.get(_extension(out))
                if src_kind and out_kind and src_kind != out_kind:
                    continue  # a different kind of file: the later step meant the source
            else:
                out = _intermediate_name(sources[0], produced)
                args["output"] = out
                produced.add(_basename(out).lower())
            for container, key in targets:
                container[key] = out

    @staticmethod
    def _route_intermediates_to_scratch(
        plan: list[dict[str, Any]],
        work_dir: Path,
        utterance: str | None,
        resolution_dir: Path,
        joining_tools: Collection[str] = (),
        file_kinds: Mapping[str, str] | None = None,
        produces: Mapping[str, Any] | None = None,
        derives: Collection[str] = (),
    ) -> list[dict[str, Any]]:
        """Move each chain intermediate into *work_dir*, with every later step that reads it.

        An intermediate is a step's ``output`` that a later step reads. It keeps its name and
        its place relative to the user's folder (``videos/x.mp4`` -> ``work_dir/videos/x.mp4``),
        so every rendered argv still compares equal by basename across runtimes, and a later
        step can put its own output where the intermediate would have been. Left where the plan
        put them:

        * an output nothing reads later, which is a deliverable;
        * an output the user named in *utterance*, since a file the user named is one they
          asked for (owner, 2026-10-09);
        * an output the user asked for without naming it (:func:`_asked_for`, the keep rule,
          owner 2026-10-15): every later reader makes a **new thing** from it (another kind of
          file per *file_kinds* and *produces*, or a tool in *derives*, the skill's
          ``derives: true``; not when every reader is a join in *joining_tools*: pieces made
          for a join are on the way), or the user named its **format** and no reader writes that
          format again ("convert clip.mov to mp4 and make a gif preview" keeps the mp4). A reader that
          refines it (same kind, same format: "convert to mp4 and make it smaller") sends it to
          the scratch;
        * everything, when there is no utterance: then every name in the plan is the caller's.

        Returns a new plan; *plan* is not mutated. Run after the NL gates (which ground plan
        paths against the utterance) and before the skill's collision repair, so that repair
        no longer sees a chain's final name on its own intermediate.
        """
        if utterance is None:
            return plan
        plan = copy.deepcopy(plan)

        def key(value: str) -> str:
            p = Path(value)
            return os.path.normcase(str((p if p.is_absolute() else resolution_dir / p).resolve()))

        def is_ref(value: Any) -> bool:
            return isinstance(value, str) and bool(value) and not value.startswith("$")

        for idx, step in enumerate(plan):
            if step.get("tool") in _TERMINAL_TOOLS:
                continue
            args = step.get("args") or {}
            out = args.get("output")
            if not isinstance(out, str) or not is_ref(out) or not _FILENAME_RE.search(out):
                continue
            name = _basename(out)
            if _mention_count(utterance, name) > 0:
                continue
            # A producer that fans out writes one file per input under this one name, and only
            # one of them can be what a later step reads: the rest are deliverables (ffmpeg_134#2,
            # ffmpeg_114#2). A glob always fans out; several input files do unless the tool joins
            # them into one output (`joins_inputs:`, e.g. concat, ffmpeg_243#0).
            prod_files = [
                v
                for v in _iter_string_values({k: v for k, v in args.items() if k != "output"})
                if _FILENAME_RE.search(v) or any(c in v for c in "*?")
            ]
            if any(any(c in f for c in "*?") for f in prod_files):
                continue
            if len(prod_files) > 1 and step.get("tool") not in joining_tools:
                continue
            target = key(out)

            readers: list[tuple[Any, Any]] = []
            for later in plan[idx + 1 :]:
                if later.get("tool") in _TERMINAL_TOOLS:
                    continue
                largs = later.get("args") or {}
                for k, v in largs.items():
                    if k == "output":
                        continue
                    if is_ref(v) and key(v) == target:
                        readers.append((largs, k))
                    elif isinstance(v, list):
                        readers.extend(
                            (v, i)
                            for i, item in enumerate(v)
                            if is_ref(item) and key(item) == target
                        )
                # A later step writing the same file again ends this intermediate's readers.
                if is_ref(largs.get("output")) and key(largs["output"]) == target:
                    break
            if not readers:
                continue
            if _asked_for(
                out,
                step,
                plan[idx + 1 :],
                target,
                key,
                utterance,
                file_kinds,
                produces,
                derives,
                joining_tools,
            ):
                continue  # a result the user asked for: it stays where the plan put it

            # Same place relative to the user's folder, so a later step can put its own output
            # where this file would have been (`videos/x.mp4` -> `<work>/videos/x.mp4`).
            spelled = Path(out)
            resolved = (spelled if spelled.is_absolute() else resolution_dir / spelled).resolve()
            try:
                moved = work_dir / resolved.relative_to(resolution_dir.resolve())
            except ValueError:
                moved = work_dir / name
            args["output"] = str(moved)
            for container, k in readers:
                container[k] = str(moved)
        return plan

    def route_kinds(self) -> tuple[dict[str, str], dict[str, Any], set[str]]:
        """The skill's file kinds, each tool's ``produces:`` and the tools that ``derive``, for
        the keep rule (:func:`_asked_for`)."""
        return (
            self.file_kinds,
            {name: td.produces for name, td in self.registry.items() if td.produces is not None},
            {name for name, td in self.registry.items() if td.derives},
        )

    def _batch_outputs(self) -> dict[str, str]:
        """Each tool that saves its result only in a batch (`batch_output:`), to the extension."""
        return {name: td.batch_output for name, td in self.registry.items() if td.batch_output}

    def _lift_batch_chain(
        self, plan: list[dict[str, Any]], base: Path
    ) -> tuple[list[dict[str, Any]], BatchLift | None, dict[str, list[str]]]:
        """*plan* with its batch chain run once per file, globs and folders expanded on disk;
        the lift (which file each step works on), or None when nothing was lifted; and what
        each glob or folder expanded to.

        The lifted plan is validated again: every name it made must pass the same sandbox
        checks as the model's own.
        """
        from .batch_chain import INPUT_KEYS, expand_inputs, lift_batch

        entries: list[str] = []
        for step in plan:
            args = step.get("args") or {}
            for key in INPUT_KEYS:
                value = args.get(key)
                entries.extend([value] if isinstance(value, str) else value or [])
        expand = expand_inputs([e for e in entries if isinstance(e, str)], base, self.file_kinds)
        joining = {name for name, td in self.registry.items() if td.joins_inputs}
        lift = lift_batch(plan, expand, joining, batch_outputs=self._batch_outputs())
        if not lift.lifted:
            return lift.plan, None, expand
        self.validate_plan({"plan": lift.plan})
        return lift.plan, lift, expand

    def _planned_sets(
        self,
        plan: list[dict[str, Any]],
        work_dir: Path,
        dry_run: bool,
        confirmed: bool,
        skip_execution: bool,
    ) -> dict[int, PlannedSet]:
        """Each step whose tool writes many files from one, with what it will write
        (``Skill.planned_outputs``)."""
        if self.skill_instance is None:
            return {}
        sets: dict[int, PlannedSet] = {}
        for i, step in enumerate(plan):
            tool = step.get("tool")
            if not isinstance(tool, str) or tool in _TERMINAL_TOOLS:
                continue
            ctx = self._handler_context(dry_run, confirmed, skip_execution, work_dir, tool=tool)
            got = self.skill_instance.planned_outputs(tool, dict(step.get("args") or {}), ctx)
            if isinstance(got, PlannedSet) and got.files:
                sets[i] = got
        return sets

    def _lift_sets(
        self, plan: list[dict[str, Any]], planned: Mapping[str, list[str]], work_dir: Path
    ) -> tuple[list[dict[str, Any]], BatchLift | None]:
        """Run the steps reading a planned set once per file of it.

        A file in the run scratch is shown, and names its results, by its place in the user's
        folder. A join reading the set with no step before it reads every file.
        """
        home = {
            f: Path(f).relative_to(work_dir).as_posix()
            for files in planned.values()
            for f in files
            if Path(f).is_relative_to(work_dir)
        }
        joining = {name for name, td in self.registry.items() if td.joins_inputs}
        lift = lift_batch(
            plan, planned, joining, home=home, single=True, batch_outputs=self._batch_outputs()
        )
        if lift.lifted:
            self.validate_plan({"plan": lift.plan}, scratch=work_dir)
            return lift.plan, lift
        joined = []
        for step in plan:
            args = dict(step.get("args") or {})
            if step.get("tool") in joining:
                for key in ("inputs", "input"):
                    value = args.get(key)
                    if isinstance(value, list):
                        args[key] = [f for v in value for f in planned.get(v, [v])]
                    elif isinstance(value, str) and value in planned:
                        args[key] = list(planned[value])
            joined.append({**step, "args": args})
        return joined, None

    def _join_without_failed(
        self,
        intent_step: dict[str, Any],
        sources: dict[str, list[int | None]],
        failed: Mapping[int, str],
        scratch: Path,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        """A join with the failed files' results left out, expanded again; None when no file
        it was to join is left."""
        args = dict(intent_step.get("args") or {})
        kept_any = False
        for key, came_from in sources.items():
            value = args.get(key)
            if not isinstance(value, list) or len(value) != len(came_from):
                continue
            kept = [
                v for v, f in zip(value, came_from, strict=True) if f is None or f not in failed
            ]
            kept_any = kept_any or any(f is not None and f not in failed for f in came_from)
            args[key] = kept
        if not kept_any:
            return None
        step = {**intent_step, "args": args}
        sub_plan = self._expand_intent(step)
        if sub_plan != [step]:
            self.validate_plan({"plan": sub_plan}, allow_internal=True, scratch=scratch)
        return step, optimize_plan(sub_plan, self.registry)

    def _resolution_dir(self) -> Path:
        """The directory paths resolve against: the sandbox when configured, else root.

        `root` defaults to `Path.cwd()`, so this is open/CLI mode's working directory —
        the same rule native has always applied (`sandbox.unwrap_or(current_dir())`,
        apps/cli/src/main.rs). Both callers below MUST use it: the guard's stem exemption
        and stem resolution have to look at one directory, or the guard admits a name the
        resolver then refuses, or refuses one it would have resolved.
        """
        return self.sandbox if self.sandbox is not None else self.root

    def _listed_filenames(self) -> frozenset[str]:
        """Lowercased names of the files in `_resolution_dir()`, for the guard.

        Read at gate time rather than cached: the directory changes under a running agent
        (a chain writes into it), and a stale listing would reject a file that is there.
        Non-recursive and files-only, matching what stem resolution globs. Any OS error
        yields an empty set, which restores the strict rule rather than failing the plan.
        """
        try:
            return frozenset(
                p.name.lower() for p in self._resolution_dir().iterdir() if p.is_file()
            )
        except OSError:
            return frozenset()

    @staticmethod
    def _hallucinated_filename(
        plan: list[dict[str, Any]],
        utterance: str,
        known_files: Collection[str] = (),
    ) -> str | None:
        """Return any INPUT filename-like arg value missing from *utterance*, else None.

        The guard catches the model inventing *input* filenames the user never
        named. It deliberately ignores:
        - ``output`` arg values — output filenames are the model's to invent;
        - chained intermediates — a filename an earlier step declares it will
          produce (its ``output``) and a later step consumes is legitimate;
        - a **stem the user did name**, when the value resolves to a file that is
          actually there (*known_files*, the sandbox listing).

        The last case is the one the substring test got wrong. People name files the
        way they say them — "downscale clip_4k to 1920x1080" — and the model supplies
        `clip_4k.mp4`, the real file. Testing the *full* filename against the utterance
        then overrides a correct plan with a clarify. The T6a control arm lost eight
        utterances to exactly that.

        Two conditions keep the relaxation honest, and both are load-bearing:

        * **the value must name a file that exists.** "join clip.mov and clip_4k
          together" → `clip_4k.mov` keeps its clarify, because `clip_4k.mp4` is the
          file; naming a stem does not license guessing which file it is.
        * **the stem must look like a stem** — carrying ``_``, ``-`` or a digit, the
          same test :func:`planner._is_stem_candidate` uses to decide what is
          resolvable. Without it "make the video smaller" would pass `video.mp4` and
          the German "drei Clips" ("three clips") would pass `drei.mp4`, turning two
          genuine hallucinations into silent plans against the wrong file.

        *known_files* is passed in rather than read here so the guard stays pure and
        the L2 parity contract can state it (`sandbox_files` in
        `contracts/parity/clarify_gate_cases.json`). Empty means nothing can be
        confirmed, and the strict rule applies unchanged.
        """
        u_lower = utterance.lower()
        known = {name.lower() for name in known_files}
        # Filenames the plan itself produces; consuming one downstream is not a
        # hallucination.
        produced: set[str] = set()
        for step in plan:
            out = (step.get("args") or {}).get("output")
            if isinstance(out, str):
                produced.add(out.lower())

        for step in plan:
            if step.get("tool") in _TERMINAL_TOOLS:
                continue
            args = step.get("args") or {}
            # Skip the output key — invented output names must not be flagged.
            checkable = {k: v for k, v in args.items() if k != "output"}
            for value in _iter_string_values(checkable):
                if "*" in value or "?" in value:
                    continue
                if not _FILENAME_RE.search(value):
                    continue
                if value.lower() in produced:
                    continue
                if value.lower() in u_lower:
                    continue
                if _named_by_stem(value, u_lower, known):
                    continue
                return value
        return None

    # ── re-planning loop ──────────────────────────────────────────────────────

    def run(
        self,
        user_utterance: str,
        *,
        use_mock: bool = True,
        ollama_model: str = "mistral",
        max_tokens: int = 256,
        max_steps: int = 5,
        dry_run: bool = True,
        confirmed: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute a user request via the re-planning loop.

        Each iteration: retrieve relevant tools → infer one step (with history)
        → execute → append to history → repeat until done/clarify/reject or
        max_steps is reached.

        Returns a list of {step, result} dicts for each executed step.
        """
        history: list[dict[str, Any]] = []
        # Signatures of raw (pre-resolution) plan steps already submitted for
        # execution. Compared raw-to-raw so default-filling during execution
        # (e.g. a click option default added to the executed args) can't cause a
        # false miss.
        seen_steps: set[tuple[str, str]] = set()

        for _ in range(max_steps):
            retrieved = retrieve_tools(user_utterance, self.registry)
            payload = self.infer(
                user_utterance,
                use_mock=use_mock,
                ollama_model=ollama_model,
                max_tokens=max_tokens,
                history=history,
                registry_override=retrieved,
            )

            if not payload.get("plan"):
                break

            plan = payload["plan"]
            if plan[0]["tool"] == "done":
                break

            # Stale-plan guard: break if every actionable step in this plan was
            # already submitted. Covers single-step repeats and multi-step plans
            # that re-issue executed work with a trailing `done` (e.g.
            # [convert, done] after convert already ran).
            plan_keys = [
                (s.get("tool"), json.dumps(s.get("args", {}), sort_keys=True))
                for s in plan
                if s.get("tool") != "done"
            ]
            if plan_keys and all(k in seen_steps for k in plan_keys):
                break
            seen_steps.update(plan_keys)

            thinking = self.last_thinking
            results = self.execute_plan(
                payload,
                utterance=user_utterance,
                dry_run=dry_run,
                confirmed=confirmed,
            )

            for exec_result in results:
                history.append(
                    {
                        "step": {
                            "tool": exec_result["tool"],
                            "args": exec_result["args"],
                            "output": exec_result.get("output"),
                        },
                        "result": exec_result["result"],
                        "thinking": thinking,
                    }
                )

            # Terminate when the plan reached a terminal step. `done` may appear
            # as the last step of a multi-step plan (e.g. [convert, done]); the
            # plan[0]=="done" guard above only catches it when it leads the plan.
            if results and results[-1]["tool"] in ("clarify", "reject", "done"):
                break

        return history

    # ── evaluation ────────────────────────────────────────────────────────────

    def infer_stream(
        self,
        user_utterance: str,
        *,
        use_mock: bool = True,
        ollama_model: str = "mistral",
        max_tokens: int = 1024,
        registry_override: dict[str, ToolDef] | None = None,
    ) -> Iterator[tuple[str, str]]:
        """
        Stream inference, yielding ("thinking", chunk) or ("plan", chunk) tuples.

        Populates ``self.last_thinking`` as the thinking block streams in.
        After the iterator is exhausted, call
        ``agent.parse_plan(agent._clean_json(plan_acc))`` to get the plan dict.

        ``registry_override`` mirrors :meth:`infer`: pass the *retrieved* subset so the model
        sees the prompt it ships with. Production and the eval lane both retrieve before
        prompting, so a caller that streams without it shows the model every tool — a
        different prompt, and one the model reliably answers worse.
        """
        system_msg, user_msg = self.build_prompt(
            user_utterance, registry_override=registry_override
        )

        if use_mock:
            self.last_thinking = ""
            yield ("plan", self._mock_response(user_utterance))
            return

        if self.orchestrator is None:
            raise RuntimeError(
                "No orchestrator configured. Pass an InferenceOrchestrator to CommandAgent()."
            )

        OPEN_TAG = "<think>"
        CLOSE_TAG = "</think>"
        state = "scan"  # "scan" | "think" | "post"
        buf = ""
        thinking_acc = ""

        for raw_chunk in self.orchestrator.infer_stream(
            system_msg, user_msg, model_name=ollama_model, max_tokens=max_tokens
        ):
            buf += raw_chunk

            while buf:
                if state == "scan":
                    if buf.startswith(OPEN_TAG):
                        state = "think"
                        buf = buf[len(OPEN_TAG) :]
                    elif OPEN_TAG.startswith(buf):
                        # Partial prefix of <think> — wait for more data.
                        break
                    else:
                        state = "post"

                if state == "post":
                    yield ("plan", buf)
                    buf = ""
                    break

                if state == "think":
                    idx = buf.find(CLOSE_TAG)
                    if idx != -1:
                        segment = buf[:idx]
                        if segment:
                            thinking_acc += segment
                            yield ("thinking", segment)
                        state = "post"
                        buf = buf[idx + len(CLOSE_TAG) :]
                        # Loop again to flush remaining buf as plan.
                    else:
                        # Hold back any partial suffix that could be the start
                        # of </think> to avoid splitting the closing tag.
                        safe_end = len(buf)
                        for i in range(1, len(CLOSE_TAG)):
                            if buf.endswith(CLOSE_TAG[:i]):
                                safe_end = len(buf) - i
                                break
                        if safe_end > 0:
                            segment = buf[:safe_end]
                            thinking_acc += segment
                            yield ("thinking", segment)
                            buf = buf[safe_end:]
                        break  # Wait for more chunks.

        # Flush any content remaining in the buffer.
        if buf:
            if state == "think":
                thinking_acc += buf
                yield ("thinking", buf)
            else:
                yield ("plan", buf)

        self.last_thinking = thinking_acc

    # ── evaluation ────────────────────────────────────────────────────────────

    def run_eval(
        self,
        dataset: list[dict[str, Any]],
        *,
        use_mock: bool = False,
    ) -> list[dict[str, Any]]:
        """Run *dataset* through the pipeline and return per-item result rows."""
        return run_eval(self, dataset, use_mock=use_mock)

    def compute_metrics(self, eval_results: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate *eval_results* into summary metrics."""
        return compute_metrics(eval_results)


def _merge_core_tool_defs(registry: dict[str, ToolDef]) -> None:
    """Inject missing core tools (clarify/reject/done/wait_for_confirmation).

    CORE_TOOL_DEFS is already a name→ToolDef map (parsed from core_tools.yaml),
    so a skill that does not declare a core tool inherits the framework default.
    """
    for name, tool_def in CORE_TOOL_DEFS.items():
        registry.setdefault(name, tool_def)
