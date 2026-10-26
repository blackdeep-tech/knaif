//! Golden parity: the preview of a saved text, over `contracts/parity/text_preview_cases.json`.
//! (owner, 2026-10-23). Python: `skills/documents/python/tests/test_text_preview_parity.py`.

use std::path::Path;

use knaif_skill_documents::text::text_preview;
use serde_json::Value;

#[test]
fn text_preview_matches_the_contract() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/text_preview_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let got: Vec<Value> = text_preview(case["text"].as_str().unwrap())
            .into_iter()
            .map(Value::from)
            .collect();
        assert_eq!(Value::Array(got), case["expected"], "{name}");
    }
}
