//! Golden parity: a count of files must match the files the plan touches, over
//! `contracts/parity/count_cases.json`. Python:
//! `python/core/tests/test_count_parity.py`.

use std::path::Path;

use knaif_core::nl_clarify_gate::count_question;
use serde_json::Value;

#[test]
fn count_question_matches_the_contract() {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/count_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let file_kinds: knaif_core::FileKinds = doc["file_kinds"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
        .collect();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let base = std::env::temp_dir().join(format!(
            "knaif-count-{}-{}",
            std::process::id(),
            name.replace(' ', "_")
        ));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        for f in case["files"].as_array().unwrap() {
            std::fs::write(base.join(f.as_str().unwrap()), b"").unwrap();
        }
        let got = count_question(
            case["utterance"].as_str().unwrap(),
            case["plan"].as_array().unwrap(),
            &base,
            &file_kinds,
        );
        let _ = std::fs::remove_dir_all(&base);
        let want = case["expected"].as_str().map(str::to_string);
        assert_eq!(got, want, "{name}");
    }
}
