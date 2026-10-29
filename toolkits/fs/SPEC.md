# fs toolkit

Shared file tools. A skill uses them by declaring `toolkits: [fs]` in its `skill.yaml`; their
contracts live only in this toolkit's `tools.yaml`. All of them are internal (the model never
sees them) unless a skill lists one under `expose:`.

## System Requirements

None. Pure Python standard library, confined to the sandbox.

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `resolve_inputs` | Handler, readonly | Expands a list of paths, folders and globs inside the sandbox into files, optionally filtered by extension and recursive. Returns `{count, files}`. A missing non-glob path is kept in the list on purpose, so a later tool reports it. |
| `stage_output` | Handler | A path in the run scratch with the final file's name, in a folder of its own. Returns `{staged}`. |
| `stage_copy` | Handler | A staged copy of a file in the run scratch, with the final file's name, as `stage_output`; hand it to `commit_output`. The source stays in place, since a later step may still read it. How a step delivers a chain intermediate unchanged (ffmpeg: a compress that could not make it smaller). Returns `{staged}`. |
| `commit_output` | Handler, destructive | Checks a staged file (exists, not empty) and moves it to its final path inside the sandbox. A failure removes the staged file and leaves the user's folder untouched; a final path inside the scratch is refused. Returns `{path, bytes}`. |
| `ensure_parent` | Handler | Makes the resolved parent folder of a path inside the sandbox or the run scratch. Returns `{path}`. |
| `plan_output_path` | Handler, readonly | The one free-name rule; the caller picks the suffix and numbering (ffmpeg `_converted_2`, documents `-1`). Returns `{path}`. |
| `user_side_path` | Handler, readonly | A path, or where it would sit in the user's folder when it is in the run scratch (routing keeps an intermediate's place, so the mapping is exact). Returns `{path}`. |

Handlers reach these through `ctx.call_tool(...)`, never by import. Each declares its effects
(`resolve_inputs` and `plan_output_path` `fs.read`; `stage_output`, `commit_output` and
`ensure_parent` `fs.write`; `stage_copy` `fs.read` and `fs.write`; `user_side_path` none), so a caller must declare them too. The native runtime has the
same rules as functions in the `knaif-toolkit-fs` crate (`native/`), called by skill crates.

## Used by

`ffmpeg`, `documents`.

## Tests

`uv run pytest toolkits/fs/python/tests/` and `cargo test -p knaif-toolkit-fs`
