//! Golden parity for documents expansion — the layer below the planner, for this skill.
//!
//! The analogue of `skills/ffmpeg/native/tests/expansion_parity.rs`, where the rendered artefact is
//! the set of **output paths** a plan produces rather than an ffmpeg argv: documents ops run
//! in-process, so `preview` derives paths instead of building a command line. Runs
//! `contracts/parity/documents_expansion_cases.json` — the identical fixtures the Python side uses
//! (`python/core/tests/test_documents_expansion_parity.py`). **No model is involved**: expansion is
//! deterministic given a plan, so this gates every PR alongside the other contracts.
//!
//! **Why this exists.** `run` dispatches documents steps through the same loop as ffmpeg, which
//! until 2026-08-10 executed only the first step of a plan (`steps.first()`, no loop). The corpus
//! has seven multi-step documents rows, so this skill was exposed to exactly the same defect — it
//! simply was not the one reported. `every_plan_step_produces_an_output` is that regression.

use std::path::{Path, PathBuf};

use knaif_skill_documents::run::{preview, Preview};
use serde_json::Value;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn fixtures() -> Value {
    let path = repo_root().join("contracts/parity/documents_expansion_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read documents fixtures")).unwrap()
}

/// Enter the fixture directory, as the CLI does.
///
/// Native resolves a plan's relative paths against the process working directory, and `just parity`
/// passes `--cwd` for exactly this reason. Sandbox is `None` (open mode), mirroring the ffmpeg
/// consumer. Idempotent, so tests sharing a process may each call it.
fn enter_fixture_dir(doc: &Value) -> PathBuf {
    let sandbox = repo_root().join(doc["sandbox"].as_str().expect("sandbox key"));
    std::env::set_current_dir(&sandbox).expect("enter fixture dir");
    sandbox
}

/// Output basenames in production order, deduplicated — mirrors the Python consumer's `_outputs`.
///
/// Python expands each intent into inspect/run/verify sub-steps that repeat the same path, so
/// first-seen order is what identifies the artefacts a plan actually produces. Native's `preview`
/// returns them per step directly; deduplicating both sides keeps the comparable surface identical.
fn produced(plan: &Value, base: &Path, bundle: &Path) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for step in plan["plan"].as_array().expect("plan array") {
        let tool = step["tool"].as_str().unwrap();
        let empty = serde_json::Map::new();
        let args = step["args"].as_object().unwrap_or(&empty);
        match preview(tool, args, base, None, bundle).expect("preview") {
            Preview::Write { outputs, .. } => {
                for o in outputs {
                    let base = o
                        .file_name()
                        .expect("output has a filename")
                        .to_string_lossy()
                        .to_string();
                    if !out.contains(&base) {
                        out.push(base);
                    }
                }
            }
            Preview::Read(_) => panic!("unexpected read tool while rendering {tool}"),
        }
    }
    out
}

fn expected(case: &Value) -> Vec<String> {
    case["expected_outputs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect()
}

#[test]
fn documents_expansion_cases_match_python() {
    let doc = fixtures();
    let bundle = repo_root().join("skills/documents");
    let base = enter_fixture_dir(&doc);

    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let got = produced(&case["plan"], &base, &bundle);
        assert_eq!(
            got,
            expected(case),
            "case {name}: produced outputs diverge from Python"
        );
    }
}

#[test]
fn every_plan_step_produces_an_output() {
    // The dropped-step regression, stated as an invariant rather than a golden: it survives a
    // legitimate change to any individual op's output naming.
    let doc = fixtures();
    let bundle = repo_root().join("skills/documents");
    let base = enter_fixture_dir(&doc);

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let steps = case["plan"]["plan"].as_array().unwrap().len();
        let got = produced(&case["plan"], &base, &bundle);
        assert_eq!(
            got.len(),
            steps,
            "case {name}: {steps} plan steps produced {} outputs — a step was dropped or duplicated",
            got.len()
        );
    }
}

#[test]
fn chain_intermediates_are_threaded() {
    // Step N's output must be step N+1's declared input, or the "chain" is unrelated ops that
    // happen to run in sequence.
    let doc = fixtures();
    let bundle = repo_root().join("skills/documents");
    let base = enter_fixture_dir(&doc);

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let steps = case["plan"]["plan"].as_array().unwrap();
        if steps.len() < 2 {
            continue;
        }
        let outs = produced(&case["plan"], &base, &bundle);
        for (i, later) in steps[1..].iter().enumerate() {
            let declared = later["args"]["input"].as_str().unwrap_or_default();
            let declared = Path::new(declared)
                .file_name()
                .map(|f| f.to_string_lossy().to_string())
                .unwrap_or_default();
            assert_eq!(
                declared,
                outs[i],
                "case {name}: step {} takes {declared:?} but step {} produced {:?} — the chain is \
                 not threaded",
                i + 2,
                i + 1,
                outs[i]
            );
        }
    }
}
