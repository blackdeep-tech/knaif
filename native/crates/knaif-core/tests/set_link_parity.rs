//! Golden parity: pointing readers at a one-file-into-many set, over
//! `contracts/parity/set_link_cases.json`. Python:
//! `python/core/tests/test_set_link_parity.py`.

use std::collections::BTreeMap;
use std::path::Path;

use knaif_core::batch_chain::{link_set_readers, PlannedSet};
use serde_json::Value;

#[test]
fn link_set_readers_matches_the_contract() {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/set_link_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let sets: BTreeMap<usize, PlannedSet> = case["sets"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| {
                let files = v["files"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|f| f.as_str().unwrap().to_string())
                    .collect();
                let set = PlannedSet {
                    name: v["name"].as_str().unwrap().to_string(),
                    files,
                };
                (k.parse().unwrap(), set)
            })
            .collect();
        let got = link_set_readers(case["plan"].as_array().unwrap(), &sets);
        assert_eq!(Value::Array(got), case["expected"], "{name}");
    }
}
