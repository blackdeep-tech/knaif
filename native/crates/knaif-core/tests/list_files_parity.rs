//! Golden parity: the one folder listing, over `contracts/parity/list_files_cases.json`.
//! Python: `python/core/tests/test_list_files_parity.py`.

use std::path::Path;

use knaif_core::batch_chain::list_files;
use serde_json::Value;

#[test]
fn list_files_matches_the_contract() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/list_files_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let base = std::env::temp_dir().join(format!(
            "knaif-list-files-{}-{}",
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
        let got = list_files(
            &base.join(case["folder"].as_str().unwrap()),
            case["pattern"].as_str().unwrap(),
            case["recursive"].as_bool().unwrap(),
        );
        let rels: Vec<Value> = got
            .iter()
            .map(|(rel, _)| Value::from(rel.clone()))
            .collect();
        let all_files = got.iter().all(|(_, p)| p.is_file());
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(Value::Array(rels), case["expected"], "{name}");
        assert!(all_files, "{name}: a listed path is not a file");
    }
}
