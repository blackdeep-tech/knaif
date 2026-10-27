//! Golden parity: prompt rules joining the system header, over
//! `contracts/parity/prompt_rule_cases.json`. Python:
//! `python/core/tests/test_prompt_rule_parity.py`.

use std::collections::HashSet;
use std::path::Path;

use knaif_core::{apply_prompt_rules, PromptRule};
use serde_json::Value;

#[test]
fn apply_prompt_rules_matches_the_contract() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/prompt_rule_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let rules: Vec<PromptRule> = serde_json::from_value(doc["rules"].clone()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let present: HashSet<String> = case["present"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        let got = apply_prompt_rules(
            case["header"].as_str().unwrap(),
            &rules,
            &present,
            case["utterance"].as_str().unwrap(),
        );
        assert_eq!(got, case["expected"].as_str().unwrap(), "{name}");
    }
}

#[test]
fn the_shipped_prompts_load_their_rules() {
    let skills = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills");
    for skill in ["ffmpeg", "documents"] {
        let overrides = knaif_core::load_prompt_yaml(&skills.join(skill).join("prompt.yaml"));
        assert!(
            overrides
                .rules
                .iter()
                .any(|r| r.when.as_deref() == Some("many")),
            "{skill}: the per-file batch rule"
        );
    }
}
