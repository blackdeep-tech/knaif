//! Golden parity: whether a request asks for many files, over
//! `contracts/parity/many_files_cases.json`. Decides whether the per-file batch rule and its
//! examples join a prompt. Python: `python/core/tests/test_many_files_parity.py`.

use std::path::Path;

use knaif_core::nl_clarify_gate::asks_for_many_files;
use serde_json::Value;

#[test]
fn asks_for_many_files_matches_the_contract() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/many_files_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let utterance = case["utterance"].as_str().unwrap();
        assert_eq!(
            asks_for_many_files(utterance),
            case["many"].as_bool().unwrap(),
            "{utterance}"
        );
    }
}
