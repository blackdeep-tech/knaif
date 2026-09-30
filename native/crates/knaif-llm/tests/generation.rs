//! V4: the native half of the generation-settings drift guard.
//!
//! `contracts/runtime/generation.yaml` is the canonical source. This asserts the native
//! defaults match it; `python/core/tests/test_generation_settings.py` asserts `models.yaml`
//! and `eval_backends.yaml` do. Nothing is copied from the contract — its consumers are
//! hand-annotated config files and these constants, and a generator would flatten commentary
//! worth more than the duplication costs. So the contract is enforced by comparison.
//!
//! `temperature` is not checked here: native has no temperature parameter at all. It takes the
//! argmax candidate directly (`llama.rs`, `max_by(logit)`), which is greedy decoding by
//! construction — there is no value that could drift. The contract records 0.0 so the Python
//! side, which does pass a literal, has something to be held to.
//!
//! See docs/plans/2026-09-10-skill-quality-lifecycle.md (V4).

use std::path::Path;

fn settings() -> serde_yaml::Value {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/runtime/generation.yaml");
    let doc: serde_yaml::Value =
        serde_yaml::from_str(&std::fs::read_to_string(&path).expect("read generation.yaml"))
            .expect("parse generation.yaml");
    doc["settings"].clone()
}

#[test]
fn native_defaults_match_the_contract() {
    let s = settings();
    assert_eq!(
        s["max_tokens"].as_i64().expect("max_tokens"),
        i64::from(knaif_llm::MAX_TOKENS),
        "knaif_llm::MAX_TOKENS disagrees with contracts/runtime/generation.yaml"
    );
    assert_eq!(
        s["n_ctx"].as_u64().expect("n_ctx"),
        u64::from(knaif_llm::N_CTX),
        "knaif_llm::N_CTX disagrees with contracts/runtime/generation.yaml"
    );
}

#[test]
fn native_cannot_express_the_settings_it_does_not_read() {
    // `json_mode` and `thinking_enabled` are contract values native honors by having no
    // alternative: it never builds a GBNF grammar, and it appends `/no_think` unconditionally.
    // That is parity only while the contract says false for both — so pin the direction, and
    // make a future `true` fail here rather than silently diverge at inference time.
    let s = settings();
    assert_eq!(
        s["json_mode"].as_bool(),
        Some(false),
        "native has no JSON-grammar path; `json_mode: true` would need a native change first"
    );
    assert_eq!(
        s["thinking_enabled"].as_bool(),
        Some(false),
        "native appends /no_think unconditionally; `thinking_enabled: true` needs a native change"
    );

    let llama = std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/llama.rs"))
        .expect("read llama.rs");
    assert!(
        llama.contains("/no_think"),
        "the /no_think suffix is gone but the contract still says thinking_enabled: false"
    );
}

#[test]
fn native_compute_config_matches_the_contract() {
    // docs/plans/2026-09-23-inference-config-parity.md: identical tokens and greedy decoding on
    // both lanes still flipped 1.2% of eval outcomes, because llama.cpp was configured
    // differently. These are pinned, not inherited, so a crate bump cannot move them silently.
    let s = settings();
    assert_eq!(
        s["n_ubatch"].as_u64().expect("n_ubatch"),
        u64::from(knaif_llm::N_UBATCH),
        "knaif_llm::N_UBATCH disagrees with contracts/runtime/generation.yaml"
    );
    assert_eq!(
        s["n_batch"].as_u64().expect("n_batch"),
        u64::from(knaif_llm::N_CTX),
        "native decodes the prompt in one batch (n_batch = n_ctx); the contract must say so"
    );
    assert_eq!(
        s["flash_attn"].as_str(),
        Some("auto"),
        "native passes llama.cpp's AUTO policy; a different contract value needs a native change"
    );
    assert_eq!(
        knaif_llm::FLASH_ATTN_AUTO,
        -1,
        "llama.h: LLAMA_FLASH_ATTN_TYPE_AUTO = -1"
    );
    assert_eq!(
        s["reset_cache_per_call"].as_bool(),
        Some(true),
        "native builds a fresh context per call; the contract must not allow reuse"
    );

    let llama = std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/llama.rs"))
        .expect("read llama.rs");
    for needle in [
        "with_flash_attention_policy(crate::FLASH_ATTN_AUTO)",
        "with_n_ubatch(crate::N_UBATCH)",
        "with_n_batch(self.n_ctx)",
    ] {
        assert!(llama.contains(needle), "llama.rs no longer sets `{needle}`");
    }
}
