//! Golden parity: run `contracts/parity/effects_cases.json` through the Rust registry loader and the
//! skill-bundle rule. The Python side runs the identical fixtures
//! (python/core/tests/test_effects_parity.py); both must agree on what a declaration means.
//!
//! Python enforces declared effects at run time (`ctx.exec`, `ctx.call_tool`); native validates
//! them when a bundle loads, until a native step context exists to hold them (audit F11).

use std::collections::BTreeMap;
use std::path::Path;

use knaif_core::effects::check_declared;
use knaif_core::registry::load_registry_str;
use serde_json::Value;

fn fixtures() -> Value {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/effects_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read fixtures")).unwrap()
}

fn load(yaml: &str) -> anyhow::Result<knaif_core::Registry> {
    let registry = load_registry_str(yaml)?;
    check_declared(&registry, "tools.yaml")?;
    Ok(registry)
}

#[test]
fn effects_match_the_contract() {
    let doc = fixtures();
    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let yaml = case["tools_yaml"].as_str().unwrap();
        match case.get("error_contains").and_then(Value::as_str) {
            Some(phrase) => {
                let err = load(yaml).expect_err(name).to_string();
                assert!(err.contains(phrase), "{name}: {err:?} lacks {phrase:?}");
            }
            None => {
                let registry = load(yaml).unwrap_or_else(|e| panic!("{name}: {e}"));
                let got: BTreeMap<String, Vec<String>> = registry
                    .iter()
                    .map(|(n, td)| (n.clone(), td.effects.clone().unwrap_or_default()))
                    .collect();
                let want: BTreeMap<String, Vec<String>> =
                    serde_json::from_value(case["expected"].clone()).unwrap();
                assert_eq!(got, want, "{name}");
            }
        }
    }
}

#[test]
fn core_control_tools_need_no_declaration() {
    let core = knaif_core::load_registry(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/runtime/core_tools.yaml"),
    )
    .unwrap();
    assert!(core.values().all(|td| td.effects.is_none()));
}

#[test]
fn every_shipped_skill_declares_its_effects() {
    let skills = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills");
    for skill in ["ffmpeg", "documents"] {
        let registry = knaif_core::load_registry(&skills.join(skill).join("tools.yaml")).unwrap();
        check_declared(&registry, skill).unwrap();
    }
}

#[test]
fn a_skill_bundle_with_an_undeclared_tool_is_refused() {
    let dir = std::env::temp_dir().join(format!("knaif-effects-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(
        dir.join("tools.yaml"),
        "convert:\n  description: Convert.\n  required_args: [input]\n",
    )
    .unwrap();
    let err = knaif_core::load_skill_registry(&dir)
        .expect_err("undeclared")
        .to_string();
    assert!(err.contains("declares no effects"), "{err}");
    let _ = std::fs::remove_dir_all(&dir);
}
