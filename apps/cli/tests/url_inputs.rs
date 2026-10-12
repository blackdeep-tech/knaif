//! URL inputs in the native `run`: downloaded into the run scratch before anything else runs,
//! after approval, with only the result reaching the user's folder (Python
//! `CommandAgent._fetch_url_inputs`).
//!
//! No model: `KNAIF_LLM_BACKEND=mock` plus `KNAIF_LLM_MOCK_RESPONSE` feeds the runtime an exact
//! plan. A local server answers the reserved `files.knaif.test` through `KNAIF_NET_TEST_HOST`.
//! Stdin is null, so there is nobody to ask: without `--allow-download` the run must stop.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;

const NOTES: &[u8] = b"Invoice Alpha\nsecond line\n";

/// Serves `NOTES` at any path; counts requests.
fn serve(body: &'static [u8], ctype: &'static str) -> (String, Arc<AtomicUsize>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let hits = Arc::new(AtomicUsize::new(0));
    let counter = hits.clone();
    thread::spawn(move || {
        for mut stream in listener.incoming().flatten() {
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            loop {
                let mut line = String::new();
                if reader.read_line(&mut line).is_err() || line.trim().is_empty() {
                    break;
                }
            }
            counter.fetch_add(1, Ordering::SeqCst);
            let head = format!(
                "HTTP/1.0 200 OK\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\n\r\n",
                body.len()
            );
            let _ = stream.write_all(head.as_bytes());
            let _ = stream.write_all(body);
        }
    });
    (base, hits)
}

struct Dir(PathBuf);
impl Dir {
    fn new(tag: &str) -> Self {
        let d = std::env::temp_dir().join(format!("knaif_url_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        Self(d)
    }
    fn names(&self) -> Vec<String> {
        let mut v: Vec<String> = std::fs::read_dir(&self.0)
            .unwrap()
            .flatten()
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        v.sort();
        v
    }
}
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn run(
    skill: &str,
    sandbox: &Path,
    base: &str,
    plan: &str,
    request: &str,
    flags: &[&str],
) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", skill, "--sandbox"])
        .arg(sandbox)
        .args(flags)
        .arg(request)
        .stdin(Stdio::null())
        .env(
            "KNAIF_SKILLS_ROOT",
            concat!(env!("CARGO_MANIFEST_DIR"), "/../../skills"),
        )
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", plan)
        .env("KNAIF_NET_TEST_HOST", format!("files.knaif.test={base}"))
        .env_remove("NO_COLOR")
        .output()
        .expect("run the knaif binary");
    format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    )
}

const URL: &str = "https://files.knaif.test/documents/notes.txt";

fn convert_plan() -> String {
    format!(
        r#"{{"plan": [{{"tool": "convert_document", "args": {{"input": "{URL}", "to_format": "md"}}}}]}}"#
    )
}

#[test]
fn an_approved_url_becomes_one_result_in_the_users_folder() {
    let (base, hits) = serve(NOTES, "text/plain");
    let sb = Dir::new("approved");
    let out = run(
        "documents",
        &sb.0,
        &base,
        &convert_plan(),
        &format!("convert {URL} to markdown"),
        &["--allow-download"],
    );
    assert_eq!(sb.names(), vec!["notes.md".to_string()], "{out}");
    assert!(std::fs::read_to_string(sb.0.join("notes.md"))
        .unwrap()
        .contains("Invoice Alpha"));
    assert_eq!(hits.load(Ordering::SeqCst), 1, "{out}");
}

#[test]
fn without_approval_and_without_a_terminal_nothing_is_requested() {
    let (base, hits) = serve(NOTES, "text/plain");
    let sb = Dir::new("noflag");
    let out = run(
        "documents",
        &sb.0,
        &base,
        &convert_plan(),
        &format!("convert {URL} to markdown"),
        &[],
    );
    assert!(out.contains("--allow-download"), "{out}");
    assert_eq!(hits.load(Ordering::SeqCst), 0);
    assert!(sb.names().is_empty(), "{out}");
}

#[test]
fn yes_does_not_approve_a_download() {
    let (base, hits) = serve(NOTES, "text/plain");
    let sb = Dir::new("yes");
    let out = run(
        "documents",
        &sb.0,
        &base,
        &convert_plan(),
        &format!("convert {URL} to markdown"),
        &["--yes"],
    );
    assert!(out.contains("--allow-download"), "{out}");
    assert_eq!(hits.load(Ordering::SeqCst), 0);
}

#[test]
fn a_dry_run_requests_nothing_and_says_what_it_would_fetch() {
    let (base, hits) = serve(NOTES, "text/plain");
    let sb = Dir::new("dry");
    let out = run(
        "documents",
        &sb.0,
        &base,
        &convert_plan(),
        &format!("convert {URL} to markdown"),
        &["--dry-run", "--allow-download"],
    );
    assert!(out.contains(&format!("would download {URL}")), "{out}");
    assert_eq!(hits.load(Ordering::SeqCst), 0);
    assert!(sb.names().is_empty(), "{out}");
}

#[test]
fn a_dry_run_read_of_a_url_does_not_read_the_file_it_has_not_downloaded() {
    // Python's read steps return an empty stub in a dry run when the input is not there yet.
    let (base, hits) = serve(NOTES, "text/plain");
    for args in [
        format!(r#""tool": "inspect_document", "args": {{"input": "{URL}"}}"#),
        format!(r#""tool": "extract_text", "args": {{"input": "{URL}"}}"#),
        format!(r#""tool": "find_in_document", "args": {{"input": "{URL}", "query": "Alpha"}}"#),
    ] {
        let sb = Dir::new("dryread");
        let out = run(
            "documents",
            &sb.0,
            &base,
            &format!(r#"{{"plan": [{{{args}}}]}}"#),
            &format!("read {URL}"),
            &["--dry-run", "--allow-download"],
        );
        assert!(out.contains(&format!("would download {URL}")), "{out}");
        assert!(!out.contains("Error"), "{args}\n{out}");
        assert_eq!(hits.load(Ordering::SeqCst), 0);
    }
}

#[test]
fn a_refused_download_stops_the_run_by_name() {
    let (base, _hits) = serve(b"<html></html>", "text/html; charset=utf-8");
    let sb = Dir::new("html");
    let out = run(
        "documents",
        &sb.0,
        &base,
        &convert_plan(),
        &format!("convert {URL} to markdown"),
        &["--allow-download"],
    );
    assert!(out.contains("web page"), "{out}");
    assert!(sb.names().is_empty(), "{out}");
}

#[test]
fn ffmpeg_converts_a_url_into_the_users_folder() {
    let ok = Command::new("ffmpeg")
        .arg("-version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    if !ok.map(|s| s.success()).unwrap_or(false) {
        eprintln!("SKIPPED: ffmpeg is not on PATH");
        return;
    }
    let src = Dir::new("clipsrc");
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
        .args(["-f", "lavfi", "-i", "sine=d=1", "-shortest", "clip.mov"])
        .current_dir(&src.0)
        .status()
        .unwrap();
    assert!(made.success());
    let bytes: &'static [u8] = Box::leak(
        std::fs::read(src.0.join("clip.mov"))
            .unwrap()
            .into_boxed_slice(),
    );
    let (base, hits) = serve(bytes, "video/quicktime");
    let sb = Dir::new("ffmpeg");
    let url = "https://files.knaif.test/ffmpeg/holiday.mov";
    let plan = format!(
        r#"{{"plan": [{{"tool": "convert_video", "args": {{"inputs": ["{url}"], "container": "mp4"}}}}]}}"#
    );
    let out = run(
        "ffmpeg",
        &sb.0,
        &base,
        &plan,
        &format!("convert {url} to mp4"),
        &["--allow-download"],
    );
    assert_eq!(
        sb.names(),
        vec!["holiday_converted.mp4".to_string()],
        "{out}"
    );
    assert_eq!(hits.load(Ordering::SeqCst), 1, "{out}");
}
