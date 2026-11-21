//! Golden parity: the result set's "leaves" line over `contracts/parity/result_set_cases.json`.
//! Python: `python/core/tests/test_result_set_parity.py`. Every preview ends with
//! this line, so both runtimes must say the same thing about what a plan leaves.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::Path;

use knaif_core::registry::Produces;
use knaif_core::result_set::{leaves_line, result_set, same_for_line};
use serde_json::Value;

fn doc() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/result_set_cases.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn names(v: &Value) -> HashSet<String> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect()
}

#[test]
fn leaves_lines_match_the_contract() {
    let doc = doc();
    let cases = doc["leaves"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let t = &doc["tables"][case["tables"].as_str().unwrap()];
        let produces: HashMap<String, Produces> =
            serde_json::from_value(t["produces"].clone()).unwrap();
        let file_kinds: HashMap<String, String> =
            serde_json::from_value(t["file_kinds"].clone()).unwrap();
        let expand: HashMap<String, Vec<String>> =
            serde_json::from_value(case["expand"].clone()).unwrap();
        let results = result_set(
            case["plan"].as_array().unwrap(),
            "<work>",
            Path::new("."),
            &expand,
            &names(&t["readonly"]),
            &names(&t["joining"]),
            &produces,
            &file_kinds,
            &BTreeMap::new(),
        );
        assert_eq!(
            leaves_line(&results),
            case["line"].as_str().unwrap(),
            "{name}"
        );
    }
}

#[test]
fn same_for_lines_match_the_contract() {
    let doc = doc();
    for case in doc["same_for"].as_array().unwrap() {
        let files: Vec<String> = case["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s.as_str().unwrap().to_string())
            .collect();
        assert_eq!(same_for_line(&files), case["line"].as_str().unwrap());
    }
}
