//! Golden parity: run `contracts/parity/scratch_routing_cases.json`. through the Rust router and
//! assert the recorded routed plan. The Python side runs the identical fixtures
//! (python/core/tests/test_scratch_routing_parity.py); both must agree.
//!
//! Routing decides which outputs reach the user's folder: an intermediate moves into the run
//! scratch, a deliverable or a file the user named stays. If the runtimes disagree, the same
//! utterance leaves different files behind.

use std::path::Path;

use knaif_core::scratch::route_intermediates_to_scratch;
use serde_json::Value;

fn fixtures() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/scratch_routing_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read fixtures")).unwrap()
}

/// Absolute scratch paths back to the fixture's `<work>/…` spelling, `/`-separated.
fn placeholders(value: &Value, work: &str) -> Value {
    match value {
        Value::String(s) => match s.strip_prefix(work) {
            Some(rest) => Value::String(format!("<work>{}", rest.replace('\\', "/"))),
            None => value.clone(),
        },
        Value::Array(items) => Value::Array(items.iter().map(|v| placeholders(v, work)).collect()),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| (k.clone(), placeholders(v, work)))
                .collect(),
        ),
        _ => value.clone(),
    }
}

#[test]
fn scratch_routing_matches_the_contract() {
    let doc = fixtures();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");

    let tmp = std::env::temp_dir().join(format!("knaif-routing-parity-{}", std::process::id()));
    let sandbox = tmp.join("sandbox");
    std::fs::create_dir_all(&sandbox).unwrap();
    let work = tmp.join("work");
    let joining: std::collections::HashSet<String> = doc["joining_tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();

    for case in cases {
        let name = case["name"].as_str().unwrap();
        let plan = case["plan"].as_array().unwrap();
        let routed = route_intermediates_to_scratch(
            plan,
            &work,
            case["utterance"].as_str(),
            &sandbox,
            &joining,
        );
        let got = placeholders(&Value::Array(routed), &work.to_string_lossy());
        assert_eq!(got, case["expected"], "{name}");
    }
    let _ = std::fs::remove_dir_all(&tmp);
}
