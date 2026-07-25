//! Golden parity: run `contracts/parity/planner_cases.json` through the Rust deterministic
//! pipeline (parse → normalize → apply_defaults → validate) and assert the recorded
//! valid/invalid outcome + error substring. The Python side runs the identical fixtures
//! (python/core/tests/test_planner_parity.py); both must agree.

use std::path::Path;

use knaif_core::registry::load_registry_str;
use knaif_core::{apply_defaults, normalize_plan, parse_plan, validate_plan};
use serde_json::Value;

#[test]
fn planner_parity_cases() {
    let fixtures =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/planner_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();
    let registries = doc["registries"].as_object().unwrap();
    let cwd = std::env::current_dir().unwrap();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = registries[case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry =
            load_registry_str(reg_yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));

        let plan_str = serde_json::to_string(&case["plan"]).unwrap();
        let mut payload = parse_plan(&plan_str).unwrap_or_else(|e| panic!("{name}: parse {e}"));
        normalize_plan(&mut payload, Some(&registry));
        apply_defaults(&mut payload, &registry);
        let result = validate_plan(&payload, &registry, &cwd, None);

        let expect_valid = case["valid"].as_bool().unwrap();
        assert_eq!(result.is_ok(), expect_valid, "case {name}: got {result:?}");
        if !expect_valid {
            if let Some(sub) = case.get("error_contains").and_then(Value::as_str) {
                let err = result.unwrap_err().to_string();
                assert!(
                    err.contains(sub),
                    "case {name}: error {err:?} missing {sub:?}"
                );
            }
        }
    }
}
