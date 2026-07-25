# `skills/` — canonical skill bundles

The single, canonical home for **self-contained skill bundles**, shared across every
runtime (Python, Rust, and future Tauri/Swift/Kotlin apps). It lives at repo **root** — not
under `contracts/` or a framework path — so skill data is maximally discoverable to every
app (Resolved Decision #2).

**Bundle layout** (Resolved Decision #3): each skill is a bundle at `skills/<name>/` with
the declarative YAML at the **top** of the bundle and per-language implementations in
named subfolders:

```text
skills/<name>/
├── skill.yaml · tools.yaml · prompt.yaml · vocab.yaml · SPEC.md   # data at the TOP (all apps read this)
├── profiles/** · data/*.jsonl · eval/
├── python/                 # a real package, loaded by path (repo-only, excluded from the wheel)
│   ├── __init__.py · handlers.py · intents.py · steps.py · _engine.py
│   └── tests/
└── native/                 # Rust crate; a member of the root Cargo workspace
    └── Cargo.toml · src/
```

The declarative YAML is the **single source of truth** consumed by both runtimes;
`ctx.skill_dir` resolves to the bundle root. App-specific tweaks are supplied as YAML
**override deltas** owned by the app, never forks of the bundle.

Live skills:

| Skill | Runtimes | Status |
|---|---|---|
| `ffmpeg` | python + native (`knaif-skill-ffmpeg`) | supported |
| `documents` | python + native (`knaif-skill-documents`) | supported |
| `io` | python only | `status: stale` — under rebuild |

Authoring guide: [`docs/TOOL_SCHEMA.md`](../docs/TOOL_SCHEMA.md). Design rationale:
[`docs/plans/2026-06-17-monorepo-dual-runtime.md`](../docs/plans/2026-06-17-monorepo-dual-runtime.md).
