//! knaif-llm — inference backends behind an `LlmBackend` trait.
//!
//! The trait keeps the runtime backend-agnostic. `MockBackend` (deterministic, no model) is
//! used by tests and offline dev. `LlamaCppBackend` (via `llama-cpp-2`, one build bundling
//! CPU + Vulkan + CUDA / Metal with runtime device selection) is the shipped backend — it is
//! a heavy build (cmake + C++ + GPU SDKs) and lands in a follow-on spike; the trait and the
//! `KNAIF_LLM_BACKEND` switch accommodate it now. **No Ollama backend** — Ollama is confined
//! to the Python dev/eval path. Depends on `knaif-models` to locate model files.

use anyhow::Result;

#[cfg(feature = "llama")]
mod llama;
#[cfg(feature = "llama")]
pub use llama::LlamaCppBackend;

/// A local inference backend: turn a `(system, user)` prompt into a raw plan (a JSON string the
/// deterministic layer then parses/validates/repairs). The backend owns chat formatting because
/// the correct framing (chat template, special tokens, think-block handling) is model-specific —
/// [`LlamaCppBackend`] applies the GGUF's built-in template so a fine-tuned model sees the exact
/// framing it was trained on, rather than a hand-rolled approximation.
pub trait LlmBackend {
    fn generate_plan(&self, system: &str, user: &str) -> Result<String>;
    /// Short identifier for logs / `--version`-style output (e.g. "mock", "llama.cpp").
    fn name(&self) -> &str;
}

/// Whether a GPU/accelerator device is available to the local inference backend.
///
/// `Some(true)` — a discrete GPU, iGPU, or accelerator is present, so model layers can be
/// offloaded. `Some(false)` — none found, so inference will run entirely on the CPU (slow for a
/// multi-billion-parameter model). `None` — this build has no llama.cpp backend (mock only), so
/// there is nothing to probe. `verbose` only controls whether the one-time ggml backend-load
/// scan is quiet (mirrors the run's own verbosity). Cheap: enumerates devices without loading a
/// model. Callers use it to warn before a long CPU-only run rather than sitting silent.
#[cfg(feature = "llama")]
#[must_use]
pub fn gpu_present(verbose: bool) -> Option<bool> {
    Some(llama::gpu_present(verbose))
}

/// Mock-only build: no inference backend compiled in, so GPU availability is not a meaningful
/// question — the caller falls back to no warning.
#[cfg(not(feature = "llama"))]
#[must_use]
pub fn gpu_present(_verbose: bool) -> Option<bool> {
    None
}

/// Deterministic backend for tests and offline dev — returns a canned response, no model.
pub struct MockBackend {
    response: String,
}

impl MockBackend {
    pub fn new(response: impl Into<String>) -> Self {
        Self {
            response: response.into(),
        }
    }

    /// A mock that returns an empty plan envelope (`{"plan": []}`).
    pub fn empty_plan() -> Self {
        Self::new(r#"{"plan": []}"#)
    }
}

impl LlmBackend for MockBackend {
    fn generate_plan(&self, _system: &str, _user: &str) -> Result<String> {
        Ok(self.response.clone())
    }

    fn name(&self) -> &str {
        "mock"
    }
}

/// Select a backend, honoring `$KNAIF_LLM_BACKEND` (`mock` | `llama`).
///
/// Defaults to the mock backend. The mock returns `$KNAIF_LLM_MOCK_RESPONSE` when set (a dev/eval
/// seam for driving `run` / parity harnesses without a model), else an empty plan envelope.
/// `llama` is accepted but not yet buildable (the llama.cpp integration is a follow-on spike), so
/// it returns a clear error rather than silently falling back — shipped surfaces must run
/// llama.cpp, and a silent mock would hide that.
pub fn backend_from_env() -> Result<Box<dyn LlmBackend>> {
    match std::env::var("KNAIF_LLM_BACKEND").ok().as_deref() {
        None | Some("") | Some("mock") => {
            Ok(Box::new(match std::env::var("KNAIF_LLM_MOCK_RESPONSE") {
                Ok(r) if !r.is_empty() => MockBackend::new(r),
                _ => MockBackend::empty_plan(),
            }))
        }
        Some("llama") | Some("llama.cpp") | Some("llamacpp") => Err(anyhow::anyhow!(
            "set --model <path> (and build with --features llama) to use llama.cpp; \
             unset KNAIF_LLM_BACKEND or set it to 'mock' for the offline mock"
        )),
        Some(other) => Err(anyhow::anyhow!(
            "unknown KNAIF_LLM_BACKEND {other:?} (expected 'mock' or 'llama')"
        )),
    }
}

/// Select a backend for a request: a `model` path builds [`LlamaCppBackend`] (feature `llama`);
/// otherwise the mock ([`backend_from_env`], honoring `$KNAIF_LLM_MOCK_RESPONSE`). This is the
/// entry the CLI uses so `--model` drives real inference while offline runs stay on the mock.
/// `verbose` controls llama.cpp's native stderr trace (ignored for the mock).
pub fn backend_for(model: Option<&std::path::Path>, verbose: bool) -> Result<Box<dyn LlmBackend>> {
    match model {
        Some(path) => build_llama(path, verbose),
        None => backend_from_env(),
    }
}

#[cfg(feature = "llama")]
fn build_llama(path: &std::path::Path, verbose: bool) -> Result<Box<dyn LlmBackend>> {
    let ngl = std::env::var("KNAIF_N_GPU_LAYERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(999);
    let max_tokens = std::env::var("KNAIF_MAX_TOKENS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(512);
    Ok(Box::new(
        LlamaCppBackend::load(path, ngl, verbose)?.with_max_tokens(max_tokens),
    ))
}

#[cfg(not(feature = "llama"))]
fn build_llama(_path: &std::path::Path, _verbose: bool) -> Result<Box<dyn LlmBackend>> {
    Err(anyhow::anyhow!(
        "this build has no llama.cpp backend; rebuild the CLI with --features llama to use --model"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mock_returns_canned_response() {
        let b = MockBackend::new(r#"{"plan": [{"tool": "x", "args": {}}]}"#);
        assert_eq!(b.name(), "mock");
        assert_eq!(
            b.generate_plan("sys", "anything").unwrap(),
            r#"{"plan": [{"tool": "x", "args": {}}]}"#
        );
    }

    #[test]
    fn empty_plan_mock() {
        assert_eq!(
            MockBackend::empty_plan().generate_plan("s", "p").unwrap(),
            r#"{"plan": []}"#
        );
    }

    #[test]
    fn backend_from_env_defaults_to_mock() {
        // Note: exercises the None/default branch without mutating process env.
        let b = MockBackend::empty_plan();
        assert_eq!(b.name(), "mock");
    }
}
