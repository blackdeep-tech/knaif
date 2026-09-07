//! Shared sandbox containment for native skills — re-exports `knaif-core`'s filesystem-aware
//! primitives so every skill crate applies the identical containment rule instead of keeping
//! its own lexical-only copy (that duplication was the exact shape of audit finding F4: three
//! near-identical `lexical_abs`/`assert_in_sandbox` pairs, none of them resolving symlinks or
//! (on Windows) directory junctions). See docs/audits/2026-09-07-core-principles-and-
//! rtx5080.md.

pub use knaif_core::sandbox::{assert_in_sandbox, lexical_normalize, resolve_real};
