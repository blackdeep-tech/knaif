//! `extract_text` saves its text (owner, 2026-10-23), and the L4 lane still has to grade what it
//! read. Under `$KNAIF_DUMP_PLAN` the run writes the read result on stderr, as the read path
//! does, so the documents verifier can check pages and text; the 1.4.0 Windows cells failed every
//! extract row because only the read path wrote it.
//!
//! No model: `KNAIF_LLM_BACKEND=mock` plus `KNAIF_LLM_MOCK_RESPONSE` feeds the runtime the plan.

use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

const MARKER: &str = "===KNAIF-RESULT===";

struct Dir(PathBuf);
impl Dir {
    fn new(tag: &str) -> Self {
        let d = std::env::temp_dir().join(format!("knaif_extract_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("notes.md"), "Alpha line\nBeta line\n").unwrap();
        Self(d)
    }
}
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn extract(dir: &Dir, dump: bool) -> Output {
    let plan = r#"{"plan": [{"tool": "extract_text", "args": {"input": "notes.md"}}]}"#;
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_knaif"));
    cmd.args(["run", "documents", "--sandbox"])
        .arg(&dir.0)
        .args(["--allow", "overwrite", "extract the text of notes.md"])
        .stdin(Stdio::null())
        .env(
            "KNAIF_SKILLS_ROOT",
            concat!(env!("CARGO_MANIFEST_DIR"), "/../../skills"),
        )
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", plan)
        .env_remove("KNAIF_DUMP_PLAN");
    if dump {
        cmd.env("KNAIF_DUMP_PLAN", "1");
    }
    cmd.output().expect("run the knaif binary")
}

fn result_line(stderr: &str) -> Option<serde_json::Value> {
    stderr
        .lines()
        .find_map(|l| l.strip_prefix(MARKER))
        .map(|j| serde_json::from_str(j).expect("the result line is JSON"))
}

#[test]
fn a_saved_extract_writes_its_read_result_for_the_lane() {
    let dir = Dir::new("dump");
    let out = extract(&dir, true);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(out.status.success(), "{stderr}");
    assert!(dir.0.join("notes.txt").is_file(), "the text is still saved");
    let line = result_line(&stderr).unwrap_or_else(|| panic!("no result line:\n{stderr}"));
    assert_eq!(line["tool"], "extract_text");
    assert_eq!(line["result"]["pages"].as_array().map(Vec::len), Some(1));
    let text = line["result"]["text"].as_str().unwrap_or_default();
    assert!(
        text.contains("Alpha line") && text.contains("Beta line"),
        "{line}"
    );
}

#[test]
fn without_the_dump_a_saved_extract_writes_no_result_line() {
    let dir = Dir::new("plain");
    let out = extract(&dir, false);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(out.status.success(), "{stderr}");
    assert!(result_line(&stderr).is_none(), "{stderr}");
}
