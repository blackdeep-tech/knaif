# `installers/macos/` — macOS packaging (placeholder, POST-V1)

**Status:** placeholder · **Migration phase:** created in Phase 1; **deferred to a post-v1
fast-follow.**

macOS is **not a v1 blocker** — the current dev environment is not macOS. The Rust core
stays cross-platform and the `knaif-llm` trait keeps macOS reachable; only
installers/release is deferred. When it lands: tarball (universal2 if feasible, else
per-arch), dylibs in `lib/`, **signed/notarized** (an Apple Developer account is
available). Apple's single Apple-only inference backend is **Metal** (CPU + Metal
compiled in).

See [`docs/plans/2026-06-17-monorepo-dual-runtime.md`](../../docs/plans/2026-06-17-monorepo-dual-runtime.md) (Phase 9, *Out of Scope*).
