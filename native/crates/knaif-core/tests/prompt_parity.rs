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
/// Byte-identity is the wrong contract here, and finding that out was the useful part. Python's
/// `_EXAMPLES` carries 9 examples; **two of them are history examples**, carrying a `completed:`
/// line that demonstrates re-planning from prior steps. This runtime is single-shot and cannot do
/// that, so shipping those two would show the model a capability the runtime lacks.
///
/// So the contract is *Python's block minus its history examples* — 7 — and the assertion is
/// written that way rather than as a string comparison, so a new Python example is inherited
/// automatically while a new history example is still excluded.
///
/// When this contract first ran, native had **4**: it was also missing the `reject` example
/// (safety routing) and the `$var` binding example, neither of which has anything to do with
/// history. Latent, because documents, ffmpeg and io all override the block via `prompt.yaml` —
/// it reaches only a newly authored skill or an SDK app without one, which is exactly the case
/// nobody thinks to measure. Fixed 2026-08-09.
#[test]
fn default_blocks_match_python_minus_history_examples() {
    let doc = fixtures();
    for case in doc["default_block_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let (system, _) = render(&doc, case);

        let expected_system = case["expected_system"].as_str().unwrap();
        let native_examples = examples_of(&system);
        let python_examples: Vec<&str> = examples_of(expected_system)
            .into_iter()
            .filter(|e| !e.contains("completed:"))
            .collect();

        assert_eq!(
            native_examples, python_examples,
            "case {name}: the default example block diverges from Python's (history excluded)"
        );
        assert!(
            !system.contains("completed:"),
            "case {name}: a history example reached a single-shot runtime's default prompt"
        );
        assert!(
            system.contains("\"tool\": \"reject\""),
            "case {name}: the reject example is missing from the default block"
        );
    }
}

/// Split a rendered system message into its `request:`-led example blocks.
fn examples_of(system: &str) -> Vec<&str> {
    let Some(idx) = system.find("Examples:") else {
        return Vec::new();
    };
    system[idx..]
        .split("  request:")
        .skip(1)
        .map(str::trim_end)
        .collect()
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
/// The *rules* converged on 2026-08-09 too: Python adopted native's replace-every-backslash.
/// Its previous rule rewrote only whitespace-delimited path-shaped tokens, which left
/// `convert "C:\My Videos\clip.mov" to mp4` untouched — the exact failure the function exists to
/// prevent, whenever a Windows path contains a space. No corpus utterance has ever contained a
/// backslash (0 of 847 eval, 0 of 404 train), so this test is still the only thing measuring it.
#[test]
fn path_normalization_runs_in_the_core_and_matches_python() {
    let doc = fixtures();
    for case in doc["path_normalization_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let raw = case["utterance"].as_str().unwrap();
        let (_, user) = render(&doc, case);

        // Normalization happens inside the core now, so no backslash survives into the prompt —
        // for every consumer of this crate, not only the CLI.
        assert!(
            !user.contains('\\'),
            "case {name}: a backslash reached the prompt; knaif-core should have normalized it"
        );
        assert_eq!(
            user,
            raw.replace('\\', "/"),
            "case {name}: the rule is replace-every-backslash"
        );
        assert_eq!(
            user,
            case["python_normalized_utterance"].as_str().unwrap(),
            "case {name}: the two runtimes' normalization rules have diverged again"
        );
    }
}
