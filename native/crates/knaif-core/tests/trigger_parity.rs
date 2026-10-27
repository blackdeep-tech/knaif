//! L2: when a triggered tool joins the prompt, over `contracts/parity/trigger_cases.json`. The
//! Python side (python/core/tests/test_trigger_parity.py) runs the identical cases.

use std::path::Path;

use knaif_core::registry::triggered;
use serde_json::Value;

#[test]
fn triggered_matches_the_contract() {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/trigger_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    for case in doc["cases"].as_array().unwrap() {
        let utterance = case["utterance"].as_str().unwrap();
        let triggers: Vec<String> = case["triggers"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t.as_str().unwrap().to_string())
            .collect();
        assert_eq!(
            triggered(utterance, &triggers),
            case["expected"].as_bool().unwrap(),
            "{utterance}"
        );
    }
}
