//! Golden parity: run `contracts/parity/batch_chain_cases.json`. through the Rust batch chain lift
//! and assert the recorded lifted plan. The Python side runs the identical fixtures
//! (python/core/tests/test_batch_chain_parity.py); both must agree.
//!
//! The per-file rule: the model writes the steps for one file with the batch glob as
//! the first step's input, and core repeats them for every file the glob matched. The lift decides
//! which file every repeated step reads and writes, so the runtimes must agree on it step for
//! step, names included.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use knaif_core::batch_chain::{lift_batch, lift_per_file_chain};
use serde_json::Value;

fn fixtures() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/batch_chain_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read fixtures")).unwrap()
}

#[test]
fn batch_chain_lifting_matches_the_contract() {
    let doc = fixtures();
    let joining: HashSet<String> = doc["joining_tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let batch_outputs: HashMap<String, String> =
        serde_json::from_value(doc["batch_outputs"].clone()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");

    for case in cases {
        let name = case["name"].as_str().unwrap();
        let expand: HashMap<String, Vec<String>> =
            serde_json::from_value(case["expand"].clone()).unwrap();
        let plan = case["plan"].as_array().unwrap();
        let home: HashMap<String, String> = case
            .get("home")
            .map(|h| serde_json::from_value(h.clone()).unwrap())
            .unwrap_or_default();
        let single = case.get("single").and_then(Value::as_bool).unwrap_or(false);
        let lift = lift_batch(plan, &expand, &joining, &home, single, &batch_outputs);
        let record = serde_json::json!({
            "files": lift.files,
            "shown": lift.shown,
            "file_of": lift.file_of,
            "join_files": lift
                .join_files
                .iter()
                .map(|(k, v)| (k.to_string(), serde_json::json!(v)))
                .collect::<serde_json::Map<String, Value>>(),
        });
        assert_eq!(record, case["lift"], "{name}: lift record changed");
        assert_eq!(
            Value::Array(lift.plan.clone()),
            case["expected"],
            "{name}: lifted plan changed (lift_batch)"
        );
        if case.get("home").is_some() || case.get("single").is_some() {
            continue;
        }
        let got = lift_per_file_chain(plan, &expand, &joining, &batch_outputs);
        assert_eq!(
            Value::Array(got),
            case["expected"],
            "{name}: lifted plan changed"
        );
    }
}
