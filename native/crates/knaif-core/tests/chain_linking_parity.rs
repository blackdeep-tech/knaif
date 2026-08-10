//! Golden parity: run `contracts/parity/chain_linking_cases.json` through the Rust chain linker
//! and assert the recorded linked plan. The Python side runs the identical fixtures
//! (python/core/tests/test_chain_linking_parity.py); both must agree.
//!
//! Chain linking rewrites the model's plan *before* validation, so it decides which file each step
//! actually reads. Native shipped only its first pass until 2026-08-10, which made "trim clip.mp4,
//! compress it, and remove the audio" build the silent video from the uncompressed trim — a wrong
//! artefact that every plan-envelope comparison scored as a match, because the envelopes were
//! identical and only the *linked* plans differed.

use std::path::Path;

use knaif_core::clarify_gate::link_chain_intermediates;
use knaif_core::registry::load_registry_str;
use knaif_core::{output_capable_tools, validate_plan};
use serde_json::Value;

fn fixtures() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/chain_linking_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read fixtures")).unwrap()
}

/// A tool is an eligible producer only when its schema *accepts* an `output` arg.
///
/// Pinned separately because it gates both passes: widening it writes `output` onto a tool the
/// validator then rejects with "unsupported args", and narrowing it silently stops linking.
#[test]
fn output_capable_derivation() {
    let doc = fixtures();
    for (reg_name, expected) in doc["expected_output_capable"].as_object().unwrap() {
        let reg_yaml = doc["registries"][reg_name].as_str().unwrap();
        let registry = load_registry_str(reg_yaml).expect("registry");
        let mut got: Vec<String> = output_capable_tools(&registry).into_iter().collect();
        got.sort();
        let want: Vec<&str> = expected
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(got, want, "{reg_name}: producer set changed");
    }
}

#[test]
fn chain_linking_matches_the_contract() {
    let doc = fixtures();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");

    for case in cases {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = doc["registries"][case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry =
            load_registry_str(reg_yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));
        let output_capable = output_capable_tools(&registry);

        let mut plan = case["plan"]["plan"].as_array().unwrap().clone();
        link_chain_intermediates(
            &mut plan,
            case["utterance"].as_str().unwrap(),
            &output_capable,
        );

        assert_eq!(
            Value::Array(plan),
            case["expected"]["plan"],
            "{name}: linked plan changed"
        );
    }
}

/// Linking must never produce a plan the validator rejects.
///
/// The whole point of the output-capable filter is that `output` is only written where the schema
/// declares it; this is the assertion that catches it if that filter is ever widened. Terminal
/// control tools are not in the inline registry, so plans carrying one are covered by the linked-
/// plan assertion above instead.
#[test]
fn linked_plans_still_validate() {
    let doc = fixtures();
    let cwd = std::env::current_dir().unwrap();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = doc["registries"][case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry =
            load_registry_str(reg_yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));
        let steps = case["expected"]["plan"].as_array().unwrap();
        if steps
            .iter()
            .any(|s| !registry.contains_key(s["tool"].as_str().unwrap_or("")))
        {
            continue;
        }
        validate_plan(&case["expected"], &registry, &cwd, None)
            .unwrap_or_else(|e| panic!("{name}: linked plan does not validate: {e}"));
    }
}
