//! Golden parity: a glob or a folder expands in natural order, over
//! `contracts/parity/batch_cases.json`. Python:
//! `python/core/tests/test_batch_cases_parity.py` (the fs toolkit's `resolve_inputs`).

use std::path::Path;

use knaif_toolkit_fs::resolve_inputs;
use serde_json::Value;

fn strings(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_array)
        .map(|a| a.iter().map(|s| s.as_str().unwrap().to_string()).collect())
        .unwrap_or_default()
}

#[test]
fn resolve_inputs_matches_the_contract() {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/batch_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = doc["expand"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let base = std::env::temp_dir().join(format!(
            "knaif-batch-cases-{}-{}",
            std::process::id(),
            name.replace(' ', "_")
        ));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        for f in strings(case.get("files")) {
            let file = base.join(&f);
            std::fs::create_dir_all(file.parent().unwrap()).unwrap();
            std::fs::write(file, b"").unwrap();
        }
        let extensions = strings(case.get("extensions"));
        let files = resolve_inputs(
            &strings(case.get("paths")),
            &base,
            Some(&base),
            None,
            &extensions,
            false,
            &strings(case.get("exclude")),
        )
        .unwrap();
        let root = std::fs::canonicalize(&base).unwrap();
        let root = knaif_core::sandbox::resolve_real(&root, &root);
        let got: Vec<String> = files
            .iter()
            .map(|f| {
                knaif_core::sandbox::resolve_real(f, &root)
                    .strip_prefix(&root)
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/")
            })
            .collect();
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(got, strings(case.get("expected")), "{name}");
    }
}
