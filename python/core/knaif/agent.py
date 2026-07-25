"""CommandAgent – main entry point for the local command agent."""

from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core_tools import CORE_TOOL_DEFS
from .evaluator import compute_metrics, run_eval
from .executor import HANDLERS as _EXECUTOR_HANDLERS
from .handler_api import HandlerContext
from .nl_clarify_gate import nl_clarify_gate, required_args_clarify
from .planner import (
    _VALID_FILE_TYPES,
    StemAmbiguousError,
    StemNotFoundError,
    _resolve_path,
    apply_defaults,
    classify_preflight_errors,
    normalize_plan,
    optimize_plan,
    parse_plan,
    resolve_args,
    resolve_stems,
    summarize_plan_steps,
    validate_arg_by_schema,
    validate_plan,
)
from .prompt import build_prompt, normalize_path_separators
from .registry import ToolDef, load_registry, retrieve_tools
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
    rc = result.get("returncode")
    if rc is not None and rc != 0:
        return True
    for out in result.get("outputs") or []:
        if isinstance(out, dict):
            out_rc = out.get("returncode")
            if out_rc is not None and out_rc != 0:
                return True
    return False


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
        self.prompt_examples: list[dict] = []
        self.last_thinking: str = ""
        self.last_plan_summary: str = ""
        self.last_parse_error: str | None = None
        self.last_validation_error: str | None = None
        self.last_retried: bool = False
        # One corrective re-prompt on a parse/validation failure (validator-
        # feedback retry). Toggle off to measure the without-retry baseline.
        self.repair_invalid_plans: bool = True
        # OOP path: populated by from_skill() when skill_class: is present
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
            tools_yaml_path=skill.tools_yaml_path,
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

    def validate_plan(self, payload: dict[str, Any]) -> None:
        """Validate every step in *payload['plan']*; raises ValueError on failure."""
        validate_plan(payload, self.registry, self.root, self.sandbox)

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
        if utterance is not None:
            utterance = normalize_path_separators(utterance)

        normalize_plan(payload, self.registry)
        apply_defaults(payload, self.registry)

        # Missing-required-arg clarify gate (before structural validation): when
        # the model omits a required arg the user must supply, ask rather than
        # hard-error. Only fires on absent args, so well-formed plans are
        # untouched. Needs an utterance (NL path); direct execute_plan calls in
        # tests with deliberately-partial args still hit normal validation.
        if utterance is not None:
            missing = required_args_clarify(payload.get("plan", []), self.registry)
            if missing is not None:
                q = missing[0]["args"]["question"]
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

        intent_plan = payload["plan"]

        # Stem resolution on intent-level args, before expanders run.
        # Expanders receive the intent args and forward them to internal steps,
        # so resolving here means every expanded step sees the full filename.
        # Terminal tools (clarify/reject/done) carry no file paths — skipped.
        if self.sandbox is not None:
            clean_intents: list[dict[str, Any]] = []
            for intent_step in intent_plan:
                if intent_step.get("tool") in _TERMINAL_TOOLS:
                    clean_intents.append(intent_step)
                else:
                    try:
                        resolved_args = resolve_stems(intent_step.get("args", {}), self.sandbox)
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
                utterance, intent_plan, injected_files=injected_files, registry=self.registry
            )
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

        # Expand each intent independently, preserving intent boundaries.
        intent_blocks: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for intent_step in intent_plan:
            tool = intent_step.get("tool")
            sub_plan: list[dict[str, Any]]

            # OOP path: dispatch expansion via Intent.expand()
            tool_obj = self.tool_map.get(tool) if (self.tool_map and tool) else None
            if tool_obj is not None:
                from .tool import Intent as _Intent

                if isinstance(tool_obj, _Intent):
                    sub_plan = tool_obj.expand(intent_step.get("args", {}))
                    if not isinstance(sub_plan, list):
                        raise ValueError(f"Expander for {tool!r} must return a list of steps.")
                    output_var = intent_step.get("output")
                    if output_var and sub_plan:
                        last = dict(sub_plan[-1])
                        last["output"] = output_var
                        sub_plan = list(sub_plan[:-1]) + [last]
                else:
                    sub_plan = [intent_step]
            else:
                expander = self.expanders.get(tool) if tool else None
                if expander is None:
                    sub_plan = [intent_step]
                else:
                    sub_plan = expander(intent_step.get("args", {}))
                    if not isinstance(sub_plan, list):
                        raise ValueError(f"Expander for {tool!r} must return a list of steps.")
                    output_var = intent_step.get("output")
                    if output_var and sub_plan:
                        last = dict(sub_plan[-1])
                        last["output"] = output_var
                        sub_plan = list(sub_plan[:-1]) + [last]
            intent_blocks.append((intent_step, sub_plan))

        # Re-validate the union of expanded steps so structural errors surface
        # before any user-facing prompt.
        any_expanded = any(block != [orig] for orig, block in intent_blocks)
        if any_expanded:
            self.validate_plan({"plan": [s for _, b in intent_blocks for s in b]})

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
            from .tool import Intent as _Intent
            from .tool import Step as _Step

            # Files that earlier intents will produce — don't fail preflight for them.
            planned_output_names: set[str] = set()
            preflight_errors: list[str] = []
            for orig_intent, block in intent_blocks:
                for step in block:
                    tool = step.get("tool", "")
                    if tool in _TERMINAL_TOOLS:
                        continue
                    step_args = step.get("args") or {}
                    kw: dict[str, Any] = {
                        "root": self.root,
                        "sandbox": self.sandbox,
                        "planned_output_names": planned_output_names,
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

        context: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        summary_idx = 0

        for orig_intent, sub_plan in intent_blocks:
            is_terminal = orig_intent.get("tool") in _TERMINAL_TOOLS

            if not is_terminal and (_show or _approve):
                clause = step_summaries[summary_idx] if summary_idx < len(step_summaries) else ""
                summary_idx += 1

                if _show:
                    if self.plan_display is not None:
                        self.plan_display(clause)
                    else:
                        print(clause)

                if _approve:
                    if self.plan_confirmer is not None:
                        ok = bool(self.plan_confirmer(clause))
                    else:
                        ok = confirmed
                    if not ok:
                        break  # decline this intent → stop, keep prior results

            sub_results, should_stop = self._execute_steps(
                sub_plan, context, dry_run, confirmed, skip_execution
            )
            results.extend(sub_results)

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

        return results

    def _execute_steps(
        self,
        sub_plan: list[dict[str, Any]],
        context: dict[str, Any],
        dry_run: bool,
        confirmed: bool,
        skip_execution: bool,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Execute *sub_plan* sequentially against the shared *context*.

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
                        _resolve_path(args[path_key], self.root, self.sandbox)
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
            if tool_def and tool_def.safety_category == "destructive":
                if not dry_run and not confirmed:
                    raise ValueError(f"{tool!r} requires confirmed=True when dry_run=False.")

            ctx = HandlerContext(
                root=self.root,
                dry_run=dry_run,
                confirmed=confirmed,
                skill_dir=self.skill_dir,
                sandbox=self.sandbox,
                confirmer=self.confirmer,
                skip_execution=skip_execution,
            )
            _t0 = time.perf_counter()
            if exec_tool_obj is not None:
                result = exec_tool_obj.handle(args, ctx)
            else:
                result = self.handlers[tool](args, ctx)
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

            if _step_failed(result):
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
            filtered = select_examples(self.prompt_examples, retrieved_tool_names, user_utterance)
            if filtered:
                examples_block = render_examples_block(filtered)

        return build_prompt(
            user_utterance,
            registry,
            history=history or [],
            system_header=self.system_header,
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
        """Return a mock JSON response driven by registry keywords and mock_args."""
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
        # Normalize here, not just in build_prompt: the same text grounds the
        # hallucinated-filename guard and chain-intermediate linking below, and
        # those must compare against the paths the model was actually shown.
        user_utterance = normalize_path_separators(user_utterance)
        u_tokens = set(user_utterance.lower().split())
        if self.unsafe_phrases and any(
            all(word in u_tokens for word in phrase.lower().split())
            for phrase in self.unsafe_phrases
        ):
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
        self._link_chain_intermediates(
            payload.get("plan") or [], user_utterance, self._output_capable
        )
        hallucinated = self._hallucinated_filename(payload.get("plan") or [], user_utterance)
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

        probe = copy.deepcopy(payload)
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

        CommandAgent._forward_thread_reused_sources(plan, output_capable)

    @staticmethod
    def _forward_thread_reused_sources(
        plan: list[dict[str, Any]],
        output_capable: set[str] | None = None,
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
        """
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

            # Collect later references to the same source file.
            targets: list[tuple[Any, Any]] = []
            for later in plan[idx + 1 :]:
                if later.get("tool") in _TERMINAL_TOOLS:
                    continue
                largs = later.get("args") or {}
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
            if not isinstance(out, str) or not out:
                out = _intermediate_name(sources[0], produced)
                args["output"] = out
                produced.add(_basename(out).lower())
            for container, key in targets:
                container[key] = out

    @staticmethod
    def _hallucinated_filename(plan: list[dict[str, Any]], utterance: str) -> str | None:
        """Return any INPUT filename-like arg value missing from *utterance*, else None.

        The guard catches the model inventing *input* filenames the user never
        named. It deliberately ignores:
        - ``output`` arg values — output filenames are the model's to invent;
        - chained intermediates — a filename an earlier step declares it will
          produce (its ``output``) and a later step consumes is legitimate.
        """
        u_lower = utterance.lower()
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
                if value.lower() not in u_lower:
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
    ) -> Iterator[tuple[str, str]]:
        """
        Stream inference, yielding ("thinking", chunk) or ("plan", chunk) tuples.

        Populates ``self.last_thinking`` as the thinking block streams in.
        After the iterator is exhausted, call
        ``agent.parse_plan(agent._clean_json(plan_acc))`` to get the plan dict.
        """
        system_msg, user_msg = self.build_prompt(user_utterance)

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
