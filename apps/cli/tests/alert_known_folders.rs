//! Completion alerts and known folders on the shipped path: `knaif run` with a plan that writes into a known folder and
//! asks for an alert, through the real binary. No model: `KNAIF_LLM_BACKEND=mock` plus
//! `KNAIF_LLM_MOCK_RESPONSE` feeds the runtime an exact plan. The documents skill converts text to
//! Markdown in-process, so these need no external program.
//!
//! The rules, as in Python (`test_alert.py`, `test_known_folders.py`):
//! - an output whose first segment names a known folder goes into that folder
//!   (`KNAIF_KNOWN_FOLDERS_ROOT` stands in for the user's folders);
//! - a folder of that name in the working folder makes the run ask instead;
//! - `--sandbox` refuses a known folder outside it;
//! - the alert fires once when work ran (`KNAIF_OS_SINK` records it), never in a dry run, never
//!   when nothing ran, and never on stdout.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const REQUEST: &str = "convert sample.txt to markdown, save it in my Documents folder and beep";

fn plan(steps: &str) -> String {
    format!(r#"{{"plan": [{steps}]}}"#)
}

const CONVERT: &str = r#"{"tool": "convert_document", "args": {"input": "sample.txt", "to_format": "md", "output": "Documents/sample.md"}}"#;
const ALERT: &str = r#"{"tool": "alert_when_done", "args": {}}"#;

/// A working folder inside the checkout (the binary finds `contracts/` by walking up from it),
/// holding `sample.txt`, with the known folders and the alert sink beside it. Removed on drop.
struct Work {
    root: PathBuf,
}

impl Work {
    fn new(tag: &str) -> Self {
        let root = Path::new(env!("CARGO_TARGET_TMPDIR"))
            .join(format!("alert_known_folders_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("work")).unwrap();
        std::fs::write(root.join("work").join("sample.txt"), "Invoice Alpha\n").unwrap();
        Self { root }
    }
    fn work(&self) -> PathBuf {
        self.root.join("work")
    }
    fn known(&self) -> PathBuf {
        self.root.join("known")
    }
    fn sink(&self) -> PathBuf {
        self.root.join("alerts.jsonl")
    }
    fn alerts(&self) -> Vec<serde_json::Value> {
        std::fs::read_to_string(self.sink())
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    fn run(&self, payload: &str, flags: &[&str]) -> (String, String, bool) {
        let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
            .args(["run", "documents", "--yes"])
            .args(flags)
            .arg("--")
            .args(REQUEST.split_whitespace())
            .current_dir(self.work())
            .stdin(Stdio::null())
            .env(
                "KNAIF_SKILLS_ROOT",
                concat!(env!("CARGO_MANIFEST_DIR"), "/../../skills"),
            )
            .env("KNAIF_LLM_BACKEND", "mock")
            .env("KNAIF_LLM_MOCK_RESPONSE", payload)
            .env("KNAIF_KNOWN_FOLDERS_ROOT", self.known())
            .env("KNAIF_OS_SINK", self.sink())
            .output()
            .unwrap();
        (
            String::from_utf8_lossy(&out.stdout).into_owned(),
            String::from_utf8_lossy(&out.stderr).into_owned(),
            out.status.success(),
        )
    }
}

impl Drop for Work {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

#[test]
fn the_output_lands_in_the_known_folder_and_the_alert_fires_once() {
    let w = Work::new("done");
    let (stdout, stderr, ok) = w.run(&plan(&format!("{CONVERT}, {ALERT}")), &[]);
    assert!(ok, "stdout: {stdout}\nstderr: {stderr}");
    let out = w.known().join("Documents").join("sample.md");
    assert!(
        out.is_file(),
        "no {out:?}\nstdout: {stdout}\nstderr: {stderr}"
    );
    assert!(
        !w.work().join("Documents").exists(),
        "a local Documents folder was made"
    );
    let alerts = w.alerts();
    assert_eq!(alerts.len(), 1, "{alerts:?}");
    assert_eq!(alerts[0]["outcome"], "done");
    assert!(alerts[0]["message"].as_str().unwrap().starts_with("Done ("));
    assert!(
        !stdout.contains("Done ("),
        "the alert never writes to stdout"
    );
}

#[test]
fn a_dry_run_writes_nothing_and_never_alerts() {
    let w = Work::new("dry");
    let (stdout, stderr, ok) = w.run(&plan(&format!("{CONVERT}, {ALERT}")), &["--dry-run"]);
    assert!(ok, "stdout: {stdout}\nstderr: {stderr}");
    assert!(!w.known().exists());
    assert!(w.alerts().is_empty());
}

#[test]
fn no_alert_asked_means_no_alert() {
    let w = Work::new("quiet");
    let (stdout, stderr, ok) = w.run(&plan(CONVERT), &[]);
    assert!(ok, "stdout: {stdout}\nstderr: {stderr}");
    assert!(w.known().join("Documents").join("sample.md").is_file());
    assert!(w.alerts().is_empty());
}

#[test]
fn a_same_named_local_folder_asks_and_nothing_runs() {
    let w = Work::new("same");
    std::fs::create_dir_all(w.work().join("Documents")).unwrap();
    let (stdout, stderr, ok) = w.run(&plan(&format!("{CONVERT}, {ALERT}")), &[]);
    assert!(ok, "stdout: {stdout}\nstderr: {stderr}");
    let all = format!("{stdout}{stderr}");
    assert!(all.contains("Which one should I save into?"), "{all}");
    assert!(!w.known().exists());
    assert!(std::fs::read_dir(w.work().join("Documents"))
        .unwrap()
        .next()
        .is_none());
    assert!(w.alerts().is_empty(), "nothing ran: no alert");
}

#[test]
fn the_sandbox_refuses_a_known_folder_outside_it() {
    let w = Work::new("sandbox");
    let sandbox = w.work().to_string_lossy().into_owned();
    let (stdout, stderr, ok) = w.run(
        &plan(&format!("{CONVERT}, {ALERT}")),
        &["--sandbox", &sandbox],
    );
    let all = format!("{stdout}{stderr}");
    assert!(!ok, "{all}");
    assert!(all.contains("outside the sandbox"), "{all}");
    assert!(!w.known().exists());
    assert!(w.alerts().is_empty(), "nothing ran: no alert");
}

#[test]
fn an_alert_with_no_work_asks() {
    let w = Work::new("nowork");
    let (stdout, stderr, ok) = w.run(&plan(ALERT), &[]);
    assert!(ok, "stdout: {stdout}\nstderr: {stderr}");
    let all = format!("{stdout}{stderr}");
    assert!(all.contains("there is no task in this request"), "{all}");
    assert!(w.alerts().is_empty());
}

#[test]
fn an_alert_with_args_still_plans_and_alerts() {
    // The model sometimes gives the alert a kind; the tool takes none, and they are ignored
    // rather than sent back as a validation error.
    let w = Work::new("args");
    let alert = r#"{"tool": "alert_when_done", "args": {"kind": "beep"}}"#;
    let (stdout, stderr, ok) = w.run(&plan(&format!("{alert}, {CONVERT}")), &[]);
    assert!(ok, "stdout: {stdout}\nstderr: {stderr}");
    assert!(w.known().join("Documents").join("sample.md").is_file());
    assert_eq!(w.alerts().len(), 1);
}

#[test]
fn a_failure_after_work_started_alerts_failed() {
    let w = Work::new("failed");
    let merge = r#"{"tool": "merge_pdfs", "args": {"inputs": ["sample.txt", "sample.txt"], "output": "merged.pdf"}}"#;
    let (stdout, stderr, ok) = w.run(&plan(&format!("{CONVERT}, {merge}, {ALERT}")), &[]);
    assert!(
        !ok,
        "the merge of two text files must fail\nstdout: {stdout}\nstderr: {stderr}"
    );
    let alerts = w.alerts();
    assert_eq!(
        alerts.len(),
        1,
        "{alerts:?}\nstdout: {stdout}\nstderr: {stderr}"
    );
    assert_eq!(alerts[0]["outcome"], "failed");
    assert!(alerts[0]["message"].as_str().unwrap().starts_with("Failed"));
}
