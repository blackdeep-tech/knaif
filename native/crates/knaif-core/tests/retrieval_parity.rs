//! Golden parity: run `contracts/parity/retrieval_cases.json` through the Rust retrieval port and
//! assert it selects the same tools as the Python reference. The Python side runs the identical
//! fixtures (python/core/tests/test_retrieval_parity.py).
//!
//! R2 of docs/plans/2026-08-08-native-python-planning-parity.md.
//!
//! **Two tests, not one, and the split is the point.** The tool *set* already agrees; the *order*
//! does not, because `retrieve_tools` returns a `BTreeMap` and throws the ranking away at the
//! return. Python emits retrieved tools in relevance order and `build_prompt` lists them in that
//! order, so an identical set in a different order is still a different prompt. Keeping the order
//! assertion as its own `#[ignore]`d test records exactly what Q1 has to fix and turns it green
//! the moment it does — rather than hiding the gap behind a set comparison that passes today.

use knaif_core::registry::load_registry_str;
use knaif_core::retrieve_tools;
use serde_json::Value;
use std::path::Path;

const ALWAYS_INCLUDE: &[&str] = &["clarify", "reject", "done"];

fn fixtures() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/retrieval_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read retrieval fixtures")).unwrap()
}

/// Run every case, returning `(case_name, ranked_selection, expected_ranked)`.
fn selections() -> Vec<(String, Vec<String>, Vec<String>)> {
    let doc = fixtures();
    let registries = doc["registries"].as_object().unwrap();
    let mut out = Vec::new();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap().to_string();
        let yaml = registries[case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry = load_registry_str(yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));

        let selected = retrieve_tools(
            case["query"].as_str().unwrap(),
            &registry,
            case["top_k"].as_u64().unwrap() as usize,
            case["min_score"].as_f64().unwrap(),
        );
        let ranked: Vec<String> = selected
            .names()
            .filter(|k| !ALWAYS_INCLUDE.contains(k))
            .map(str::to_string)
            .collect();
        let expected: Vec<String> = case["expected_ranked"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        out.push((name, ranked, expected));
    }
    out
}

#[test]
fn retrieval_parity_selects_the_same_tools() {
    let cases = selections();
    assert!(!cases.is_empty(), "fixture file has no cases");

    for (name, ranked, expected) in cases {
        let mut got = ranked.clone();
        let mut want = expected.clone();
        got.sort();
        want.sort();
        assert_eq!(got, want, "case {name}: selected a different tool SET");
    }
}

/// Closed by Q1 (2026-08-09): `retrieve_tools` now returns `RetrievedTools`, which keeps rank
/// order, and `build_prompt_from` emits tools in the order it receives them. This assertion failed
/// with the alphabetical `BTreeMap` ordering before that change — which is the only moment a
/// contract proves it detects the bug it exists for.
#[test]
fn retrieval_parity_preserves_relevance_order() {
    for (name, ranked, expected) in selections() {
        assert_eq!(
            ranked, expected,
            "case {name}: tool ORDER diverges from Python; the prompt differs even though the \
             set matches"
        );
    }
}

#[test]
fn internal_tools_are_never_retrieved() {
    // The fixture's `internal_helper` carries matching keywords precisely so this cannot pass by
    // accident — if the internal filter regressed, this case is the one that would surface it.
    for (name, ranked, _) in selections() {
        assert!(
            !ranked.iter().any(|t| t == "internal_helper"),
            "case {name}: an internal tool reached the retrieved set"
        );
    }
}
