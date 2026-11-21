//! Golden parity: an invented batch folder reads as here, over
//! `contracts/parity/invented_folder_cases.json`. Python:
//! `python/core/tests/test_invented_folder_parity.py`.

use std::path::Path;

use knaif_core::batch_chain::drop_invented_folders;
use serde_json::Value;

#[test]
fn drop_invented_folders_matches_the_contract() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/invented_folder_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let base = std::env::temp_dir().join(format!(
            "knaif-invented-{}-{}",
            std::process::id(),
            name.replace(' ', "_")
        ));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        for d in case["dirs"].as_array().unwrap() {
            std::fs::create_dir_all(base.join(d.as_str().unwrap())).unwrap();
        }
        let got = drop_invented_folders(
            case["plan"].as_array().unwrap(),
            case["utterance"].as_str().unwrap(),
            &base,
        );
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(Value::Array(got), case["expected"], "{name}");
    }
}
