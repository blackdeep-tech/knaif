//! Golden parity: the request words that keep every step's output
//! (`--keep-intermediates`). The Python side runs the identical fixtures
//! (python/core/tests/test_keep_intermediates.py); both must agree, or the same request leaves
//! different files on the two runtimes.

use std::path::Path;

use knaif_core::scratch::wants_everything_kept;
use serde_json::Value;

#[test]
fn keep_request_words_match_the_contract() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/keep_request_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let utterance = case["utterance"].as_str().unwrap();
        let keep = case["keep"].as_bool().unwrap();
        assert_eq!(wants_everything_kept(utterance), keep, "{utterance}");
    }
}
