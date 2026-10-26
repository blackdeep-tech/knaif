//! One display for both CLIs (owner, 2026-10-23): what `knaif run` prints for a canned
//! model output, end to end, pinned in `contracts/parity/cli_output_cases.json`.
//!
//! Native is the reference: under `KNAIF_RECORD_CONTRACT=1` this records what the binary prints
//! into the cases; otherwise it checks the binary against them. Python's `run` is held to the
//! same cases (`python/core/tests/test_cli_output_parity.py`). Each case runs in an empty folder
//! inside the checkout (the binary finds its contracts above it), with stdin closed and output
//! piped, so this is the plain view.

use std::path::PathBuf;
use std::process::{Command, Stdio};

fn ffmpeg_available() -> bool {
    Command::new("ffmpeg")
        .arg("-version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

fn contract() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../contracts/parity/cli_output_cases.json")
}

/// Every timing as `<t> s` (`1.842 s`, the batch summary's `0.1s`): they differ run to run. A
/// number with a decimal point, then `s` or ` s`, not followed by a letter or digit. Python's test
/// masks the same way.
fn masked(text: &str) -> String {
    // Line ends as `\n`: a generated text fixture has the platform's.
    let text = &without_progress(text).replace("\r\n", "\n");
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        let starts = chars[i].is_ascii_digit() && (i == 0 || !chars[i - 1].is_ascii_digit());
        if starts {
            let mut j = i;
            while j < chars.len() && chars[j].is_ascii_digit() {
                j += 1;
            }
            if chars.get(j) == Some(&'.') && chars.get(j + 1).is_some_and(char::is_ascii_digit) {
                let mut k = j + 1;
                while k < chars.len() && chars[k].is_ascii_digit() {
                    k += 1;
                }
                let s_at = if chars.get(k) == Some(&' ') { k + 1 } else { k };
                let after = chars.get(s_at + 1);
                if chars.get(s_at) == Some(&'s') && !after.is_some_and(|c| c.is_alphanumeric()) {
                    out.push_str("<t> s");
                    i = s_at + 1;
                    continue;
                }
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// Native's live ffmpeg line, redrawn in place and erased once the command ends: from its
/// ` │   └─ <spinner frame>` to the erase (`\r` + ESC `[2K`). Python draws none.
fn without_progress(text: &str) -> String {
    const FRAMES: &str = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏";
    const LEAD: &str = " │   └─ ";
    const ERASE: &str = "\r\u{1b}[2K";
    let mut out = String::new();
    let mut rest = text;
    while let Some(at) = rest.find(LEAD) {
        let after = &rest[at + LEAD.len()..];
        if after.chars().next().is_some_and(|c| FRAMES.contains(c)) {
            if let Some(end) = after.find(ERASE) {
                // The first frame starts with its own `\r`.
                out.push_str(rest[..at].strip_suffix('\r').unwrap_or(&rest[..at]));
                rest = &after[end + ERASE.len()..];
                continue;
            }
        }
        out.push_str(&rest[..at + LEAD.len()]);
        rest = after;
    }
    out.push_str(rest);
    out
}

fn run(case: &serde_json::Value, dir: &std::path::Path) -> (String, String, bool) {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_knaif"));
    if case["view"].as_str() == Some("rich") {
        cmd.env("KNAIF_VIEW", "rich").env("NO_COLOR", "1");
    }
    cmd.arg("run").arg(case["skill"].as_str().unwrap());
    for flag in case["flags"].as_array().unwrap() {
        cmd.arg(flag.as_str().unwrap());
    }
    if case["view"].as_str() != Some("rich") {
        cmd.env_remove("KNAIF_VIEW").env_remove("NO_COLOR");
    }
    let out = cmd
        .arg(case["utterance"].as_str().unwrap())
        .current_dir(dir)
        .stdin(Stdio::null())
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", case["plan"].to_string())
        .env_remove("KNAIF_DUMP_PLAN")
        .env_remove("KNAIF_DEBUG")
        .output()
        .expect("run the knaif binary");
    (
        masked(&String::from_utf8_lossy(&out.stdout)),
        masked(&String::from_utf8_lossy(&out.stderr)),
        out.status.success(),
    )
}

#[test]
fn cli_output_cases() {
    let path = contract();
    let mut doc: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    let record = std::env::var_os("KNAIF_RECORD_CONTRACT").is_some();
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../sandbox")
        .join(format!("tmp_cli_output_{}", std::process::id()));
    for (i, case) in doc["cases"].as_array_mut().unwrap().iter_mut().enumerate() {
        let dir = root.join(i.to_string());
        std::fs::create_dir_all(&dir).unwrap();
        for file in case["files"].as_array().into_iter().flatten() {
            std::fs::write(dir.join(file.as_str().unwrap()), b"").unwrap();
        }
        let fixtures = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../sandbox/fixtures")
            .join(case["skill"].as_str().unwrap());
        let mut missing = false;
        for file in case["fixtures"].as_array().into_iter().flatten() {
            let name = file.as_str().unwrap();
            missing |= std::fs::copy(fixtures.join(name), dir.join(name)).is_err();
        }
        let executes = !case["flags"]
            .as_array()
            .unwrap()
            .iter()
            .any(|f| f == "--dry-run");
        if missing || (executes && case["skill"] == "ffmpeg" && !ffmpeg_available()) {
            eprintln!("SKIPPED: {} (fixture or ffmpeg missing)", case["name"]);
            continue;
        }
        let (stdout, stderr, ok) = run(case, &dir);
        let name = case["name"].as_str().unwrap().to_string();
        if let Some(expected) = case["files_after"].as_array() {
            let mut left: Vec<String> = std::fs::read_dir(&dir)
                .unwrap()
                .flatten()
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .collect();
            left.sort();
            let expected: Vec<String> = expected
                .iter()
                .map(|f| f.as_str().unwrap().to_string())
                .collect();
            assert_eq!(left, expected, "{name}: the files left");
        }
        if record {
            case["stdout"] = serde_json::Value::String(stdout);
            if case.get("stderr_prefix").is_none() {
                case["stderr"] = serde_json::Value::String(stderr);
            }
            case["ok"] = serde_json::Value::Bool(ok);
            continue;
        }
        assert_eq!(case["stdout"].as_str(), Some(stdout.as_str()), "{name}");
        match case["stderr_prefix"].as_str() {
            Some(prefix) => assert!(stderr.starts_with(prefix), "{name}: {stderr}"),
            None => assert_eq!(case["stderr"].as_str(), Some(stderr.as_str()), "{name}"),
        }
        assert_eq!(case["ok"].as_bool(), Some(ok), "{name}");
    }
    let _ = std::fs::remove_dir_all(&root);
    if record {
        std::fs::write(&path, serde_json::to_string_pretty(&doc).unwrap() + "\n").unwrap();
    }
}
