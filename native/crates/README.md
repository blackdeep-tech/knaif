# `native/crates/` — reusable Rust runtime libraries

Home of the **library-first native runtime**. These crates are consumed by every shipped
native surface — CLI, future Tauri desktop, future mobile shells — so they live at repo
root, not under `apps/cli/` (Resolved Decision #2).

| Crate | Responsibility |
|---|---|
| `knaif-core` | planner, registry, prompt, validate, safety, optimize, steps |
| `knaif-models` | `ModelStore`: manifest, download, checksum, atomic install, delete, store resolver — **no inference deps** |
| `knaif-llm` | `llama-cpp-2` backend (CPU + Vulkan + CUDA / Metal) + mock, behind a trait; depends on `knaif-models`; **no Ollama** |
| `knaif-skill-api` | `HandlerContext`, `Step`/`Intent` traits, sandbox helpers |

Skill implementations are **not** here — they live inside their bundle at
`skills/<name>/native/` (`knaif-skill-ffmpeg`, `knaif-skill-documents`), alongside the
declarative YAML both runtimes read. All of them, plus `apps/cli`, are members of the root
`Cargo.toml` workspace.

```bash
just check-native        # fmt + clippy, warnings are errors
just test-native         # cargo test --workspace
just parity <skill>      # native vs Python on real utterances
```

Tech stack, backends, and packaging: [`docs/NATIVE.md`](../../docs/NATIVE.md). Design
rationale: [`docs/plans/2026-06-17-monorepo-dual-runtime.md`](../../docs/plans/2026-06-17-monorepo-dual-runtime.md).
