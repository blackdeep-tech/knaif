//! knaif-models — the shared model store, backend store, and their manifests.
//!
//! Deliberately has **no inference dependencies** so a model-management UI can embed it
//! without linking llama.cpp. `knaif-llm` depends on this crate to locate model files and to
//! decide whether an installed backend payload belongs to the running build; the CLI and future
//! UIs call the same `ModelStore` / `BackendStore` APIs — no per-app download code.

pub mod backend_manifest;
pub mod backend_store;
pub mod fetcher;
pub mod manifest;
pub mod store;

pub use backend_manifest::{
    current_platform, BackendFile, BackendManifest, BackendPlatform, BackendRequires, BackendSpec,
    PublishStatus,
};
pub use backend_store::{
    backend_dir_state, BackendDirState, BackendEntry, BackendProgress, BackendState, BackendStore,
};
pub use fetcher::HttpFetcher;
pub use manifest::{Manifest, ModelSpec, Recommendations};
pub use store::{backends_dir, store_dir, Fetcher, ModelEntry, ModelStore, VerifyOutcome};
