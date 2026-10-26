//! Golden parity: the batch failure line and end summary over
//! `contracts/parity/batch_report_cases.json`. Python:
//! `python/core/tests/test_batch_report_parity.py`.

use std::path::Path;

use knaif_core::batch_chain::{failure_line, summary_line};
use serde_json::Value;

fn doc() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/batch_report_cases.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn summary_lines_match_the_contract() {
    let doc = doc();
    let cases = doc["summary"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let got = summary_line(
            case["done"].as_u64().unwrap() as usize,
            case["failed"].as_u64().unwrap() as usize,
            case["seconds"].as_f64().unwrap(),
            case["bytes"].as_u64().unwrap(),
        );
        assert_eq!(got, case["line"].as_str().unwrap());
    }
}

#[test]
fn failure_lines_match_the_contract() {
    let doc = doc();
    let cases = doc["failure"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let got = failure_line(
            case["file"].as_str().unwrap(),
            case["reason"].as_str().unwrap(),
        );
        assert_eq!(got, case["line"].as_str().unwrap());
    }
}
