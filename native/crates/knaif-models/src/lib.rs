//! knaif-models — the shared model store and manifest.
//!
//! Deliberately has **no inference dependencies** so a model-management UI can embed it
//! without linking llama.cpp. `knaif-llm` depends on this crate to locate model files; the
//! CLI and future UIs call the same `ModelStore` API — no per-app download code.

pub mod fetcher;
pub mod manifest;
pub mod store;

pub use fetcher::HttpFetcher;
pub use manifest::{Manifest, ModelSpec, Recommendations};
pub use store::{backends_dir, store_dir, Fetcher, ModelEntry, ModelStore, VerifyOutcome};
