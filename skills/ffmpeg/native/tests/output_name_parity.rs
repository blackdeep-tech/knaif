//! Golden parity: how an ffmpeg step names what it writes, over
//! `contracts/parity/output_name_cases.json` (owner, 2026-10-23). Python:
//! `skills/ffmpeg/python/tests/test_output_name_parity.py`. Each plan is rendered as a dry run in a
//! folder holding the case's (empty) files; the outputs are the last argument of each command.

use knaif_skill_ffmpeg::run::{expand_dry_run, Expansion};
use knaif_skill_ffmpeg::FfmpegData;
use serde_json::Value;
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

#[test]
fn output_names_match_the_contract() {
    let path = repo_root().join("contracts/parity/output_name_cases.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let data = FfmpegData::load(&repo_root().join("skills/ffmpeg")).expect("load ffmpeg data");
    let cases = doc["cases"].as_array().unwrap();
    assert!(!cases.is_empty(), "fixture file has no cases");
    for (i, case) in cases.iter().enumerate() {
        let name = case["name"].as_str().unwrap();
        let base =
            std::env::temp_dir().join(format!("knaif-output-name-{}-{i}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        for f in case["files"].as_array().unwrap() {
            std::fs::write(base.join(f.as_str().unwrap()), b"").unwrap();
        }
        // Relative inputs and outputs resolve against the working folder, as `knaif run` has it.
        std::env::set_current_dir(&base).unwrap();
        // The working folder reads back as its real path: macOS's temp dir `/var/folders/...` is
        // `/private/var/folders/...` once resolved, so an output may carry either form.
        let real_base = std::fs::canonicalize(&base).unwrap_or_else(|_| base.clone());
        let mut outputs: Vec<Value> = Vec::new();
        for step in case["plan"]["plan"].as_array().unwrap() {
            let tool = step["tool"].as_str().unwrap();
            let args = step["args"].as_object().unwrap();
            match expand_dry_run(tool, args, &data, Some(&base)).expect("expand") {
                Expansion::Commands(cmds) => {
                    for cmd in cmds {
                        let out = PathBuf::from(cmd.last().unwrap());
                        let out = if out.is_absolute() {
                            out
                        } else {
                            base.join(out)
                        };
                        let rel = out
                            .strip_prefix(&base)
                            .or_else(|_| out.strip_prefix(&real_base))
                            .unwrap_or(&out)
                            .to_string_lossy()
                            .replace('\\', "/");
                        outputs.push(Value::from(rel));
                    }
                }
                Expansion::Clarify(q) => panic!("{name}: unexpected clarify: {q}"),
            }
        }
        std::env::set_current_dir(repo_root()).unwrap();
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(Value::Array(outputs), case["expected_outputs"], "{name}");
    }
}
