//! Golden parity: `contracts/parity/url_inputs_cases.json` through `knaif_core::urls` and the
//! planner. The Python side runs the identical fixtures (python/core/tests/test_url_inputs_parity.py):
//! which values are URLs, which are refused before any request, what a download is called,
//! which content types a skill accepts, and where a URL may stand in a plan.

use std::path::Path;

use knaif_core::registry::load_registry_str;
use knaif_core::urls::{check_content_type, check_url, is_url, url_file_name};
use serde_json::Value;

fn fixtures() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/url_inputs_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read fixtures")).unwrap()
}

#[test]
fn the_url_class_matches_the_contract() {
    for case in fixtures()["url_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let value = case["value"].as_str().unwrap();
        let want_url = case["is_url"].as_bool().unwrap();
        assert_eq!(is_url(value), want_url, "{name}: is_url");
        if !want_url {
            continue;
        }
        match case.get("error_contains").and_then(Value::as_str) {
            Some(phrase) => {
                let err = check_url(value).unwrap_or_else(|| panic!("{name}: accepted"));
                assert!(err.contains(phrase), "{name}: {err:?} lacks {phrase:?}");
            }
            None => assert_eq!(check_url(value), None, "{name}"),
        }
    }
}

#[test]
fn download_names_match_the_contract() {
    for case in fixtures()["file_name_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let got = url_file_name(
            case["url"].as_str().unwrap(),
            case.get("content_type").and_then(Value::as_str),
        );
        assert_eq!(got, case["expected"].as_str().unwrap(), "{name}");
    }
}

#[test]
fn content_types_match_the_contract() {
    for case in fixtures()["content_type_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let accepted: Vec<String> = serde_json::from_value(case["accepted"].clone()).unwrap();
        let got = check_content_type(Some(case["content_type"].as_str().unwrap()), &accepted);
        if case.get("ok").and_then(Value::as_bool) == Some(true) {
            assert_eq!(got, None, "{name}");
        } else {
            let err = got.unwrap_or_else(|| panic!("{name}: accepted"));
            let phrase = case["error_contains"].as_str().unwrap();
            assert!(err.contains(phrase), "{name}: {err:?} lacks {phrase:?}");
        }
    }
}

#[test]
fn validation_matches_the_contract() {
    let doc = fixtures();
    let mut registry = load_registry_str(doc["validation_tools_yaml"].as_str().unwrap()).unwrap();
    let core = knaif_core::load_registry(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/runtime/core_tools.yaml"),
    )
    .unwrap();
    registry.extend(core);
    let root = std::env::temp_dir();
    for case in doc["validation_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let got = knaif_core::planner::validate_plan(&case["plan"], &registry, &root, None);
        if case.get("ok").and_then(Value::as_bool) == Some(true) {
            got.unwrap_or_else(|e| panic!("{name}: {e}"));
        } else {
            let err = got.expect_err(name).to_string();
            let phrase = case["error_contains"].as_str().unwrap();
            assert!(err.contains(phrase), "{name}: {err:?} lacks {phrase:?}");
        }
    }
}
