//! L1 / V2: the *shipped* prompt carries the per-utterance example selection.
//!
//! `knaif-core`'s parity test proves `select_examples` matches the reference. This proves the
//! binary calls it. That distinction is not academic: before V1, `retrieve_tools` was a faithful,
//! fully tested port that nothing in the CLI ever called, and the prompt was wrong anyway. A
//! library-level contract cannot catch an unwired one.
//!
//! No model: `KNAIF_LLM_BACKEND=mock` plus `$KNAIF_DUMP_PROMPT` gives the exact system message
//! the runtime would have sent, so this is deterministic (L1).
//!
//! See docs/plans/2026-09-10-skill-quality-lifecycle.md (V2).

use std::path::{Path, PathBuf};
use std::process::Command;

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// The `(system, user)` dump for one utterance, with the system half returned.
fn system_prompt(skill: &str, utterance: &str) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["plan", "--skill", skill, utterance])
        .current_dir(repo())
        .env("KNAIF_DUMP_PROMPT", "1")
        .env("KNAIF_LLM_BACKEND", "mock")
        .env(
            "KNAIF_LLM_MOCK_RESPONSE",
            r#"{"plan":[{"tool":"done","args":{}}]}"#,
        )
        .output()
        .expect("run the knaif binary");
    let stderr = String::from_utf8_lossy(&out.stderr);
    // The same markers `prompt_dump` writes; see `PROMPT_DUMP_MARKER` in main.rs.
    let begin = "===KNAIF-PROMPT-BEGIN system\n";
    let end = "\n===KNAIF-PROMPT-END system";
    let from = stderr
        .find(begin)
        .unwrap_or_else(|| panic!("no prompt dump for {skill}/{utterance}:\n{stderr}"))
        + begin.len();
    let len = stderr[from..]
        .find(end)
        .unwrap_or_else(|| panic!("unterminated dump for {skill}/{utterance}:\n{stderr}"));
    stderr[from..from + len].to_string()
}

/// The example requests the prompt actually carries, in order.
fn example_requests(system: &str) -> Vec<String> {
    system
        .lines()
        .filter_map(|l| l.strip_prefix("  request: \""))
        .filter_map(|l| l.strip_suffix('"'))
        .map(str::to_string)
        .collect()
}

#[test]
fn the_shipped_prompt_selects_examples_per_utterance() {
    let fixtures = repo().join("contracts/parity/example_cases.json");
    let doc: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();

    for row in doc["shipped"].as_array().unwrap() {
        let skill = row["skill"].as_str().unwrap();
        let utterance = row["utterance"].as_str().unwrap();
        let want: Vec<String> = row["expected_requests"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();

        let got = example_requests(&system_prompt(skill, utterance));
        assert_eq!(
            got, want,
            "{skill}/{utterance}: shipped prompt examples differ"
        );
        assert!(
            got.len() < row["corpus_size"].as_u64().unwrap() as usize,
            "{skill}/{utterance}: the prompt still carries the whole corpus"
        );
    }
}

#[test]
fn the_selection_changes_with_the_utterance() {
    // The property that distinguishes selection from a fixed truncation: two utterances against
    // the same bundle must not receive the same examples.
    let a = example_requests(&system_prompt("ffmpeg", "make clip.mp4 smaller"));
    let b = example_requests(&system_prompt(
        "ffmpeg",
        "rotate clip.mp4 90 degrees clockwise",
    ));
    assert_ne!(a, b, "the examples block does not respond to the utterance");
}
