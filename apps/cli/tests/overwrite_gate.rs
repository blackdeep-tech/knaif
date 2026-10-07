//! 1.3.0: a step acts without asking, but never replaces an existing file without `--overwrite`.
//!
//! Real execution, so these need `ffmpeg` on PATH and skip (loudly) when it is missing. No model:
//! `KNAIF_LLM_BACKEND=mock` plus `KNAIF_LLM_MOCK_RESPONSE` feeds the runtime an exact plan. Stdin is
//! always null, so there is nobody to ask — the non-interactive half of the contract.
//!
//! The rendered command still carries `-y` (it is part of the parity contract), which is exactly
//! why the gate has to sit before it: ffmpeg itself would replace the file silently.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

fn ffmpeg_available() -> bool {
    Command::new("ffmpeg")
        .arg("-version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// A scratch directory holding a 1-second clip, removed on drop.
struct Scratch(PathBuf);

impl Scratch {
    fn new(tag: &str) -> Option<Self> {
        if !ffmpeg_available() {
            eprintln!("SKIPPED: ffmpeg is not on PATH");
            return None;
        }
        let dir =
            std::env::temp_dir().join(format!("knaif_overwrite_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let made = Command::new("ffmpeg")
            .args([
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=d=1:s=64x64:r=10",
            ])
            .args(["-f", "lavfi", "-i", "sine=d=1", "-shortest", "clip.mp4"])
            .current_dir(&dir)
            .status()
            .unwrap();
        assert!(made.success(), "could not make the fixture clip");
        Some(Self(dir))
    }

    fn path(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The plan, with absolute paths: the binary looks for `contracts/` from its working directory, so
/// it runs from the checkout rather than from the scratch folder.
fn slash(dir: &Path) -> String {
    dir.display().to_string().replace('\\', "/")
}

fn plan(dir: &Path) -> String {
    let d = slash(dir);
    format!(
        r#"{{"plan": [{{"tool": "strip_audio", "args": {{"inputs": ["{d}/clip.mp4"], "output": "{d}/silent.mp4"}}}}]}}"#
    )
}

fn run(dir: &Path, flags: &[&str]) -> (String, bool) {
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", "ffmpeg"])
        .args(flags)
        .arg(format!("strip audio from {}/clip.mp4", slash(dir)))
        .stdin(Stdio::null())
        .env(
            "KNAIF_SKILLS_ROOT",
            concat!(env!("CARGO_MANIFEST_DIR"), "/../../skills"),
        )
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", plan(dir))
        .env_remove("NO_COLOR")
        .output()
        .expect("run the knaif binary");
    (
        format!(
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        ),
        out.status.success(),
    )
}

#[test]
fn a_new_output_is_written_without_asking() {
    let Some(s) = Scratch::new("new") else { return };
    let (out, ok) = run(&s.0, &[]);
    assert!(ok, "the default acts without asking:\n{out}");
    assert!(
        s.path("silent.mp4").is_file(),
        "the output was written:\n{out}"
    );
}

#[test]
fn an_existing_output_is_not_replaced_without_overwrite() {
    let Some(s) = Scratch::new("keep") else {
        return;
    };
    std::fs::write(s.path("silent.mp4"), b"precious").unwrap();
    let (out, ok) = run(&s.0, &[]);
    assert!(!ok, "no terminal, no --overwrite: it must stop:\n{out}");
    assert!(out.contains("--overwrite"), "names the flag:\n{out}");
    assert_eq!(std::fs::read(s.path("silent.mp4")).unwrap(), b"precious");
}

#[test]
fn yes_does_not_approve_replacing_a_file() {
    let Some(s) = Scratch::new("yes") else { return };
    std::fs::write(s.path("silent.mp4"), b"precious").unwrap();
    let (out, ok) = run(&s.0, &["--yes"]);
    assert!(!ok, "--yes is not --overwrite:\n{out}");
    assert_eq!(std::fs::read(s.path("silent.mp4")).unwrap(), b"precious");
}

#[test]
fn overwrite_replaces_the_file() {
    let Some(s) = Scratch::new("replace") else {
        return;
    };
    std::fs::write(s.path("silent.mp4"), b"precious").unwrap();
    let (out, ok) = run(&s.0, &["--overwrite"]);
    assert!(ok, "--overwrite approves it:\n{out}");
    assert_ne!(std::fs::read(s.path("silent.mp4")).unwrap(), b"precious");
}

#[test]
fn a_dry_run_never_asks_and_never_touches_the_file() {
    let Some(s) = Scratch::new("dry") else { return };
    std::fs::write(s.path("silent.mp4"), b"precious").unwrap();
    let (out, ok) = run(&s.0, &["--dry-run"]);
    assert!(ok, "a preview does not need an answer:\n{out}");
    assert_eq!(std::fs::read(s.path("silent.mp4")).unwrap(), b"precious");
}
