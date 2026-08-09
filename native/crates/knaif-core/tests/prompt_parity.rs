//! Golden parity: render `contracts/parity/prompt_cases.json` through the Rust `build_prompt` and
//! assert byte-identical `(system, user)` messages against the Python reference, which runs the
//! same fixtures in python/core/tests/test_prompt_parity.py.
//!
//! **Scope.** Logical messages only. Each runtime then applies the GGUF chat template through a
//! different llama.cpp binding, so identical messages are necessary for parity but not proof of an
//! identical token sequence. The divergence that opened this plan was accepted on the strength of
//! a check that did not exist; a green test whose limits are unstated invites that again.
//!
//! R1 of docs/plans/2026-08-08-native-python-planning-parity.md.

use knaif_core::registry::load_registry_str;
use knaif_core::{build_prompt, PromptOverrides};
use serde_json::Value;
use std::path::Path;

fn fixtures() -> Value {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/prompt_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read prompt fixtures")).unwrap()
}

fn overrides_for(doc: &Value, key: &str) -> PromptOverrides {
    let ov = &doc["overrides"][key];
    PromptOverrides {
        system_header: ov
            .get("system_header")
            .and_then(Value::as_str)
            .map(str::to_string),
        examples_block: ov
            .get("examples_block")
            .and_then(Value::as_str)
            .map(str::to_string),
    }
}

fn render(doc: &Value, case: &Value) -> (String, String) {
    let yaml = doc["registries"][case["registry"].as_str().unwrap()]
        .as_str()
        .unwrap();
    let registry = load_registry_str(yaml).expect("registry");
    let overrides = overrides_for(doc, case["overrides"].as_str().unwrap());
    build_prompt(case["utterance"].as_str().unwrap(), &registry, &overrides)
}

#[test]
fn prompt_render_cases_match_python() {
    let doc = fixtures();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no render cases");

    for case in cases {
        let name = case["name"].as_str().unwrap();
        let (system, user) = render(&doc, case);
        assert_eq!(
            system,
            case["expected_system"].as_str().unwrap(),
            "case {name}: system message diverges from Python"
        );
        assert_eq!(
            user,
            case["expected_user"].as_str().unwrap(),
            "case {name}: user message diverges from Python"
        );
    }
}

/// The built-in fallbacks each runtime uses when a skill supplies no `prompt.yaml`.
///
/// The system headers agree byte-for-byte. The **example blocks do not**: Python's
/// `knaif.prompt._EXAMPLES` carries 9 examples, native's `DEFAULT_EXAMPLES` carries 4, and the
/// four missing ones include the `reject` example, both history/`completed:` examples and the
/// variable-binding example.
///
/// Latent rather than shipped: documents, ffmpeg and io each provide a `prompt.yaml` examples
/// block, so no released skill reaches this path. It is live for a newly authored skill and for
/// SDK apps without a `prompt.yaml` — which is precisely the case nobody would think to measure.
///
/// Found by this contract on its first run. Un-ignore when Q reconciles the two defaults.
#[test]
#[ignore = "Q: native DEFAULT_EXAMPLES has 4 entries against Python's 9 (missing reject, both \
            history examples, and variable binding). Latent — every shipped skill overrides it."]
fn default_blocks_match_python() {
    let doc = fixtures();
    for case in doc["default_block_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let (system, user) = render(&doc, case);
        assert_eq!(
            system,
            case["expected_system"].as_str().unwrap(),
            "case {name}: built-in default block diverges from Python"
        );
        assert_eq!(user, case["expected_user"].as_str().unwrap(), "case {name}");
    }
}

#[test]
fn internal_tools_are_never_listed() {
    // `hidden_tool` is in the fixture registry purely so this cannot pass vacuously.
    let doc = fixtures();
    for case in doc["cases"].as_array().unwrap() {
        let (system, _) = render(&doc, case);
        assert!(
            !system.contains("hidden_tool"),
            "case {}: an internal tool reached the prompt",
            case["name"].as_str().unwrap()
        );
    }
}

/// Path normalization happens at a **different layer** on each side, and this test pins that.
///
/// Python's `normalize_path_separators` lives in `knaif/prompt.py` and runs *inside* `build_prompt`.
/// Native's lives in `apps/cli/src/main.rs`, is not exported from `knaif-core`, and runs in the CLI
/// *before* `build_prompt`. So `knaif_core::build_prompt` returns the raw utterance where Python
/// returns a normalized one — meaning any non-CLI consumer of this crate (an embedder, a GUI, the
/// skill API) gets un-normalized input and Python's callers do not.
///
/// Asserted, not ignored: the gap is real today and this records its exact shape. Q5 decides which
/// rule is canonical; whichever wins, this test must be updated deliberately.
#[test]
fn path_normalization_happens_in_the_cli_not_the_core() {
    let doc = fixtures();
    for case in doc["path_normalization_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let raw = case["utterance"].as_str().unwrap();
        let (_, user) = render(&doc, case);

        assert!(
            user.contains(raw),
            "case {name}: knaif-core unexpectedly rewrote the utterance; if normalization moved \
             into the core, update this contract and Q5 with it"
        );

        // Where Python's output differs from the raw input, the two runtimes' build_prompt
        // genuinely disagree at this layer.
        let py = case["python_normalized_utterance"].as_str().unwrap();
        if py != raw {
            assert!(
                !user.contains(py) || py == raw,
                "case {name}: expected a divergence from Python here"
            );
        }
    }
}
