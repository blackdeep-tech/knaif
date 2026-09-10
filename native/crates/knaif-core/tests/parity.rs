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

/// L1b: the retrieval-parity contract. Same utterance + registry -> the same tools in the
/// same order as the reference runtime produced them.
///
/// `#[ignore]` because it **cannot pass today**, and that is deliberate: the contract is
/// authored before the convergence work so the moment it goes green is the moment the port
/// is proven correct. Two things stand in the way, both V1's job:
///
/// 1. `retrieve_tools` returns a `BTreeMap<String, &ToolDef>` — sorted by *name*, so it
///    cannot represent a relevance ranking at all. The signature has to change.
/// 2. Nothing calls it. `apps/cli` builds every prompt from the full registry, which is
///    why native's prompt lists all 13 ffmpeg tools where Python lists 5.
///
/// Un-skip in V1's PR. See docs/plans/2026-09-10-skill-quality-lifecycle.md (L1b, V1).
#[test]
#[ignore = "red until V1 wires retrieval into the CLI and gives it an ordered return type"]
fn retrieval_parity_cases() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/retrieval_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();
    let registries = doc["registries"].as_object().unwrap();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = registries[case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry =
            load_registry_str(reg_yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));

        let top_k = case["top_k"].as_u64().unwrap() as usize;
        let min_score = case["min_score"].as_f64().unwrap();
        let selected = knaif_core::retrieve_tools(
            case["query"].as_str().unwrap(),
            &registry,
            top_k,
            min_score,
        );
        let got: Vec<&str> = selected.keys().map(String::as_str).collect();
        let want: Vec<&str> = case["expected_order"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(got, want, "case {name}: retrieval order differs");
    }
}

/// L2: the clarify-gate parity contract. Given the same `(utterance, plan, registry)` both
/// runtimes must produce the same post-gate payload — the plan with chain intermediates bound
/// to their producer, or a single clarify step.
///
/// Deterministic, so this layer is either 100% or broken; and it matters more than its size
/// suggests, firing on roughly 17% of the ffmpeg corpus. Not `#[ignore]`d: unlike L1 this is
/// expected to pass, and any failure is a live divergence rather than pending work.
#[test]
fn clarify_gate_parity_cases() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/clarify_gate_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();
    let registries = doc["registries"].as_object().unwrap();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = registries[case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry =
            load_registry_str(reg_yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));
        let output_capable = knaif_core::clarify_gate::output_capable_tools(&registry);

        let got = knaif_core::clarify_gate::apply_clarify_gate(
            case["plan"].clone(),
            case["utterance"].as_str().unwrap(),
            &output_capable,
        );
        assert_eq!(
            got, case["expected_payload"],
            "case {name}: post-gate payload differs"
        );
    }
}
