# Variable Binding And Plan Optimizer

This document describes the implemented data-flow mechanism for multi-step plans and the conservative optimizer that removes unused read-only work.

## Objective

Variable binding lets one step feed a later step:

```json
{
  "plan": [
    { "tool": "find_files", "args": { "path": ".", "file_type": "text" }, "output": "$found" },
    { "tool": "summarize_files", "args": { "files": "$found.files" } }
  ]
}
```

It also lets `CommandAgent.execute_plan()` remove read-only steps whose results are not consumed before a later action.

## Implemented Components

| File | Role |
|---|---|
| `skills/io/tools.yaml` | I/O skill tool definitions |
| `skills/ffmpeg/tools.yaml` | FFmpeg skill tool definitions |
| `knaif/registry.py` | `ToolDef`, registry loading, retrieval |
| `knaif/planner.py` | validation, optimization, variable resolution |
| `knaif/agent.py` | expansion, optimization, execution |
| `knaif/skill.py` | skill loading |
| `knaif/handler_api.py` | handler context |

## Plan Schema

Any plan step may declare an optional `output` field.

```json
{ "tool": "inspect_media", "args": { "files": "$files" }, "output": "$probes" }
```

Rules:

- Output declarations must be `$identifier`.
- Dotted output declarations are invalid.
- Args can reference `$var` or `$var.field`.
- Dotted references split on the first dot and require the stored value to be a dict.
- Variables are scoped to one `execute_plan()` call.

## Validation

`validate_step()` checks:

- the step is an object
- the tool exists
- `args` is an object
- required args are present
- unsupported args are rejected
- `output`, when present, is a valid `$identifier`
- `$var` references have valid syntax

When an arg value starts with `$`, semantic validation is deferred until execution. This is necessary because the runtime value is not available during structural validation.

`validate_plan()` also checks forward references for multi-step plans. A step cannot use `$x` before an earlier step declares `output: "$x"`. Single-step plans skip this check so the re-planning loop can still infer one step at a time.

## Runtime Resolution

`resolve_args(args, context)` returns a new args dict. It does not mutate the input.

```python
resolve_args(
    {"exchange": "$market.exchange"},
    {"market": {"exchange": "Binance", "price": 41000}},
)
# {"exchange": "Binance"}
```

After resolution, `execute_plan()` revalidates sandbox-sensitive path args and known enum args before dispatching the handler.

## Optimizer

`optimize_plan(plan, registry)` removes read-only steps only when all of these are true:

1. The tool has `readonly: true`.
2. A later non-readonly step exists.
3. The read-only step has no output, or its output variable is not referenced by later args.

The optimizer never removes action steps, never reorders steps, and never removes read-only terminal-answer steps.

Examples:

| Plan | Outcome |
|---|---|
| `[list_files]` | Kept |
| `[list_files, find_files]` | Both kept |
| `[find_files, move_files]` | `find_files` removed |
| `[find_files output:$f, move_files]` | `find_files` removed if `$f` is unused |
| `[find_files output:$f, some_action uses $f]` | Both kept |

## Interaction With Skills

Skill packages are implemented. This mechanism is used by the current `Skill.load()`, `CommandAgent.from_skill()`, `HandlerContext`, and the `Step` / `Intent` class architecture.

`Intent.expand()` can emit multi-step workflows with variable bindings:

```json
{
  "plan": [
    { "tool": "resolve_inputs", "args": { "paths": ["clip.mp4"] }, "output": "$files" },
    { "tool": "inspect_media", "args": { "files": "$files" }, "output": "$probes" },
    { "tool": "build_recipes", "args": { "probes": "$probes" }, "output": "$recipes" }
  ]
}
```

The FFmpeg skill relies on this heavily. Public tools such as `prepare_for_platform` expand into internal workflow tools that exchange data through `$files`, `$probes`, `$recipes`, and later variables.

### Caveat: no cross-*intent* references in expander-based skills

The bindings above are **intra**-expansion. A model-emitted plan must not chain one
public intent to the next through a variable — `trim_video output: "$trimmed"` followed
by `extract_audio files: "$trimmed.files"` never resolves in a skill like ffmpeg,
because `Intent.expand()` consumes the intent's args at *expansion* time, before any
earlier intent has executed and bound its output. The unresolved token leaks through
`resolve_inputs` and any derived filename inherits the leaked stem.

For such skills the prompt must instruct the model to name the intermediate file
explicitly and reuse that name verbatim as the next intent's input. Cross-step variable
binding does work for direct-handler skills like `io`, where args stay top-level and are
resolved at execution time.

### Choosing how to chain

| The intermediate is… | Chain with | Example |
|---|---|---|
| Knowable up front (a filename the model invents) | a **literal value** repeated in the next step | ffmpeg's `trim_video` → `extract_audio` |
| A runtime value (a probe result, a filled quantity) | **`$var` / `$var.field`** | a trading skill's fill amount |

Prefer the literal when both would work — it survives expansion, needs no binding, and
renders identically in the plan preview.

## Prompt And History Status

The default prompt and the I/O skill prompt describe the optional `output` field and `$var` references. `_format_history()` includes the output variable name when a previous step declared one.

`CommandAgent.run()` still uses a re-planning loop. Direct calls to `execute_plan()` execute the whole expanded plan in one pass.

## Testing

Core tests cover:

- output syntax validation
- malformed variable references
- forward-reference rejection
- scalar and dotted variable resolution
- optimizer pruning and preservation rules
- execution with resolved args
- sandbox escape rejection after resolution

Run:

```bash
uv run pytest tests/test_planner.py tests/test_agent.py -v
uv run pytest --tb=short
```
