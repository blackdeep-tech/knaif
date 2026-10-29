//! Golden parity: an arg only the request can state (`stated_args`) is left out when the request
//! does not state it. The Python side runs the identical fixtures
//! (python/core/tests/test_stated_args_parity.py).

use std::path::Path;

use knaif_core::nl_clarify_gate::drop_unstated_args;
use serde_json::Value;

#[test]
fn stated_args_match_the_contract() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let doc: Value = serde_json::from_str(
        &std::fs::read_to_string(root.join("contracts/parity/stated_args_cases.json")).unwrap(),
    )
    .unwrap();
    let registry = knaif_core::load_registry(&root.join("skills/ffmpeg/tools.yaml")).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let utterance = case["utterance"].as_str().unwrap();
        let (plan, notes) = drop_unstated_args(&case["plan"], utterance, &registry);
        assert_eq!(plan, case["expect_plan"], "{name}");
        let want: Vec<String> = case["notes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n.as_str().unwrap().to_string())
            .collect();
        assert_eq!(notes, want, "{name}");
    }
}
