# `apps/cli/` — native Rust CLI

The **native Rust CLI app** — the first shipped native product. It is a **thin wrapper**
around the reusable engine crates in root `native/crates/` (`knaif-core`, `knaif-llm`,
`knaif-models`, `knaif-skill-api`, `knaif-skill-*`); desktop and mobile apps reuse the same
crates, so the CLI owns no engine logic of its own.

Natural-language command shape: `knaif run ffmpeg "compress this for discord"`.
Skill selection is compile-time (cargo features).

Commands: `--version`, `skills list`, `run <skill> "<req>"`, `plan --skill <X> --json`.

See [`docs/NATIVE.md`](../../docs/NATIVE.md).
