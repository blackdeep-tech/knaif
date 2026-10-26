//! Golden parity: one file into many, rendered. Renders the fixed plans in
//! `contracts/parity/pieces_expansion_cases.json`. and asserts one ffmpeg command per piece, as the
//! Python reference produces them (`python/core/tests/test_pieces_expansion_parity.py`).

use knaif_skill_ffmpeg::run::{expand_dry_run, Expansion};
use knaif_skill_ffmpeg::FfmpegData;
use serde_json::Value;
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

/// Reduce a path-like token to its basename, mirroring `scripts/parity_check.py::canon_token`.
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

#[test]
fn pieces_render_as_python_renders_them() {
    let path = repo_root().join("contracts/parity/pieces_expansion_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let data = FfmpegData::load(&repo_root().join("skills/ffmpeg")).expect("load ffmpeg data");
    std::env::set_current_dir(repo_root().join(doc["sandbox"].as_str().unwrap())).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let mut got: Vec<Vec<String>> = Vec::new();
        for step in case["plan"]["plan"].as_array().unwrap() {
            let tool = step["tool"].as_str().unwrap();
            let args = step["args"].as_object().unwrap();
            match expand_dry_run(tool, args, &data, None).expect("expand") {
                Expansion::Commands(cmds) => got.extend(
                    cmds.iter()
                        .map(|c| c.iter().map(|t| canon_token(t)).collect()),
                ),
                Expansion::Clarify(q) => panic!("{name}: unexpected clarify: {q}"),
            }
        }
        let want: Vec<Vec<String>> =
            serde_json::from_value(case["expected_commands"].clone()).expect("expected_commands");
        assert_eq!(got, want, "{name}: rendered commands diverge from Python");
    }
}
