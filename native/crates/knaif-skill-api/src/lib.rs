//! knaif-skill-api — the contract native skills implement (Phase 7).
//!
//! `HandlerContext`, the `Step` / `Intent` trait equivalents, and sandbox helpers, mirroring
//! the Python `handler_api` / `tool` modules. Kept clean enough that a future WASM host is a
//! backend, not a rewrite. `HandlerContext`/`Step`/`Intent` are still a skeleton (see
//! docs/audits/2026-09-07-core-principles-and-rtx5080.md, F11); `sandbox` is real — every
//! native skill that needs a filesystem-aware containment check should use it rather than
//! keep its own lexical-only copy (F4).

pub mod sandbox;
