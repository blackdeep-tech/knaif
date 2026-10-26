//! (`--keep-intermediates`: every step's output stays in the user's folder. Without
//! it, a chain's refined intermediate goes to the run scratch (the keep rule); with the flag, or
//! with request words like "keep the intermediate files", nothing is routed.
//!
//! Real execution, so these need `ffmpeg` on PATH and skip (loudly) when it is missing. No model:
//! `KNAIF_LLM_BACKEND=mock` plus `KNAIF_LLM_MOCK_RESPONSE` feeds the runtime an exact plan.

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

/// A folder holding a 2-second clip, removed on drop.
struct Folder(PathBuf);

impl Folder {
    fn new(tag: &str) -> Option<Self> {
        if !ffmpeg_available() {
            eprintln!("SKIPPED: ffmpeg is not on PATH");
            return None;
        }
        let dir = std::env::temp_dir().join(format!("knaif_keep_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let made = Command::new("ffmpeg")
            .args(["-v", "error", "-y", "-f", "lavfi", "-i"])
            .arg("testsrc=d=2:s=64x64:r=10")
            .args(["-f", "lavfi", "-i", "sine=d=2", "-shortest", "clip.mp4"])
            .current_dir(&dir)
            .status()
            .unwrap();
        assert!(made.success(), "could not make the fixture clip");
        Some(Self(dir))
    }

    fn has(&self, name: &str) -> bool {
        self.0.join(name).is_file()
    }
}

impl Drop for Folder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn slash(dir: &Path) -> String {
    dir.display().to_string().replace('\\', "/")
}

/// Trim, then mute the trim: the same kind, so the trim is refined and is an intermediate.
fn plan(dir: &Path) -> String {
    let d = slash(dir);
    format!(
        r#"{{"plan": [
            {{"tool": "trim_video", "args": {{"input": "{d}/clip.mp4", "end": "00:00:01", "output": "{d}/mid.mp4"}}}},
            {{"tool": "strip_audio", "args": {{"inputs": ["{d}/mid.mp4"], "output": "{d}/final.mp4"}}}}
        ]}}"#
    )
}

fn run(dir: &Path, request: &str, flags: &[&str]) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", "ffmpeg"])
        .args(flags)
        .arg(format!("{request} {}/clip.mp4", slash(dir)))
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
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.status.success(), "the run failed:\n{text}");
    text
}

#[test]
fn a_refined_intermediate_goes_to_the_scratch_by_default() {
    let Some(f) = Folder::new("default") else {
        return;
    };
    let out = run(&f.0, "trim and mute", &[]);
    assert!(f.has("final.mp4"), "the result was written:\n{out}");
    assert!(!f.has("mid.mp4"), "the trim was a step on the way:\n{out}");
}

#[test]
fn the_flag_keeps_every_output() {
    let Some(f) = Folder::new("flag") else {
        return;
    };
    let out = run(&f.0, "trim and mute", &["--keep-intermediates"]);
    assert!(f.has("final.mp4") && f.has("mid.mp4"), "both stay:\n{out}");
}

#[test]
fn request_words_keep_every_output() {
    let Some(f) = Folder::new("words") else {
        return;
    };
    let out = run(&f.0, "keep the intermediate files, trim and mute", &[]);
    assert!(f.has("final.mp4") && f.has("mid.mp4"), "both stay:\n{out}");
}
