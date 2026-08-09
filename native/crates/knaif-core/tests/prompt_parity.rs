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
        examples: Vec::new(),
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

/// Path normalization now happens at the **same layer** on both sides, and this pins the rule
/// difference that remains.
///
/// Q5 (2026-08-09) moved `normalize_path_separators` out of `apps/cli` into `knaif-core`, next to
/// `build_prompt_from`, and calls it there — matching Python, whose equivalent lives in
/// `knaif.prompt` and runs inside `build_prompt`. Before that, the CLI normalized but every other
/// consumer of this crate silently did not.
///
/// The *rules* still differ: native rewrites every backslash, Python only whitespace-delimited
/// tokens matching a path-shaped regex. That is not symmetric — Python's rule **misses** a quoted
/// path containing a space, which is the exact failure the function exists to prevent. No corpus
/// utterance has ever exercised either rule (0 backslashes in 847 eval / 404 train utterances), so
/// this test is the only thing measuring it.
#[test]
fn path_normalization_runs_in_the_core_and_pins_the_rule_gap() {
    let doc = fixtures();
    for case in doc["path_normalization_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let raw = case["utterance"].as_str().unwrap();
        let (_, user) = render(&doc, case);

        // Native normalizes inside the core now: no backslash survives into the prompt.
        assert!(
            !user.contains('\\'),
            "case {name}: a backslash reached the prompt; knaif-core should have normalized it"
        );
        assert_eq!(
            user,
            raw.replace('\\', "/"),
            "case {name}: native's rule is replace-every-backslash"
        );

        let py = case["python_normalized_utterance"].as_str().unwrap();
        if name == "quoted_windows_path" {
            assert_ne!(
                user, py,
                "case {name}: expected the runtimes to still disagree — Python's token regex \
                 cannot match a quoted path containing a space, so it leaves the backslashes in"
            );
            assert!(
                py.contains('\\'),
                "the Python side should still carry backslashes here"
            );
        } else {
            assert_eq!(user, py, "case {name}: the two rules agree on this input");
        }
    }
}
