//! Golden parity: a documents tool's dry run stops on the same bad input as Python's, over
//! `contracts/parity/dry_run_depth_cases.json` (owner, 2026-10-24). The rehearsal
//! before a real run builds each step through `preview_in`, so that is what is checked. Python:
//! `python/core/tests/test_dry_run_depth_parity.py`.

use std::path::{Path, PathBuf};

use knaif_skill_documents::run::preview_in;
use serde_json::Value;

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

#[test]
fn dry_run_depth_matches_the_contract() {
    let doc: Value = serde_json::from_str(
        &std::fs::read_to_string(repo().join("contracts/parity/dry_run_depth_cases.json")).unwrap(),
    )
    .unwrap();
    let protected = repo().join("sandbox/fixtures/documents/sample-protected.pdf");
    if !protected.is_file() {
        eprintln!("SKIPPED: documents fixtures not generated");
        return;
    }
    let bundle = repo().join("skills/documents");
    let mut wrong = Vec::new();
    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let dir =
            std::env::temp_dir().join(format!("knaif_dry_depth_{}_{name}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        if case["input"] == "protected" {
            std::fs::copy(&protected, dir.join("x.pdf")).unwrap();
        } else {
            std::fs::write(dir.join("x.pdf"), doc["broken_bytes"].as_str().unwrap()).unwrap();
        }
        let args = case["args"].as_object().unwrap();
        let passes = preview_in(
            case["tool"].as_str().unwrap(),
            args,
            &dir,
            Some(&dir),
            &bundle,
            None,
        )
        .is_ok();
        if Some(passes) != case["dry_run_passes"].as_bool() {
            wrong.push(format!("{name}: native passes={passes}"));
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
    assert!(
        wrong.is_empty(),
        "differs from Python:\n{}",
        wrong.join("\n")
    );
}
