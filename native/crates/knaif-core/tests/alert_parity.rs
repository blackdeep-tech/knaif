//! Golden parity: `contracts/parity/alert_cases.json` through `knaif_core::alert` and
//! `knaif_core::registry::triggered`. The Python side runs the identical fixtures
//! (python/core/tests/test_alert_parity.py): how the pipeline takes the `alert_when_done` run
//! option off a plan, and when a request brings the tool into the prompt.

use std::path::Path;

use knaif_core::alert::split_alert;
use knaif_core::registry::triggered;
use serde_json::{json, Value};

fn fixtures() -> Value {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/alert_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read fixtures")).unwrap()
}

#[test]
fn the_split_matches_the_contract() {
    for case in fixtures()["split"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let (plan, alert) = split_alert(&json!({ "plan": case["plan"] }));
        assert_eq!(plan["plan"], case["expected_plan"], "{name}: plan");
        assert_eq!(
            alert,
            case["expected_alert"].as_bool().unwrap(),
            "{name}: alert"
        );
    }
}

#[test]
fn triggers_match_the_contract() {
    let core = knaif_core::load_registry(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/runtime/core_tools.yaml"),
    )
    .unwrap();
    let triggers = &core["alert_when_done"].triggers;
    assert!(
        !triggers.is_empty(),
        "core_tools.yaml gives alert_when_done its triggers"
    );
    for case in fixtures()["triggers"].as_array().unwrap() {
        let utterance = case["utterance"].as_str().unwrap();
        assert_eq!(
            triggered(utterance, triggers),
            case["expected"].as_bool().unwrap(),
            "{utterance}"
        );
    }
}

#[test]
fn the_alert_moves_last_and_keeps_every_other_steps_order() {
    let mut plan = json!({"plan": [
        {"tool": "alert_when_done", "args": {}},
        {"tool": "trim_video", "args": {"input": "clip.mp4"}},
        {"tool": "resize_video", "args": {"inputs": ["clip_trimmed.mp4"]}},
    ]});
    knaif_core::alert::alert_last(&mut plan);
    let tools: Vec<&str> = plan["plan"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["tool"].as_str().unwrap())
        .collect();
    assert_eq!(tools, ["trim_video", "resize_video", "alert_when_done"]);
}
