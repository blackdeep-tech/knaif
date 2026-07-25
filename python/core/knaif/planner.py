"""Plan parsing and validation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .registry import ArgSchema, ToolDef

_TERMINAL_SUMMARY_TOOLS = frozenset({"done", "clarify", "reject"})

# ── stem resolution ───────────────────────────────────────────────────────────

# Extension-less ASCII identifier (e.g. ``clip_4k``). Must contain a structural
# marker (underscore, hyphen, or digit) to avoid treating bare words as stems.
_STEM_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]*$")
_STEM_STRUCTURE_RE = re.compile(r"[_\-]|\d")
_GLOB_CHARS = frozenset("*?[")
# Arg keys whose values are file paths (skills may use either singular or plural).
_PATH_ARG_KEYS = frozenset({"inputs", "input", "files", "src", "dst", "path", "base", "append"})


class StemNotFoundError(ValueError):
    """Raised when an extension-less stem matches no file in the sandbox."""

    def __init__(self, stem: str) -> None:
        super().__init__(f"No file matching '{stem}.*' found — please specify the filename.")
        self.stem = stem


class StemAmbiguousError(ValueError):
    """Raised when an extension-less stem matches more than one file in the sandbox."""

    def __init__(self, stem: str, matches: list[str]) -> None:
        super().__init__(
            f"'{stem}' matches multiple files: {', '.join(matches)} — please specify which one."
        )
        self.stem = stem
        self.matches = matches


def _is_stem_candidate(value: str) -> bool:
    """True if *value* looks like an extension-less filename stem to be resolved."""
    return (
        not value.startswith("$")
        and not any(c in value for c in _GLOB_CHARS)
        and "." not in value
        and bool(_STEM_ID_RE.match(value))
        and bool(_STEM_STRUCTURE_RE.search(value))
    )


def _resolve_one_stem(value: str, sandbox: Path) -> str:
    if not _is_stem_candidate(value):
        return value
    matches = [m for m in sandbox.glob(f"{value}.*") if m.is_file() and not m.name.startswith(".")]
    if not matches:
        raise StemNotFoundError(value)
    if len(matches) > 1:
        raise StemAmbiguousError(value, sorted(m.name for m in matches))
    return matches[0].name


def resolve_stems(args: dict[str, Any], sandbox: Path) -> dict[str, Any]:
    """Substitute extension-less filename stems in path-bearing args.

    For each value in a recognised path arg key (``inputs``, ``input``,
    ``files``, ``src``, ``dst``, ``path``) that looks like a stem:
    - 0 sandbox matches → :exc:`StemNotFoundError`
    - 1 sandbox match  → substituted with the full filename
    - ≥2 matches       → :exc:`StemAmbiguousError`

    Chain refs (``$var``), globs (``*.mp4``), and values that already carry an
    extension pass through unchanged. Returns a new dict; *args* is not mutated.
    """
    resolved = dict(args)
    for key in _PATH_ARG_KEYS:
        if key not in resolved:
            continue
        val = resolved[key]
        if isinstance(val, str):
            resolved[key] = _resolve_one_stem(val, sandbox)
        elif isinstance(val, list):
            resolved[key] = [
                _resolve_one_stem(v, sandbox) if isinstance(v, str) else v for v in val
            ]
    return resolved


_VALID_FILE_TYPES = frozenset(
    {
        "executable",
        "text",
        "image",
        "document",
        "script",
        "archive",
        "log",
        "config",
    }
)

# Valid output declaration: $identifier (no dots — dots are reference-only)
_OUTPUT_RE = re.compile(r"^\$[a-zA-Z_][a-zA-Z0-9_]*$")

# Valid $var or $var.field reference in an arg value
_VAR_REF_RE = re.compile(r"^\$[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?$")


def _resolve_path(raw_path: str, root: Path, sandbox: Path | None) -> Path:
    """Resolve *raw_path*, optionally enforcing a sandbox boundary.

    When *sandbox* is provided:
    - Relative paths are resolved against the sandbox root.
    - Absolute paths must still fall inside the sandbox.

    When *sandbox* is ``None`` (open / CLI mode):
    - Relative paths are resolved against *root* (cwd).
    - Absolute paths are accepted as-is; no boundary is enforced.
    """
    path = Path(raw_path)
    if sandbox is not None:
        if not path.is_absolute():
            path = (sandbox / path).resolve()
        else:
            path = path.resolve()
        try:
            path.relative_to(sandbox.resolve())
        except ValueError as exc:
            raise ValueError(
                f"Path '{path}' is outside sandbox '{sandbox}'. Use a sandbox-relative path."
            ) from exc
    else:
        if not path.is_absolute():
            path = (root / path).resolve()
        else:
            path = path.resolve()

    return path


def _validate_var_ref_syntax(value: str) -> None:
    """Raise ValueError if *value* is not a well-formed $var or $var.field reference."""
    if not _VAR_REF_RE.match(value):
        raise ValueError(
            f"Malformed variable reference {value!r}. Expected $identifier or $identifier.field"
        )


def resolve_args(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Return a new args dict with $var and $var.field references resolved.

    Non-string values pass through unchanged. Raises ValueError for missing
    variables or missing fields.
    """
    resolved: dict[str, Any] = {}
    for key, val in args.items():
        if not isinstance(val, str) or not val.startswith("$"):
            resolved[key] = val
            continue

        ref = val.lstrip("$")
        if "." in ref:
            varname, fieldname = ref.split(".", 1)
        else:
            varname, fieldname = ref, None

        if varname not in context:
            raise ValueError(f"Variable '{val}' used in args was never assigned.")

        value = context[varname]

        if fieldname is None:
            resolved[key] = value
        else:
            if not isinstance(value, dict):
                raise ValueError(
                    f"Variable '{val}': '{varname}' is not a dict, cannot access field '{fieldname}'."
                )
            if fieldname not in value:
                raise ValueError(f"Variable '{val}': field '{fieldname}' not found in '{varname}'.")
            resolved[key] = value[fieldname]

    return resolved


def optimize_plan(
    plan: list[dict[str, Any]],
    registry: dict[str, ToolDef],
) -> list[dict[str, Any]]:
    """Return a new plan with redundant readonly steps removed.

    A readonly step is removed only when a later non-readonly (action) step
    exists AND the step's output variable (if any) is not referenced by any
    subsequent step's args. Terminal readonly steps are always preserved.
    """
    referenced_after: set[str] = set()
    has_later_action = False
    to_remove: set[int] = set()

    for i in range(len(plan) - 1, -1, -1):
        step = plan[i]
        tool_def = registry.get(step.get("tool", ""))
        is_readonly = tool_def.readonly if tool_def else False

        if is_readonly and has_later_action:
            output = step.get("output")
            varname = output.lstrip("$") if output else None
            if varname is None or varname not in referenced_after:
                to_remove.add(i)

        for val in step.get("args", {}).values():
            if isinstance(val, str) and val.startswith("$"):
                referenced_after.add(val.lstrip("$").split(".", 1)[0])

        if not is_readonly:
            has_later_action = True

    return [step for i, step in enumerate(plan) if i not in to_remove]


def normalize_plan(
    payload: dict[str, Any],
    registry: dict[str, ToolDef] | None = None,
) -> None:
    """Normalise a raw model payload in-place before validation.

    Two passes:

    1. Promote args['output'] = '$identifier' to the step-level output field.
       The model sometimes encodes the variable-binding declaration inside args
       instead of at the top level of the step object.  Tools that legitimately
       declare 'output' as a required/optional arg always receive filenames
       there, not $identifier patterns, so the promotion is safe.

    2. (Registry-driven) Reconcile the singular/plural input arg with the tool's
       schema, in BOTH directions, when the schema declares exactly one of
       `input`/`inputs` and the model supplied the other.  Small models freely
       interchange the two:

       - A tool whose schema declares scalar `input` (not `inputs`), given a
         single-element `inputs` list (or bare string) → unwrap to scalar `input`.
         A multi-element `inputs` is left for validation to reject (a scalar-input
         tool genuinely cannot take multiple files).
       - A tool whose schema declares plural `inputs` (not `input`), given a
         scalar `input` string → wrap in a single-element `inputs` list.  This is
         the dominant remaining `missing required args: ['inputs']` class.

       Both branches are purely schema-driven — no tool is named here, so core
       stays skill-agnostic.  If both keys are already present, nothing is touched
       — never silently merge or overwrite a value the model supplied.
    """
    for step in payload.get("plan", []):
        args = step.get("args")
        if not isinstance(args, dict):
            continue

        # Pass 1 — $var output promotion
        out_val = args.get("output")
        if isinstance(out_val, str) and _OUTPUT_RE.match(out_val) and "output" not in step:
            step["output"] = out_val
            del args["output"]

        # Pass 2 — input/inputs coercion (registry-driven, only when registry supplied)
        if registry is None:
            continue
        tool_def = registry.get(step.get("tool"))
        if tool_def is None:
            continue
        allowed = set(tool_def.required_args) | set(tool_def.optional_args)
        wants_scalar = "input" in allowed and "inputs" not in allowed
        wants_plural = "inputs" in allowed and "input" not in allowed

        if wants_scalar and "inputs" in args and "input" not in args:
            raw = args["inputs"]
            if isinstance(raw, list):
                if len(raw) == 1:
                    args["input"] = raw[0]
                    del args["inputs"]
                # ≥2 elements: leave for validation to reject
            elif isinstance(raw, str):
                args["input"] = raw
                del args["inputs"]
        elif wants_plural and "input" in args and "inputs" not in args:
            raw = args["input"]
            if isinstance(raw, str):
                args["inputs"] = [raw]
                del args["input"]
            elif isinstance(raw, list):
                args["inputs"] = raw
                del args["input"]

        # Pass 3 — arg-key aliases. Models put the right value under a sibling
        # key (e.g. split_pdf given `pages` when the schema wants `ranges`).
        # Rename to the canonical key, never clobbering one the model supplied.
        for src_key, dst_key in (tool_def.arg_aliases or {}).items():
            if src_key in args and dst_key not in args:
                args[dst_key] = args.pop(src_key)

        # Pass 4 — scalar type coercion: a string/enum-typed arg arriving as a
        # number (e.g. pages=2 → "2"). Integer/number-typed args are left alone.
        for arg_name, value in list(args.items()):
            schema = tool_def.arg_schemas.get(arg_name)
            if schema is None:
                continue
            if (
                schema.type in ("string", "enum")
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                args[arg_name] = str(value)
            # The reverse: a numeric string for a numeric-typed arg (qwen
            # sometimes quotes `degrees: "180"`). Non-numeric strings (incl. var
            # refs like "$deg") fail the parse and are left for later stages.
            elif schema.type == "integer" and isinstance(value, str):
                try:
                    args[arg_name] = int(value.strip())
                except ValueError:
                    pass
            elif schema.type == "number" and isinstance(value, str):
                try:
                    args[arg_name] = float(value.strip())
                except ValueError:
                    pass

        # Pass 5 — enum value coercion (schema-driven aliases + case-insensitive
        # match). Models emit synonyms/casing the enum rejects ("markdown"→"md",
        # "MD"→"md", "bottom"→"bottom-center"). Unknown values are left untouched
        # for validation to reject. Skill-agnostic: the synonym map lives in the
        # tool's arg_schema, not here.
        for arg_name, value in list(args.items()):
            schema = tool_def.arg_schemas.get(arg_name)
            if schema is None or not schema.enum or not isinstance(value, str):
                continue
            if value in schema.enum:
                continue
            low = value.lower()
            alias_map = {k.lower(): v for k, v in (schema.aliases or {}).items()}
            if low in alias_map:
                args[arg_name] = alias_map[low]
                continue
            # Case- and separator-insensitive match: models emit snake_case or
            # spaced forms of hyphenated enums ("bottom_center" → "bottom-center").
            # Both sides are normalized to a single separator so the match is
            # bidirectional (hyphen↔underscore↔space).
            target = re.sub(r"[\s_]+", "-", low)
            for enum_val in schema.enum:
                if re.sub(r"[\s_]+", "-", enum_val.lower()) == target:
                    args[arg_name] = enum_val
                    break


def parse_plan(json_text: str) -> dict[str, Any]:
    """Parse *json_text* and return the payload dict."""
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(payload, dict) or "plan" not in payload:
        raise ValueError("Payload must be a JSON object with a 'plan' field.")
    if not isinstance(payload["plan"], list):
        raise ValueError("'plan' must be a list.")

    return payload


def validate_arg_by_schema(arg_name: str, value: Any, schema: ArgSchema) -> None:
    """Raise ValueError if *value* doesn't conform to *schema*.

    $var references (strings starting with '$') pass through unchanged —
    they are resolved at runtime and validated then.
    """
    if isinstance(value, str) and value.startswith("$"):
        return

    t = schema.type
    if t == "string":
        if not isinstance(value, str):
            raise ValueError(f"Arg '{arg_name}' must be a string, got {type(value).__name__!r}")
    elif t == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"Arg '{arg_name}' must be a boolean, got {type(value).__name__!r}")
    elif t == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"Arg '{arg_name}' must be an integer, got {type(value).__name__!r}")
        if schema.min is not None and value < schema.min:
            raise ValueError(f"Arg '{arg_name}' must be >= {schema.min!r}")
        if schema.max is not None and value > schema.max:
            raise ValueError(f"Arg '{arg_name}' must be <= {schema.max!r}")
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Arg '{arg_name}' must be a number, got {type(value).__name__!r}")
        if schema.min is not None and value < schema.min:
            raise ValueError(f"Arg '{arg_name}' must be >= {schema.min!r}")
        if schema.max is not None and value > schema.max:
            raise ValueError(f"Arg '{arg_name}' must be <= {schema.max!r}")
    elif t == "array":
        if not isinstance(value, list):
            raise ValueError(f"Arg '{arg_name}' must be an array, got {type(value).__name__!r}")
    elif t == "enum":
        if schema.enum is not None:
            if value not in schema.enum:
                raise ValueError(
                    f"Arg '{arg_name}' must be one of {list(schema.enum)}, got {value!r}"
                )


def validate_step(
    step: dict[str, Any],
    registry: dict[str, ToolDef],
    root: Path,
    sandbox: Path | None = None,
) -> None:
    """Raise ValueError if *step* is structurally invalid."""
    if not isinstance(step, dict):
        raise ValueError("Each plan step must be an object.")

    tool_name = step.get("tool")
    args = step.get("args")

    if tool_name not in registry:
        raise ValueError(f"Unknown tool: {tool_name!r}")
    if not isinstance(args, dict):
        raise ValueError("Each step must contain an 'args' object.")

    tool_def = registry[tool_name]
    allowed = set(tool_def.required_args) | set(tool_def.optional_args)

    missing = [k for k in tool_def.required_args if k not in args]
    if missing:
        raise ValueError(f"Tool '{tool_name}' missing required args: {missing}")

    if tool_def.any_of_args and not any(k in args for k in tool_def.any_of_args):
        raise ValueError(
            f"Tool '{tool_name}' requires at least one of: {list(tool_def.any_of_args)}"
        )

    extra = [k for k in args if k not in allowed]
    if extra:
        raise ValueError(f"Tool '{tool_name}' has unsupported args: {extra}")

    for arg_name, value in args.items():
        schema = tool_def.arg_schemas.get(arg_name)
        if schema is not None:
            try:
                validate_arg_by_schema(arg_name, value, schema)
            except ValueError as exc:
                raise ValueError(f"Tool '{tool_name}' arg validation: {exc}") from exc

    output = step.get("output")
    if output is not None:
        if not isinstance(output, str) or not _OUTPUT_RE.match(output):
            raise ValueError(f"Step output must be a $identifier (no dots), got: {output!r}")

    if "path" in args:
        val = args["path"]
        if isinstance(val, str) and val.startswith("$"):
            _validate_var_ref_syntax(val)
        else:
            if not isinstance(val, str):
                raise ValueError("'path' must be a string.")
            _resolve_path(val, root, sandbox)

    for path_key in ("src", "dst"):
        if path_key in args:
            val = args[path_key]
            if isinstance(val, str) and val.startswith("$"):
                _validate_var_ref_syntax(val)
            else:
                if not isinstance(val, str):
                    raise ValueError(f"'{path_key}' must be a string.")
                _resolve_path(val, root, sandbox)

    if "pattern" in args:
        val = args["pattern"]
        if isinstance(val, str) and val.startswith("$"):
            _validate_var_ref_syntax(val)
        elif not isinstance(val, str):
            raise ValueError("'pattern' must be a string.")

    if "file_type" in args:
        val = args["file_type"]
        if isinstance(val, str) and val.startswith("$"):
            _validate_var_ref_syntax(val)
        else:
            if not isinstance(val, str):
                raise ValueError("'file_type' must be a string.")
            if val not in _VALID_FILE_TYPES:
                raise ValueError(
                    f"Unknown file_type: {val!r}. Valid types: {sorted(_VALID_FILE_TYPES)}"
                )

    if "recursive" in args:
        val = args["recursive"]
        if isinstance(val, str) and val.startswith("$"):
            _validate_var_ref_syntax(val)
        elif not isinstance(val, bool):
            raise ValueError("'recursive' must be a boolean.")


def apply_defaults(payload: dict[str, Any], registry: dict[str, Any]) -> None:
    """Fill missing args from ToolDef.defaults for each step in *payload['plan']*.

    Operates in-place. Explicit values are never overwritten.
    """
    for step in payload.get("plan", []):
        tool_name = step.get("tool")
        tool_def = registry.get(tool_name) if tool_name else None
        if tool_def is None or not tool_def.defaults:
            continue
        args = step.setdefault("args", {})
        for key, value in tool_def.defaults.items():
            if key not in args:
                args[key] = value


def classify_preflight_errors(errors: list[str]) -> str:
    """Return 'reject' for sandbox-escape errors, 'clarify' for all others."""
    if any("outside the sandbox" in e for e in errors):
        return "reject"
    return "clarify"


def validate_plan(
    payload: dict[str, Any],
    registry: dict[str, ToolDef],
    root: Path,
    sandbox: Path | None = None,
) -> None:
    """Validate every step in *payload['plan']*; raises ValueError on first failure."""
    plan = payload["plan"]
    multi_step = len(plan) > 1
    assigned: set[str] = set()

    for i, step in enumerate(plan, start=1):
        try:
            validate_step(step, registry, root, sandbox)
        except ValueError as exc:
            raise ValueError(f"Plan step {i} invalid: {exc}") from exc

        if multi_step:
            for val in step.get("args", {}).values():
                if isinstance(val, str) and val.startswith("$"):
                    varname = val.lstrip("$").split(".", 1)[0]
                    if varname not in assigned:
                        raise ValueError(
                            f"Plan step {i}: variable '{val}' used before it is assigned."
                        )

        output = step.get("output")
        if output:
            assigned.add(output.lstrip("$"))


# ── summarize_plan ───────────────────────────────────────────────────────────


def _brief_args(args: dict[str, Any]) -> str:
    """Format an args dict as a short, human-readable string for the generic fallback."""
    if not args:
        return ""
    parts: list[str] = []
    for key, val in args.items():
        if isinstance(val, list):
            rendered = ",".join(str(v) for v in val)
        else:
            rendered = str(val)
        parts.append(f"{key}={rendered}")
    return " ".join(parts)


def summarize_plan_steps(
    plan: list[dict[str, Any]],
    summarizers: dict[str, Callable[[dict[str, Any]], str]] | None = None,
    **kwargs: Any,
) -> list[str]:
    """Return one raw summary clause per non-terminal intent step.

    Each clause is suitable for display as a bullet point — no "Will" prefix,
    no trailing period. Terminal tools (``done`` / ``clarify`` / ``reject``) are
    skipped. Extra kwargs are forwarded to summarizers for richer descriptions.
    """
    summarizers = summarizers or {}
    clauses: list[str] = []
    for step in plan:
        tool = step.get("tool", "")
        if tool in _TERMINAL_SUMMARY_TOOLS:
            continue
        args = step.get("args") or {}
        fn = summarizers.get(tool)
        if fn is not None:
            try:
                clause = str(fn(args, **kwargs)).strip()
            except TypeError:
                # Summarizer does not accept **kwargs — call without them.
                try:
                    clause = str(fn(args)).strip()
                except Exception:  # noqa: BLE001
                    clause = ""
            except Exception:  # noqa: BLE001
                clause = ""
            if not clause:
                clause = _generic_clause(tool, args)
        else:
            clause = _generic_clause(tool, args)
        clauses.append(clause)
    return clauses


def summarize_plan(
    plan: list[dict[str, Any]],
    summarizers: dict[str, Callable[[dict[str, Any]], str]] | None = None,
    **kwargs: Any,
) -> str:
    """Produce a one-sentence human-readable description of an intent plan.

    Delegates to :func:`summarize_plan_steps` and joins the clauses with
    ``", then "`` into a ``"Will …."`` sentence.
    """
    clauses = summarize_plan_steps(plan, summarizers, **kwargs)
    if not clauses:
        return "(empty plan)"
    return "Will " + ", then ".join(clauses) + "."


def _generic_clause(tool: str, args: dict[str, Any]) -> str:
    brief = _brief_args(args)
    return f"{tool} {brief}".rstrip()
