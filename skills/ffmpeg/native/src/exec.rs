//! External-tool boundary — the single place the native ffmpeg skill shells out.
//!
//! Port of the Python `_deps.run_ffmpeg`: run an already-rendered `ffmpeg` argv (never
//! model-emitted shell) and return its status. Keeping the subprocess call in one function is
//! where the future desktop/mobile UIs reuse execution (they embed the crate, they don't shell
//! to the CLI). The binary name is overridable via `$KNAIF_FFMPEG_BIN` (custom install / tests).

use std::path::Path;
use std::process::Output;

use crate::engine::{summarise_probe, Probe};

/// The ffmpeg binary to launch: `$KNAIF_FFMPEG_BIN` when set, else `ffmpeg` (found on `PATH`).
pub fn ffmpeg_bin() -> String {
    std::env::var("KNAIF_FFMPEG_BIN")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "ffmpeg".to_string())
}

/// The ffprobe binary to launch: `$KNAIF_FFPROBE_BIN` when set, else `ffprobe`.
pub fn ffprobe_bin() -> String {
    std::env::var("KNAIF_FFPROBE_BIN")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "ffprobe".to_string())
}

/// Probe a real media file, returning the normalized [`Probe`] the engine consumes. Port of
/// `_deps.run_ffprobe` + `_summarise_probe`. A missing binary yields a clear install message; a
/// non-zero exit (unreadable / not media) is an error the caller decides how to handle.
pub fn run_ffprobe(file: &Path) -> anyhow::Result<Probe> {
    let bin = ffprobe_bin();
    let output = std::process::Command::new(&bin)
        .args(["-v", "error", "-show_streams", "-show_format", "-of", "json"])
        .arg(file)
        .output()
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                anyhow::anyhow!(
                    "ffprobe not found ({bin}). Install ffmpeg (or set KNAIF_FFPROBE_BIN) to run the ffmpeg skill."
                )
            } else {
                anyhow::anyhow!("failed to launch ffprobe ({bin}): {e}")
            }
        })?;
    if !output.status.success() {
        anyhow::bail!(
            "ffprobe failed for {}: {}",
            file.display(),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    let doc: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|e| {
        anyhow::anyhow!("ffprobe returned invalid JSON for {}: {e}", file.display())
    })?;
    Ok(summarise_probe(file, &doc))
}

/// Run a rendered `ffmpeg` argv (as produced by `render_command`, so `argv[0] == "ffmpeg"`),
/// capturing output. A missing binary yields a clear install message, matching `FFmpegNotAvailable`.
pub fn run_ffmpeg(argv: &[String]) -> anyhow::Result<Output> {
    run_with_bin(&ffmpeg_bin(), argv)
}

/// Launch `bin` with `argv` minus a leading literal `ffmpeg` token (the rendered command carries
/// `ffmpeg` as `argv[0]`; the real program name comes from `bin`).
fn run_with_bin(bin: &str, argv: &[String]) -> anyhow::Result<Output> {
    let args: &[String] = match argv.first() {
        Some(first) if first == "ffmpeg" => &argv[1..],
        _ => argv,
    };
    std::process::Command::new(bin)
        .args(args)
        .output()
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                anyhow::anyhow!(
                    "ffmpeg not found ({bin}). Install ffmpeg (or set KNAIF_FFMPEG_BIN) to run the ffmpeg skill."
                )
            } else {
                anyhow::anyhow!("failed to launch ffmpeg ({bin}): {e}")
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_binary_reports_install_hint() {
        let err = run_with_bin(
            "knaif-nonexistent-ffmpeg-xyz",
            &["ffmpeg".to_string(), "-version".to_string()],
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("not found"), "unexpected error: {err}");
        assert!(err.contains("Install ffmpeg"), "unexpected error: {err}");
    }

    #[test]
    fn ffmpeg_bin_defaults_without_env() {
        // Exercises the default branch without mutating process env (parallel-test safe).
        if std::env::var_os("KNAIF_FFMPEG_BIN").is_none() {
            assert_eq!(ffmpeg_bin(), "ffmpeg");
        }
    }
}
