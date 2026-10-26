//! Golden parity: a batch glob for "every video" covers every video, over
//! `contracts/parity/kind_glob_cases.json`. Python:
//! `python/core/tests/test_kind_glob_parity.py`.

use std::collections::HashMap;
use std::path::Path;

use knaif_core::batch_chain::widen_kind_globs;
use knaif_core::registry::Produces;
use serde_json::Value;

#[test]
fn widen_kind_globs_matches_the_contract() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/kind_glob_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let file_kinds: knaif_core::FileKinds = doc["file_kinds"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
        .collect();
    let batch_kinds: Vec<String> = doc["batch_kinds"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let produces: HashMap<String, Produces> =
        serde_json::from_value(doc["produces"].clone()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let base = std::env::temp_dir().join(format!(
            "knaif-kind-glob-{}-{}",
            std::process::id(),
            name.replace(' ', "_")
        ));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        for f in case["files"].as_array().unwrap() {
            let file = base.join(f.as_str().unwrap());
            std::fs::create_dir_all(file.parent().unwrap()).unwrap();
            std::fs::write(file, b"").unwrap();
        }
        let got = widen_kind_globs(
            case["plan"].as_array().unwrap(),
            case["utterance"].as_str().unwrap(),
            &base,
            &file_kinds,
            &batch_kinds,
            &produces,
        );
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(Value::Array(got), case["expected"], "{name}");
    }
}
