//! Golden parity for intent expansion and command rendering — the layer below the planner.
//!
//! Renders the fixed plans in `contracts/parity/expansion_cases.json` and asserts the ffmpeg argv
//! matches what the Python reference produces (`python/core/tests/test_expansion_parity.py` runs
//! the identical fixtures). **No model is involved**: expansion is deterministic given a plan, so
//! this gates every PR alongside the other contracts.
//!
//! **Why this exists.** Plan-envelope parity is blind to this layer. On 2026-08-10 both runtimes
//! produced the same correct two-step plan for "cut … then extract the audio" and native's `run`
//! rendered only the first step — `steps.first()`, no loop — so the audio was never produced,
//! while every prompt- and plan-level measurement stayed green. `every_plan_step_renders_a_command`
//! is that regression.

use knaif_skill_ffmpeg::run::{expand_dry_run, Expansion};
use knaif_skill_ffmpeg::FfmpegData;
use serde_json::Value;
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn fixtures() -> Value {
    let path = repo_root().join("contracts/parity/expansion_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read expansion fixtures")).unwrap()
}

/// Reduce a path-like token to its basename, mirroring `scripts/parity_check.py::canon_token`.
///
/// Native renders relative paths and Python resolves them against the sandbox; both name the same
/// file, while a genuinely different filename or extension still diverges.
fn canon_token(tok: &str) -> String {
    let tok = tok.replace('\\', "/");
    let has_ext = tok
        .rsplit('/')
        .next()
        .and_then(|b| b.rsplit_once('.'))
        .is_some_and(|(_, ext)| {
            (1..=5).contains(&ext.len()) && ext.chars().all(char::is_alphanumeric)
        });
    if tok.contains('/') || has_ext {
        tok.rsplit('/').next().unwrap_or(&tok).to_string()
    } else {
        tok
    }
}

/// Enter the fixture directory, as the CLI does.
///
/// Native resolves a plan's relative paths against the process working directory — `run` is
/// invoked from the directory holding the files, and `just parity` passes `--cwd` for exactly this
/// reason. Every test here sets the same directory, so doing it per test is idempotent and safe
/// even though the tests share a process.
fn enter_fixture_dir() -> PathBuf {
    let doc_sandbox = repo_root().join("sandbox/fixtures/ffmpeg");
    std::env::set_current_dir(&doc_sandbox).expect("enter fixture dir");
    doc_sandbox
}

/// Render every step of a plan, in order — what `run` does.
fn render(plan: &Value, data: &FfmpegData, sandbox: Option<&Path>) -> Vec<Vec<String>> {
    let mut out = Vec::new();
    for step in plan["plan"].as_array().expect("plan array") {
        let tool = step["tool"].as_str().unwrap();
        let empty = serde_json::Map::new();
        let args = step["args"].as_object().unwrap_or(&empty);
        match expand_dry_run(tool, args, data, sandbox).expect("expand") {
            Expansion::Commands(cmds) => {
                for cmd in cmds {
                    out.push(cmd.iter().map(|t| canon_token(t)).collect());
                }
            }
            Expansion::Clarify(q) => panic!("unexpected clarify while rendering {tool}: {q}"),
        }
    }
    out
}

fn expected(case: &Value) -> Vec<Vec<String>> {
    case["expected_commands"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| {
            c.as_array()
                .unwrap()
                .iter()
                .map(|t| t.as_str().unwrap().to_string())
                .collect()
        })
        .collect()
}

#[test]
fn expansion_cases_match_python() {
    let doc = fixtures();
    let bundle = repo_root().join("skills/ffmpeg");
    let data = FfmpegData::load(&bundle).expect("load ffmpeg data");
    enter_fixture_dir();

    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let got = render(&case["plan"], &data, None);
        assert_eq!(
            got,
            expected(case),
            "case {name}: rendered commands diverge from Python"
        );
    }
}

#[test]
fn every_plan_step_renders_a_command() {
    // The dropped-step regression, stated as an invariant rather than a golden: it survives a
    // legitimate change to any individual command's flags.
    let doc = fixtures();
    let bundle = repo_root().join("skills/ffmpeg");
    let data = FfmpegData::load(&bundle).expect("load ffmpeg data");
    enter_fixture_dir();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let steps = case["plan"]["plan"].as_array().unwrap().len();
        let got = render(&case["plan"], &data, None);
        assert_eq!(
            got.len(),
            steps,
            "case {name}: {steps} plan steps rendered {} commands — a step was dropped",
            got.len()
        );
    }
}

#[test]
fn chain_intermediates_are_threaded() {
    // Step N's output must be step N+1's input, or the "chain" is unrelated commands that happen
    // to run in sequence.
    let doc = fixtures();
    let bundle = repo_root().join("skills/ffmpeg");
    let data = FfmpegData::load(&bundle).expect("load ffmpeg data");
    enter_fixture_dir();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let commands = render(&case["plan"], &data, None);
        for pair in commands.windows(2) {
            let produced = pair[0].last().expect("ffmpeg output is the last token");
            assert!(
                pair[1].contains(produced),
                "case {name}: {produced} is produced by one step but never consumed by the next"
            );
        }
    }
}
