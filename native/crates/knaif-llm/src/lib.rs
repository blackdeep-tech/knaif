//! knaif-llm — inference backends behind an `LlmBackend` trait.
//!
//! The trait keeps the runtime backend-agnostic. `MockBackend` (deterministic, no model) is
//! used by tests and offline dev. `LlamaCppBackend` (via `llama-cpp-2`, one build bundling
//! CPU + Vulkan + CUDA / Metal with runtime device selection) is the shipped backend — it is
//! **implemented**, behind the `llama` cargo feature, because it is a heavy build (cmake +
//! C++ + GPU SDKs) that the default feature set leaves out; select it at runtime with
//! `KNAIF_LLM_BACKEND`. **No Ollama backend** — Ollama is confined to the Python dev/eval
//! path. Depends on `knaif-models` to locate model files.

use anyhow::Result;

/// Generation budget per call, in tokens.
///
/// Canonical in `contracts/runtime/generation.yaml`, which `models.yaml`, `eval_backends.yaml`
/// and this constant all have to agree with — the drift guard is
/// `native/crates/knaif-llm/tests/generation.rs` and `python/core/tests/test_generation_settings.py`.
/// Declared outside the `llama` feature gate so the guard compiles in a default build.
pub const MAX_TOKENS: i32 = 512;

/// Context window, in tokens. Must hold the whole prompt plus the generated output; `n_batch` is
/// set equal to it so a large planner prompt decodes in a single pass. See [`MAX_TOKENS`] for how
/// this value is held together with the config files.
pub const N_CTX: u32 = 8192;

/// Physical batch — the chunk llama.cpp computes at a time. llama.cpp's own default, set
/// explicitly so a crate bump cannot change the arithmetic of every plan unnoticed. Canonical in
/// `contracts/runtime/generation.yaml` (`n_ubatch`); see
/// `docs/plans/2026-09-23-inference-config-parity.md`.
pub const N_UBATCH: u32 = 512;

/// llama.cpp's `LLAMA_FLASH_ATTN_TYPE_AUTO`: flash attention wherever the device supports it,
/// off otherwise. Forcing it on would run attention on the CPU on a GPU without support, and the
/// shipped binary runs on arbitrary GPUs. Canonical in `contracts/runtime/generation.yaml`.
pub const FLASH_ATTN_AUTO: i32 = -1;

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

/// The key of one model call in the eval lane's result store: the sha256 of the exact prompt,
/// `system`, a NUL byte, then `user`, as UTF-8, in lowercase hex. With greedy decoding the reply
/// is a function of this prompt, the model, the inference stack and the backend, so a stored
/// reply can stand in for the call (plan 2026-10-14-faster-release-evidence, E1). Python's store
/// computes the same key from the prompt dump (`$KNAIF_DUMP_PROMPT`).
#[must_use]
pub fn prompt_key(system: &str, user: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(system.as_bytes());
    h.update([0u8]);
    h.update(user.as_bytes());
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Eval-lane recording (`$KNAIF_LLM_RECORD_DIR`): the wrapped backend answers as always, and each
/// reply is also written to `<dir>/<prompt_key>.txt`, byte for byte. Nothing else changes.
pub struct RecordingBackend {
    inner: Box<dyn LlmBackend>,
    dir: std::path::PathBuf,
}

impl RecordingBackend {
    pub fn new(inner: Box<dyn LlmBackend>, dir: impl Into<std::path::PathBuf>) -> Self {
        Self {
            inner,
            dir: dir.into(),
        }
    }
}

impl LlmBackend for RecordingBackend {
    fn generate_plan(&self, system: &str, user: &str) -> Result<String> {
        let reply = self.inner.generate_plan(system, user)?;
        let key = prompt_key(system, user);
        let target = self.dir.join(format!("{key}.txt"));
        // Greedy decoding repeats itself: an existing reply that differs is a broken assumption,
        // never something to overwrite.
        if let Ok(held) = std::fs::read(&target) {
            if held == reply.as_bytes() {
                return Ok(reply);
            }
            return Err(anyhow::anyhow!(
                "record conflict: prompt {key} already holds a different reply in {}",
                self.dir.display()
            ));
        }
        std::fs::create_dir_all(&self.dir)?;
        // Written whole, then renamed: a reader never sees half a reply.
        let tmp = self.dir.join(format!("{key}.tmp"));
        std::fs::write(&tmp, reply.as_bytes())?;
        std::fs::rename(&tmp, &target)?;
        Ok(reply)
    }

    fn name(&self) -> &str {
        self.inner.name()
    }
}

/// Eval-lane replay (`$KNAIF_LLM_BACKEND=replay`, `$KNAIF_LLM_REPLAY_DIR`): no model; each call is
/// answered with the reply recorded for its exact prompt. A prompt never recorded is an error
/// naming its key, so a miss can never pass as a result.
pub struct ReplayBackend {
    dir: std::path::PathBuf,
}

impl ReplayBackend {
    pub fn new(dir: impl Into<std::path::PathBuf>) -> Self {
        Self { dir: dir.into() }
    }

    /// From the value of `$KNAIF_LLM_REPLAY_DIR`, which replay cannot run without.
    pub fn from_dir_var(dir: Option<&str>) -> Result<Self> {
        match dir {
            Some(d) if !d.is_empty() => Ok(Self::new(d)),
            _ => Err(anyhow::anyhow!(
                "KNAIF_LLM_BACKEND=replay needs KNAIF_LLM_REPLAY_DIR (the recorded replies)"
            )),
        }
    }
}

impl LlmBackend for ReplayBackend {
    fn generate_plan(&self, system: &str, user: &str) -> Result<String> {
        let key = prompt_key(system, user);
        let path = self.dir.join(format!("{key}.txt"));
        std::fs::read(&path)
            .map_err(|_| {
                anyhow::anyhow!(
                    "replay miss: no stored reply for prompt {key} in {}",
                    self.dir.display()
                )
            })
            .and_then(|bytes| Ok(String::from_utf8(bytes)?))
    }

    fn name(&self) -> &str {
        "replay"
    }
}

/// Whether this run replays recorded replies (`$KNAIF_LLM_BACKEND=replay`). The CLI then skips
/// model auto-selection, as for the mock, but keeps the repair retry a real model would get, so
/// a recorded repair reply is served exactly as it was live.
#[must_use]
pub fn replay_selected() -> bool {
    std::env::var("KNAIF_LLM_BACKEND").ok().as_deref() == Some("replay")
}

/// Wrap *backend* for recording when `$KNAIF_LLM_RECORD_DIR` is set; otherwise return it as is.
fn with_recording(backend: Box<dyn LlmBackend>) -> Box<dyn LlmBackend> {
    match std::env::var("KNAIF_LLM_RECORD_DIR") {
        Ok(dir) if !dir.is_empty() => Box::new(RecordingBackend::new(backend, dir)),
        _ => backend,
    }
}

/// Select a backend, honoring `$KNAIF_LLM_BACKEND` (`mock` | `llama` | `replay`).
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
        Some("replay") => Ok(Box::new(ReplayBackend::from_dir_var(
            std::env::var("KNAIF_LLM_REPLAY_DIR").ok().as_deref(),
        )?)),
        Some("llama") | Some("llama.cpp") | Some("llamacpp") => Err(anyhow::anyhow!(
            "set --model <path> (and build with --features llama) to use llama.cpp; \
             unset KNAIF_LLM_BACKEND or set it to 'mock' for the offline mock"
        )),
        Some(other) => Err(anyhow::anyhow!(
            "unknown KNAIF_LLM_BACKEND {other:?} (expected 'mock', 'llama' or 'replay')"
        )),
    }
}

/// Select a backend for a request: a `model` path builds [`LlamaCppBackend`] (feature `llama`);
/// otherwise the mock ([`backend_from_env`], honoring `$KNAIF_LLM_MOCK_RESPONSE`). This is the
/// entry the CLI uses so `--model` drives real inference while offline runs stay on the mock.
/// `verbose` controls llama.cpp's native stderr trace (ignored for the mock).
pub fn backend_for(model: Option<&std::path::Path>, verbose: bool) -> Result<Box<dyn LlmBackend>> {
    let backend = match model {
        Some(path) => build_llama(path, verbose)?,
        None => backend_from_env()?,
    };
    Ok(with_recording(backend))
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
        .unwrap_or(MAX_TOKENS);
    Ok(Box::new(
        LlamaCppBackend::load(path, ngl, verbose)?
            .with_max_tokens(max_tokens)
            .with_prefix_reuse(prefix_reuse_enabled()),
    ))
}

/// Whether to reuse the processed start of the previous prompt (`$KNAIF_PREFIX_REUSE=1`).
/// Off unless asked for: see `LlamaCppBackend::with_prefix_reuse`.
#[cfg(feature = "llama")]
fn prefix_reuse_enabled() -> bool {
    matches!(
        std::env::var("KNAIF_PREFIX_REUSE").ok().as_deref(),
        Some("1" | "true" | "on")
    )
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

    // -- record / replay: the eval lane's result store --

    fn scratch(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("knaif-llm-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn the_prompt_key_is_the_sha256_of_system_nul_user() {
        // Python's store computes the same key from the prompt dump:
        // hashlib.sha256(b"s\x00u").hexdigest()
        assert_eq!(
            prompt_key("s", "u"),
            "f30a8c69aa63889b1c10206f9668943724ee24dff65dcd7790e7d41426d3031e"
        );
        assert_ne!(
            prompt_key("ab", "c"),
            prompt_key("a", "bc"),
            "the boundary is part of it"
        );
    }

    #[test]
    fn recording_keeps_the_reply_and_writes_it_under_its_prompt() {
        let dir = scratch("record");
        let b = RecordingBackend::new(Box::new(MockBackend::new("reply one")), &dir);
        assert_eq!(b.generate_plan("sys", "user").unwrap(), "reply one");
        assert_eq!(b.name(), "mock");
        let stored =
            std::fs::read_to_string(dir.join(format!("{}.txt", prompt_key("sys", "user"))));
        assert_eq!(stored.unwrap(), "reply one");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_reply_recorded_again_must_be_the_same_reply() {
        // Greedy decoding repeats itself; a different reply for the same prompt is the very
        // nondeterminism the store rests on not having, so it fails rather than overwrites.
        let dir = scratch("conflict");
        RecordingBackend::new(Box::new(MockBackend::new("first")), &dir)
            .generate_plan("sys", "user")
            .unwrap();
        let same = RecordingBackend::new(Box::new(MockBackend::new("first")), &dir);
        assert_eq!(same.generate_plan("sys", "user").unwrap(), "first");
        let other = RecordingBackend::new(Box::new(MockBackend::new("second")), &dir);
        let msg = other.generate_plan("sys", "user").unwrap_err().to_string();
        assert!(msg.contains("record conflict"), "{msg}");
        let kept = std::fs::read_to_string(dir.join(format!("{}.txt", prompt_key("sys", "user"))));
        assert_eq!(
            kept.unwrap(),
            "first",
            "the stored reply is never overwritten"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn replay_serves_a_recorded_reply_byte_for_byte() {
        let dir = scratch("replay");
        let reply = "<think>\n</think>\n{\"plan\": [{\"tool\": \"x\", \"args\": {}}]}\r\n";
        RecordingBackend::new(Box::new(MockBackend::new(reply)), &dir)
            .generate_plan("sys", "user")
            .unwrap();
        let replay = ReplayBackend::new(&dir);
        assert_eq!(replay.name(), "replay");
        assert_eq!(replay.generate_plan("sys", "user").unwrap(), reply);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_prompt_never_recorded_is_a_loud_miss() {
        let dir = scratch("miss");
        let err = ReplayBackend::new(&dir)
            .generate_plan("sys", "other")
            .unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("replay miss"), "{msg}");
        assert!(msg.contains(&prompt_key("sys", "other")), "{msg}");
    }

    #[test]
    fn replay_needs_its_folder() {
        assert!(ReplayBackend::from_dir_var(None).is_err());
        assert!(ReplayBackend::from_dir_var(Some("")).is_err());
        assert_eq!(
            ReplayBackend::from_dir_var(Some("x")).unwrap().name(),
            "replay"
        );
    }
}
