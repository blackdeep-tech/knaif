//! A batch chain in the native run: the model's steps for one file run once for
//! every file the glob matched, a failed file stops only its own chain, and the run ends with
//! the summary on stderr and exit 1 when any file failed, as Python's CLI does.
//!
//! Real execution, so these need `ffmpeg` on PATH and skip (loudly) when it is missing. No model:
//! `KNAIF_LLM_BACKEND=mock` plus `KNAIF_LLM_MOCK_RESPONSE` feeds the runtime an exact plan.

use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

fn ffmpeg_available() -> bool {
    Command::new("ffmpeg")
        .arg("-version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// A folder of 1-second clips, removed on drop: `with_audio` have a sound track, `silent` not.
struct Folder(PathBuf);

impl Folder {
    fn new(tag: &str, with_audio: &[&str], silent: &[&str]) -> Option<Self> {
        if !ffmpeg_available() {
            eprintln!("SKIPPED: ffmpeg is not on PATH");
            return None;
        }
        let dir = std::env::temp_dir().join(format!("knaif_batch_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        for name in with_audio {
            let made = Command::new("ffmpeg")
                .args(["-v", "error", "-y", "-f", "lavfi", "-i"])
                .arg("testsrc=d=1:s=64x64:r=10")
                .args(["-f", "lavfi", "-i", "sine=d=1", "-shortest"])
                .arg(name)
                .current_dir(&dir)
                .status()
                .unwrap();
            assert!(made.success(), "could not make {name}");
        }
        for name in silent {
            let made = Command::new("ffmpeg")
                .args(["-v", "error", "-y", "-f", "lavfi", "-i"])
                .arg("testsrc=d=1:s=64x64:r=10")
                .arg(name)
                .current_dir(&dir)
                .status()
                .unwrap();
            assert!(made.success(), "could not make {name}");
        }
        Some(Self(dir))
    }

    fn has(&self, name: &str) -> bool {
        self.0.join(name).is_file()
    }

    fn names(&self) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(&self.0)
            .unwrap()
            .flatten()
            .filter(|e| e.path().is_file())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        names
    }
}

impl Drop for Folder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

const CONVERT_THEN_AUDIO: &str = r#"{"plan": [
    {"tool": "convert_video", "args": {"inputs": ["*.mov"], "container": "mp4", "output": "clip.mp4"}},
    {"tool": "extract_audio", "args": {"inputs": ["clip.mp4"], "audio_format": "mp3"}}
]}"#;

fn run(dir: &Path, request: &str, plan: &str) -> Output {
    Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", "ffmpeg", "--sandbox"])
        .arg(dir)
        .arg(request)
        .stdin(Stdio::null())
        .env(
            "KNAIF_SKILLS_ROOT",
            concat!(env!("CARGO_MANIFEST_DIR"), "/../../skills"),
        )
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", plan)
        .env("NO_COLOR", "1")
        .output()
        .unwrap()
}

#[test]
fn a_chain_runs_once_per_file_and_a_failed_file_stops_only_itself() {
    let Some(folder) = Folder::new("fail", &["beach.mov", "city.mov"], &["silent.mov"]) else {
        return;
    };
    let out = run(
        &folder.0,
        "convert all the .mov files here to mp4 and extract their audio as mp3",
        CONVERT_THEN_AUDIO,
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(
        out.status.code(),
        Some(1),
        "a failed file exits 1\n{stderr}"
    );
    assert!(
        stderr.lines().any(|l| l.starts_with("✗ silent.mov: ")),
        "the failure line names the file\n{stderr}"
    );
    assert!(
        stderr
            .lines()
            .any(|l| l.starts_with("2 done, 1 failed in ")),
        "the summary counts the files\n{stderr}"
    );
    assert!(
        !stderr.contains(&*folder.0.to_string_lossy()),
        "no folder in the reason\n{stderr}"
    );
    for name in ["beach.mp4", "city.mp4", "silent.mp4"] {
        assert!(
            folder.has(name),
            "{name} was converted: {:?}",
            folder.names()
        );
    }
    let mp3s: Vec<String> = folder
        .names()
        .into_iter()
        .filter(|n| n.ends_with(".mp3"))
        .collect();
    assert_eq!(mp3s.len(), 2, "beach and city have audio: {mp3s:?}");
    assert!(mp3s.iter().all(|n| !n.starts_with("silent")), "{mp3s:?}");
}

#[test]
fn a_batch_where_every_file_works_exits_0_with_its_summary() {
    let Some(folder) = Folder::new("ok", &["beach.mov", "city.mov"], &[]) else {
        return;
    };
    let out = run(
        &folder.0,
        "convert all the .mov files here to mp4 and extract their audio as mp3",
        CONVERT_THEN_AUDIO,
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(0), "{stderr}");
    assert!(
        stderr.lines().any(|l| l.starts_with("2 done in ")),
        "{stderr}"
    );
}

#[test]
fn every_video_written_as_mp4_covers_the_other_videos() {
    // "every video" with the model's `*.mp4`: the .mov is read too (`batch_kinds: [video]`).
    let Some(folder) = Folder::new("widen", &["a.mp4", "b.mov"], &[]) else {
        return;
    };
    let plan = r#"{"plan": [
        {"tool": "create_thumbnail", "args": {"input": "*.mp4", "at_time": "00:00:00", "output": "thumb_*.jpg"}}
    ]}"#;
    let out = run(&folder.0, "make a thumbnail of every video here", plan);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(0), "{stderr}");
    assert!(folder.has("thumb_a.jpg"), "{:?}", folder.names());
    assert!(folder.has("thumb_b.jpg"), "{:?}", folder.names());
}

#[test]
fn each_piece_of_a_split_runs_the_next_step_and_only_the_results_stay() {
    // "split clip.mp4 into 1-second parts and compress each one": the pieces are a set the skill
    // names before running; compress runs once per piece, and the raw pieces stay in the run
    // scratch, as Python leaves them.
    let Some(folder) = Folder::new("split", &["clip.mp4"], &[]) else {
        return;
    };
    let plan = r#"{"plan": [
        {"tool": "split_video", "args": {"input": "clip.mp4", "every": "0.5s", "output": "clip_part.mp4"}},
        {"tool": "compress_video", "args": {"inputs": ["clip_part.mp4"]}}
    ]}"#;
    let out = run(
        &folder.0,
        "split clip.mp4 into half-second parts and compress each one",
        plan,
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(0), "{stderr}");
    assert_eq!(
        folder.names(),
        [
            "clip.mp4",
            "clip_part_001_compressed.mp4",
            "clip_part_002_compressed.mp4"
        ],
        "{stderr}"
    );
}

#[test]
fn each_page_of_a_split_pdf_runs_the_next_step() {
    // Documents: one file per page, then a stamp on each; no ffmpeg needed.
    let dir = std::env::temp_dir().join(format!("knaif_batch_pages_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::copy(
        concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/parity/fixtures/documents/sample.pdf"
        ),
        dir.join("sample.pdf"),
    )
    .unwrap();
    let folder = Folder(dir);
    let plan = r#"{"plan": [
        {"tool": "split_pdf_every", "args": {"input": "sample.pdf", "every": 1, "output": "page.pdf"}},
        {"tool": "watermark", "args": {"input": "page.pdf", "text": "DRAFT"}}
    ]}"#;
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", "documents", "--sandbox"])
        .arg(&folder.0)
        .arg("split sample.pdf into one file per page and stamp DRAFT on each")
        .stdin(Stdio::null())
        .env(
            "KNAIF_SKILLS_ROOT",
            concat!(env!("CARGO_MANIFEST_DIR"), "/../../skills"),
        )
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", plan)
        .env("NO_COLOR", "1")
        .output()
        .unwrap();
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(0), "{stderr}");
    assert_eq!(
        folder.names(),
        [
            "sample-pages-1-watermarked.pdf",
            "sample-pages-2-watermarked.pdf",
            "sample-pages-3-watermarked.pdf",
            "sample.pdf"
        ],
        "{stderr}"
    );
}

#[test]
fn a_lifted_batch_dumps_the_plan_it_runs() {
    // `KNAIF_DUMP_PLAN`: after the model's plan, the plan the run executes (one step per file),
    // which the eval's native replay reads to attribute each file a step wrote.
    let Some(folder) = Folder::new("runplan", &["beach.mov", "city.mov"], &[]) else {
        return;
    };
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", "ffmpeg", "--sandbox"])
        .arg(&folder.0)
        .arg("convert all the .mov files here to mp4 and extract their audio as mp3")
        .stdin(Stdio::null())
        .env(
            "KNAIF_SKILLS_ROOT",
            concat!(env!("CARGO_MANIFEST_DIR"), "/../../skills"),
        )
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", CONVERT_THEN_AUDIO)
        .env("KNAIF_DUMP_PLAN", "1")
        .env("NO_COLOR", "1")
        .output()
        .unwrap();
    let stderr = String::from_utf8_lossy(&out.stderr);
    let line = stderr
        .lines()
        .find_map(|l| l.strip_prefix("===KNAIF-RUN-PLAN==="))
        .unwrap_or_else(|| panic!("no run plan dumped\n{stderr}"));
    let plan: serde_json::Value = serde_json::from_str(line).unwrap();
    assert_eq!(plan["plan"].as_array().unwrap().len(), 4, "{line}");
}

#[test]
fn a_single_batch_step_reports_each_failed_file_and_goes_on() {
    // One step over many files, no chain ("convert all the .mov files here to mp4"): a broken
    // file is reported on its own line and the others are converted; the run ends with the
    // summary and exit 1, as Python's CLI does.
    let Some(folder) = Folder::new("single", &["beach.mov", "city.mov"], &[]) else {
        return;
    };
    std::fs::write(folder.0.join("broken.mov"), b"not a video").unwrap();
    let plan = r#"{"plan": [{"tool": "convert_video", "args": {"inputs": ["*.mov"], "container": "mp4"}}]}"#;
    let out = run(&folder.0, "convert all the .mov files here to mp4", plan);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{stderr}");
    assert!(
        stderr.lines().any(|l| l.starts_with("✗ broken.mov: ")),
        "{stderr}"
    );
    assert!(
        stderr
            .lines()
            .any(|l| l.starts_with("2 done, 1 failed in ")),
        "{stderr}"
    );
    let mp4s: Vec<String> = folder
        .names()
        .into_iter()
        .filter(|n| n.ends_with(".mp4"))
        .collect();
    assert_eq!(mp4s.len(), 2, "beach and city converted: {mp4s:?}");
}

#[test]
fn a_silent_file_in_a_single_audio_step_is_a_failed_file() {
    // "extract the audio from every video here" with one silent clip: the others are extracted,
    // the silent one is reported as failed (Python: "has no audio track to extract").
    let Some(folder) = Folder::new("silent", &["beach.mov", "city.mov"], &["silent.mov"]) else {
        return;
    };
    let plan = r#"{"plan": [{"tool": "extract_audio", "args": {"inputs": ["*.mov"], "audio_format": "mp3"}}]}"#;
    let out = run(
        &folder.0,
        "extract the audio from every video here as mp3",
        plan,
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{stderr}");
    assert!(
        stderr
            .lines()
            .any(|l| l == "✗ silent.mov: has no audio track to extract"),
        "{stderr}"
    );
    assert!(
        stderr
            .lines()
            .any(|l| l.starts_with("2 done, 1 failed in ")),
        "{stderr}"
    );
}
