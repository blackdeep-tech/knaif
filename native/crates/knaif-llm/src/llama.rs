//! `LlamaCppBackend` — real local inference via `llama-cpp-2` (feature = "llama").
//!
//! Greedy decode (argmax) is enough for the deterministic JSON-plan task and keeps the
//! dependency surface minimal (no separate sampler feature). GPU offload is controlled by
//! `n_gpu_layers`; which GPU backend is compiled in is a build-time cargo-feature choice
//! (`cuda`, …) — the generate loop is backend-agnostic.

use std::num::NonZeroU32;
use std::path::Path;

use anyhow::{Context, Result};
use llama_cpp_2::context::params::LlamaContextParams;
use llama_cpp_2::llama_backend::LlamaBackend;
use llama_cpp_2::llama_batch::LlamaBatch;
use llama_cpp_2::model::params::LlamaModelParams;
use llama_cpp_2::model::{AddBos, LlamaChatMessage, LlamaChatTemplate, LlamaModel};
use llama_cpp_2::LlamaBackendDeviceType;

use crate::LlmBackend;

/// Whether any GPU-class ggml device (discrete GPU, iGPU, or accelerator) is available to
/// llama.cpp on this machine. Registers the loadable ggml backends first (in a `dynamic-backends`
/// build, that is what makes the Vulkan/CUDA libs visible), then enumerates the ggml device
/// registry — it does **not** init the backend or load a model, so it is cheap enough to call
/// before every real run. A `false` here means every model layer will run on the CPU (e.g. the
/// AppImage's bundled Vulkan finding no device under WSL2), which is the difference between a
/// snappy run and a minute-plus one.
pub(crate) fn gpu_present(verbose: bool) -> bool {
    load_dynamic_backends(verbose);
    llama_cpp_2::list_llama_ggml_backend_devices()
        .iter()
        .any(|d| {
            matches!(
                d.device_type,
                LlamaBackendDeviceType::Gpu
                    | LlamaBackendDeviceType::IntegratedGpu
                    | LlamaBackendDeviceType::Accelerator
            )
        })
}

/// Register the loadable `ggml-*` backends before any llama.cpp init.
///
/// Only does anything in a `dynamic-backends` build, where each backend (cpu/vulkan/cuda) is a
/// separate shared lib `dlopen`ed at runtime rather than static-linked. Scans, in order: the exe's
/// own directory (where a packaged artifact ships the default CPU+Vulkan backends), then
/// [`knaif_models::backends_dir`] — `$KNAIF_BACKENDS_DIR`, else `~/.knaif/backends`, where the
/// opt-in CUDA payload lands.
///
/// A missing directory is skipped silently — that is the mechanism by which a user *without* the
/// CUDA payload simply never loads it, rather than failing. ggml's registry is process-global, so
/// this runs exactly once.
#[cfg(feature = "dynamic-backends")]
fn load_dynamic_backends(verbose: bool) {
    use std::sync::Once;
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        if !verbose {
            // ggml logs `load_backend: loaded CPU backend from <lib>` during the scan below. That
            // happens before any `LlamaBackend` exists to `void_logs` — and `void_logs` only sets
            // `llama_log_set`, never `ggml_log_set`, so it could not have silenced this anyway.
            // Without this, every dynamic-backends run leaks loader noise onto stderr, regressing
            // the quiet-by-default behaviour. `send_logs_to_tracing` sets both log hooks.
            llama_cpp_2::send_logs_to_tracing(
                llama_cpp_2::LogOptions::default().with_logs_enabled(false),
            );
        }
        for dir in backend_dirs() {
            if dir.is_dir() {
                llama_cpp_2::llama_backend::load_backends_from_path(&dir);
            }
        }
    });
}

/// Static build: every backend compiled in is already registered, so there is nothing to load.
#[cfg(not(feature = "dynamic-backends"))]
fn load_dynamic_backends(_verbose: bool) {}

/// Does `dir` hold any loadable ggml backend lib?
#[cfg(feature = "dynamic-backends")]
fn has_backend_libs(dir: &std::path::Path) -> bool {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return false;
    };
    entries.filter_map(Result::ok).any(|e| {
        e.file_name().to_str().is_some_and(|n| {
            // Linux ships `libggml-*.so`, Windows `ggml-*.dll`; normalise the `lib` prefix so a
            // loadable backend is recognised on both (without it, this returned false on Linux and
            // the dev-fallback BACKENDS_DIR double-loaded every backend). `ggml-base` is the core
            // lib, not a loadable backend, so it never counts.
            let stem = n.strip_prefix("lib").unwrap_or(n);
            stem.starts_with("ggml-") && !stem.starts_with("ggml-base")
        })
    })
}

#[cfg(feature = "dynamic-backends")]
fn backend_dirs() -> Vec<std::path::PathBuf> {
    let mut dirs = Vec::new();
    // The opt-in payload dir goes FIRST, ahead of the artifact's own backends — deliberately.
    //
    // When two backends can drive the same physical GPU, llama.cpp dedupes them by PCI id and keeps
    // whichever registered FIRST ("skipping device CUDA0 … already using device Vulkan1 with the
    // same id"). Scanning the exe dir first therefore let the artifact's bundled Vulkan win and made
    // an installed CUDA payload inert — 619 MB downloaded to change nothing.
    //
    // Ordering picks the *device*; it does not decide whether a stale lib gets loaded, since both
    // dirs are always scanned regardless. A payload that no longer matches this exe is prevented by
    // pinning it to the exe's build when it is installed, not by the loader.
    dirs.push(knaif_models::backends_dir());
    // Then the artifact's own backends, beside the binary.
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|e| e.parent().map(std::path::Path::to_path_buf));
    if let Some(dir) = &exe_dir {
        dirs.push(dir.clone());
    }
    // Dev-only fallback: the build's own `$OUT_DIR/backends`, baked in at compile time by
    // llama-cpp-2's build.rs, so an unstaged `cargo run` still finds backends. Skipped once the
    // exe dir has its own — otherwise a dev build loads every backend TWICE (observed).
    // Never fires for a packaged artifact: the baked path doesn't exist on a user's machine.
    if !exe_dir.as_deref().is_some_and(has_backend_libs) {
        if let Some(dir) = llama_cpp_2::llama_backend::BACKENDS_DIR {
            dirs.push(std::path::PathBuf::from(dir));
        }
    }
    dirs
}

pub struct LlamaCppBackend {
    backend: LlamaBackend,
    model: LlamaModel,
    /// The GGUF's built-in chat template, applied per request so a fine-tuned model sees the exact
    /// framing it was trained on. `None` for a model without one (falls back to plain ChatML).
    chat_template: Option<LlamaChatTemplate>,
    n_ctx: u32,
    /// (generation threads, prompt-decode threads) — see `resolve_n_threads`.
    n_threads: (i32, i32),
    max_tokens: i32,
}

/// CPU threads for `(generation, prompt_decode)`.
///
/// llama.cpp defaults BOTH to `GGML_DEFAULT_N_THREADS = 4` regardless of the machine, which
/// throttles the CPU path badly. The two phases want different counts, and measuring them on an
/// 8-core/16-thread CPU (Qwen3-4B q4, 3938-token prompt) shows why:
///
/// | threads | prompt decode | generation |
/// |---------|---------------|------------|
/// | 4 (llama.cpp default) | 24 tok/s | 5.5 tok/s |
/// | 8 (physical)          | 54 tok/s | **6.0 tok/s** |
/// | 16 (logical/SMT)      | **70 tok/s** | 5.2 tok/s |
///
/// Prompt decode is compute-bound and scales onto every logical thread; generation is
/// memory-bound and *regresses* when SMT siblings contend for the same cache/bandwidth. So:
/// generation → physical cores, prompt decode → all logical threads.
///
/// Both are harmless when layers are GPU-offloaded (the GPU does the work) and decisive when
/// they aren't. `$KNAIF_N_THREADS` / `$KNAIF_N_THREADS_BATCH` override.
fn resolve_n_threads() -> (i32, i32) {
    fn env_override(key: &str) -> Option<i32> {
        std::env::var(key)
            .ok()
            .and_then(|v| v.parse::<i32>().ok())
            .filter(|n| *n > 0)
    }
    let physical = i32::try_from(num_cpus::get_physical()).unwrap_or(4).max(1);
    let logical = i32::try_from(num_cpus::get())
        .unwrap_or(physical)
        .max(physical);
    (
        env_override("KNAIF_N_THREADS").unwrap_or(physical),
        env_override("KNAIF_N_THREADS_BATCH").unwrap_or(logical),
    )
}

impl LlamaCppBackend {
    /// Load a GGUF model. `n_gpu_layers = 0` is CPU-only; a large value (e.g. 99) offloads
    /// every layer to the selected GPU backend. `verbose = false` silences llama.cpp's native
    /// stderr trace (metadata dump, per-tensor load/repack, context construction); `true` leaves
    /// it at the library's default (everything, unfiltered).
    pub fn load(model_path: &Path, n_gpu_layers: u32, verbose: bool) -> Result<Self> {
        // Must precede `LlamaBackend::init` — ggml only sees backends registered before init.
        load_dynamic_backends(verbose);
        let mut backend = LlamaBackend::init().context("llama.cpp backend init")?;
        if !verbose {
            backend.void_logs();
        }
        let model_params = LlamaModelParams::default().with_n_gpu_layers(n_gpu_layers);
        let t_load0 = std::time::Instant::now();
        let model = LlamaModel::load_from_file(&backend, model_path, &model_params)
            .with_context(|| format!("loading model {}", model_path.display()))?;
        if std::env::var("KNAIF_TIMING")
            .map(|v| !v.is_empty())
            .unwrap_or(false)
        {
            eprintln!(
                "[knaif-timing] model load_from_file = {} ms",
                t_load0.elapsed().as_millis()
            );
        }
        // Grab the model's built-in chat template once (Qwen3 ships one); missing → hand-rolled.
        let chat_template = model.chat_template(None).ok();
        // The planner prompt (tool list + examples) is large; `n_ctx` must hold prompt + output and
        // `n_batch` (set = n_ctx below) must hold the whole prompt in one decode, else llama.cpp
        // asserts `n_tokens_all <= n_batch`. `$KNAIF_N_CTX` overrides.
        let n_ctx = std::env::var("KNAIF_N_CTX")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(8192);
        Ok(Self {
            backend,
            model,
            chat_template,
            n_ctx,
            n_threads: resolve_n_threads(),
            max_tokens: 512,
        })
    }

    /// Format `(system, user)` into the model's prompt string. Uses the GGUF's built-in chat
    /// template when present (so a fine-tuned model gets the exact framing + think-block handling
    /// it was trained on), else a plain ChatML fallback. `/no_think` is appended to the system turn
    /// to disable Qwen3 thinking (the promoted eval config); a leaked `<think>` block is still
    /// stripped downstream by `knaif-core::extract_json`.
    fn format_prompt(&self, system: &str, user: &str) -> Result<String> {
        let system = format!("{system}\n\n/no_think");
        match &self.chat_template {
            Some(tmpl) => {
                let messages = [
                    LlamaChatMessage::new("system".to_string(), system)
                        .map_err(|e| anyhow::anyhow!("building system message: {e}"))?,
                    LlamaChatMessage::new("user".to_string(), user.to_string())
                        .map_err(|e| anyhow::anyhow!("building user message: {e}"))?,
                ];
                self.model
                    .apply_chat_template(tmpl, &messages, true)
                    .map_err(|e| anyhow::anyhow!("applying chat template: {e}"))
            }
            None => Ok(format!(
                "<|im_start|>system\n{system}<|im_end|>\n\
                 <|im_start|>user\n{user}<|im_end|>\n\
                 <|im_start|>assistant\n"
            )),
        }
    }

    #[must_use]
    pub fn with_max_tokens(mut self, max_tokens: i32) -> Self {
        self.max_tokens = max_tokens;
        self
    }
}

impl LlmBackend for LlamaCppBackend {
    fn generate_plan(&self, system: &str, user: &str) -> Result<String> {
        let timing = std::env::var("KNAIF_TIMING")
            .map(|v| !v.is_empty())
            .unwrap_or(false);
        let t_start = std::time::Instant::now();
        let prompt = self.format_prompt(system, user)?;
        // `n_batch = n_ctx` so a large prompt is processed in a single decode (see `load`).
        // `n_threads_batch` drives prompt decode (the batched path) and `n_threads` generation;
        // both must be set explicitly — llama.cpp defaults each to 4 (see `resolve_n_threads`).
        let ctx_params = LlamaContextParams::default()
            .with_n_ctx(NonZeroU32::new(self.n_ctx))
            .with_n_batch(self.n_ctx)
            .with_n_threads(self.n_threads.0)
            .with_n_threads_batch(self.n_threads.1);
        let t_ctx0 = std::time::Instant::now();
        let mut ctx = self
            .model
            .new_context(&self.backend, ctx_params)
            .context("creating llama context")?;
        if timing {
            eprintln!(
                "[knaif-timing] new_context = {} ms",
                t_ctx0.elapsed().as_millis()
            );
        }

        // The chat template already emits the model's start tokens (`<|im_start|>…`), so don't add
        // a BOS on top — Qwen3 has no BOS in its trained framing.
        let tokens = self
            .model
            .str_to_token(&prompt, AddBos::Never)
            .context("tokenizing prompt")?;

        let mut batch = LlamaBatch::new(tokens.len().max(1), 1);
        let last = tokens.len().saturating_sub(1);
        for (i, token) in tokens.iter().enumerate() {
            batch.add(*token, i as i32, &[0], i == last)?;
        }
        let t_dec0 = std::time::Instant::now();
        ctx.decode(&mut batch).context("decoding prompt")?;
        if timing {
            eprintln!(
                "[knaif-timing] prompt_decode ({} tokens) = {} ms",
                tokens.len(),
                t_dec0.elapsed().as_millis()
            );
        }

        let t_gen0 = std::time::Instant::now();
        let mut out_bytes: Vec<u8> = Vec::new();
        let mut n_cur = batch.n_tokens();
        let mut produced = 0;
        while produced < self.max_tokens {
            let next = ctx
                .candidates_ith(batch.n_tokens() - 1)
                .max_by(|a, b| a.logit().total_cmp(&b.logit()))
                .map(|d| d.id())
                .ok_or_else(|| anyhow::anyhow!("no candidate tokens"))?;

            if self.model.is_eog_token(next) {
                break;
            }
            out_bytes.extend(self.model.token_to_piece_bytes(next, 32, false, None)?);

            batch.clear();
            batch.add(next, n_cur, &[0], true)?;
            ctx.decode(&mut batch).context("decoding generated token")?;
            n_cur += 1;
            produced += 1;
        }

        if timing {
            eprintln!(
                "[knaif-timing] generation ({} tokens) = {} ms",
                produced,
                t_gen0.elapsed().as_millis()
            );
            eprintln!(
                "[knaif-timing] generate_plan TOTAL = {} ms",
                t_start.elapsed().as_millis()
            );
        }
        // Decode the full accumulated byte stream once so multi-byte UTF-8 sequences split
        // across tokens are handled correctly.
        Ok(String::from_utf8_lossy(&out_bytes).into_owned())
    }

    fn name(&self) -> &str {
        "llama.cpp"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Inference proof: gated on `$KNAIF_TEST_GGUF` (a path to any local GGUF) so the suite
    /// still passes with the `llama` feature but no model available (CI, other machines).
    /// `$KNAIF_TEST_NGL` sets `n_gpu_layers` (default 0 = CPU; e.g. 99 offloads to the GPU
    /// backend when built with a GPU feature like `cuda`).
    #[test]
    fn inference_produces_text() {
        let Ok(path) = std::env::var("KNAIF_TEST_GGUF") else {
            eprintln!("KNAIF_TEST_GGUF unset — skipping llama.cpp inference proof");
            return;
        };
        let ngl: u32 = std::env::var("KNAIF_TEST_NGL")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        eprintln!("loading {path} with n_gpu_layers={ngl}");
        let backend = LlamaCppBackend::load(Path::new(&path), ngl, true)
            .expect("load model")
            .with_max_tokens(24);
        let out = backend
            .generate_plan("You output JSON.", "Output the JSON object {\"ok\": true}.")
            .expect("generate");
        eprintln!("llama.cpp output (ngl={ngl}): {out:?}");
        assert!(!out.trim().is_empty(), "expected non-empty generation");
    }
}
