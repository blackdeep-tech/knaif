//! External-tool boundary — the single place the native ffmpeg skill shells out.
//!
//! Port of the Python `_deps.run_ffmpeg`: run an already-rendered `ffmpeg` argv (never
//! model-emitted shell) and return its status. Keeping the subprocess call in one function is
//! where the future desktop/mobile UIs reuse execution (they embed the crate, they don't shell
//! to the CLI). The binary is the one `knaif skills deps` reports: `$KNAIF_FFMPEG_BIN`, else
//! `PATH`, else (Windows) the install folders `skill.yaml` declares.

use std::path::Path;
use std::process::Output;

use crate::engine::{summarise_probe, Probe};

/// This bundle's `skill.yaml`, whose `external_tools` entry says where ffmpeg may live.
const SKILL_YAML: &str = include_str!("../../skill.yaml");

/// The ffmpeg binary to launch: `$KNAIF_FFMPEG_BIN` when set, else the `PATH` hit, else the
/// declared install folders; the bare `ffmpeg` when none resolves.
pub fn ffmpeg_bin() -> String {
    knaif_skill_api::tools::command_bin(SKILL_YAML, "ffmpeg")
        .to_string_lossy()
        .into_owned()
}

/// The ffprobe binary to launch, resolved as [`ffmpeg_bin`] (`$KNAIF_FFPROBE_BIN` first).
pub fn ffprobe_bin() -> String {
    knaif_skill_api::tools::command_bin(SKILL_YAML, "ffprobe")
        .to_string_lossy()
        .into_owned()
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

/// How far a running ffmpeg has got, from its `-progress` stream.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Progress {
    /// Seconds of output written so far.
    pub out_seconds: f64,
    /// Encoding speed relative to real time (`2.1` = 2.1x), when ffmpeg reports one.
    pub speed: Option<f64>,
}

/// Fold one `key=value` line of ffmpeg's `-progress` output into `state`. A block ends with
/// `progress=continue|end`, which is when a complete update is returned.
pub fn parse_progress_line(line: &str, state: &mut Progress) -> Option<Progress> {
    let (key, value) = line.trim().split_once('=')?;
    match key {
        // Despite the name, `out_time_ms` is microseconds too; `out_time_us` is the honest one.
        "out_time_us" | "out_time_ms" => {
            if let Ok(us) = value.trim().parse::<f64>() {
                state.out_seconds = (us / 1_000_000.0).max(0.0);
            }
            None
        }
        "speed" => {
            state.speed = value
                .trim()
                .trim_end_matches('x')
                .trim()
                .parse::<f64>()
                .ok();
            None
        }
        "progress" => Some(*state),
        _ => None,
    }
}

/// The duration, in seconds, of the first input of a rendered argv (`-i <file>`), by probing it.
/// `None` when there is no such input or it cannot be probed; progress then has no percentage.
pub fn input_duration(argv: &[String]) -> Option<f64> {
    let at = argv.iter().position(|a| a == "-i")?;
    let file = argv.get(at + 1)?;
    run_ffprobe(Path::new(file)).ok()?.duration
}

/// [`run_ffmpeg`] that reports progress while it runs. ffmpeg is asked for `-progress pipe:1
/// -nostats`; those are global options added after the program name and are not part of the
/// command shown to the user. The returned `Output` carries stderr, as `run_ffmpeg`'s does.
pub fn run_ffmpeg_with_progress(
    argv: &[String],
    mut on_progress: impl FnMut(Progress),
) -> anyhow::Result<Output> {
    use std::io::{BufRead, BufReader, Read};
    use std::process::Stdio;
    let bin = ffmpeg_bin();
    let args: &[String] = match argv.first() {
        Some(first) if first == "ffmpeg" => &argv[1..],
        _ => argv,
    };
    let mut child = std::process::Command::new(&bin)
        .args(["-progress", "pipe:1", "-nostats"])
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                anyhow::anyhow!(
                    "ffmpeg not found ({bin}). Install ffmpeg (or set KNAIF_FFMPEG_BIN) to run the ffmpeg skill."
                )
            } else {
                anyhow::anyhow!("failed to launch ffmpeg ({bin}): {e}")
            }
        })?;
    // stderr is drained on its own thread: a full pipe would otherwise stall ffmpeg while this
    // thread waits on stdout.
    let mut err_pipe = child.stderr.take().expect("stderr was piped");
    let drain = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = err_pipe.read_to_end(&mut buf);
        buf
    });
    let mut state = Progress::default();
    if let Some(out) = child.stdout.take() {
        for line in BufReader::new(out).lines().map_while(Result::ok) {
            if let Some(update) = parse_progress_line(&line, &mut state) {
                on_progress(update);
            }
        }
    }
    let status = child.wait()?;
    let stderr = drain.join().unwrap_or_default();
    Ok(Output {
        status,
        stdout: Vec::new(),
        stderr,
    })
}

/// One sentence naming why a failed ffmpeg run failed, or `None` when nothing recognizable is
/// there (the caller then falls back to ffmpeg's own last lines).
///
/// ffmpeg's real reason is a line somewhere in its stderr ("Permission denied"), followed by a
/// generic "Conversion failed!"; a tail of the stream shows only the latter, and the exit status
/// is an AVERROR (`-13` prints as `0xfffffff3` on Windows). stderr text is matched first because
/// it carries the file name; the exit status is the fallback for when the line scrolled away.
pub fn failure_reason(stderr: &str, exit_code: Option<i32>) -> Option<String> {
    const BY_TEXT: [(&str, &str); 7] = [
        ("permission denied", "ffmpeg is not allowed to write there (permission denied). Check that the output folder is writable."),
        ("read-only file system", "the output folder is read-only."),
        ("no such file or directory", "a file ffmpeg needs was not found. Check the file names."),
        ("no space left on device", "the disk is full."),
        ("invalid data found when processing input", "the input is not a valid media file, or it is damaged."),
        ("unknown encoder", "this ffmpeg build does not include a needed encoder."),
        ("encoder not found", "this ffmpeg build does not include a needed encoder."),
    ];
    let lower = stderr.to_lowercase();
    if let Some((_, sentence)) = BY_TEXT.iter().find(|(needle, _)| lower.contains(needle)) {
        return Some((*sentence).to_string());
    }
    // A Unix shell reports the low byte (243 for -13); Windows reports the whole AVERROR.
    let code = exit_code.map(|c| if (128..=255).contains(&c) { c - 256 } else { c })?;
    match code {
        -13 => Some(BY_TEXT[0].1.to_string()),
        -2 => Some(BY_TEXT[2].1.to_string()),
        -28 => Some(BY_TEXT[3].1.to_string()),
        -1_094_995_529 => Some(BY_TEXT[4].1.to_string()),
        _ => None,
    }
}

/// Does this probe describe a file with anything in it? A video stream or an audio stream.
pub fn has_streams(probe: &Probe) -> bool {
    probe.has_audio || probe.video_codec.is_some() || probe.width.is_some()
}

/// Byte-identical to Python's `require_streams` message — it reaches the user on both runtimes.
pub fn empty_output_error(name: &str) -> String {
    format!(
        "ffmpeg finished but {name} has no audio or video in it — the step produced nothing to \
         work with."
    )
}

/// Fail when ffmpeg exited 0 but wrote a file with no audio and no video. Port of
/// `steps.require_streams`.
///
/// ffmpeg's exit code is not evidence of output: a trim past the end exits 0 with a 185-byte
/// container, and the chain then fails one step later under that file's name. A missing ffprobe
/// is an error (as in Python); any other probe failure is a different question and passes.
pub fn require_streams(output: &Path) -> anyhow::Result<()> {
    if !output.is_file() {
        return Ok(());
    }
    let probe = match run_ffprobe(output) {
        Ok(probe) => probe,
        Err(e) if e.to_string().starts_with("ffprobe not found") => return Err(e),
        Err(_) => return Ok(()),
    };
    if has_streams(&probe) {
        return Ok(());
    }
    let name = output
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| output.to_string_lossy().into_owned());
    anyhow::bail!("{}", empty_output_error(&name))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn progress_lines_fold_into_one_update_per_block() {
        let mut st = Progress::default();
        let block = [
            "frame=120",
            "out_time_us=4000000",
            "speed=2.50x",
            "progress=continue",
        ];
        let updates: Vec<Option<Progress>> = block
            .iter()
            .map(|l| parse_progress_line(l, &mut st))
            .collect();
        assert_eq!(updates[..3], [None, None, None]);
        let done = updates[3].expect("an update at progress=");
        assert!((done.out_seconds - 4.0).abs() < 1e-9);
        assert_eq!(done.speed, Some(2.5));
    }

    #[test]
    fn progress_tolerates_unknown_speed_and_noise() {
        let mut st = Progress::default();
        assert_eq!(parse_progress_line("speed=N/A", &mut st), None);
        assert_eq!(st.speed, None);
        assert_eq!(parse_progress_line("not a key value line", &mut st), None);
        assert_eq!(parse_progress_line("out_time_us=N/A", &mut st), None);
        assert_eq!(st.out_seconds, 0.0);
        assert!(parse_progress_line("progress=end", &mut st).is_some());
    }

    #[test]
    fn a_command_without_an_input_has_no_duration() {
        assert_eq!(input_duration(&["ffmpeg".into(), "-version".into()]), None);
    }

    #[test]
    fn a_permission_line_in_stderr_names_the_cause() {
        let stderr = "[out#0] Error opening output sandbox/x.mp4: Permission denied
Error opening output file x.mp4.
Conversion failed!";
        let reason = failure_reason(stderr, Some(-13)).expect("a reason");
        assert!(reason.contains("permission denied"), "{reason}");
    }

    #[test]
    fn the_exit_status_names_the_cause_when_the_line_is_gone() {
        // The read-only-folder case from the 1.2.0 manual test: only "Conversion failed!" remains.
        let tail = "Error opening output file x.mp4.
Error opening output files: Permission denied";
        assert!(failure_reason(tail, None).is_some());
        let lost = "Conversion failed!";
        assert!(failure_reason(lost, Some(-13))
            .unwrap()
            .contains("permission denied"));
        assert!(failure_reason(lost, Some(243))
            .unwrap()
            .contains("permission denied"));
        assert!(failure_reason(lost, Some(-2))
            .unwrap()
            .contains("not found"));
        assert!(failure_reason(lost, Some(-28))
            .unwrap()
            .contains("disk is full"));
    }

    #[test]
    fn an_unrecognized_failure_has_no_reason() {
        assert_eq!(failure_reason("something odd", Some(1)), None);
        assert_eq!(failure_reason("", None), None);
    }

    #[test]
    fn invalid_input_and_missing_encoders_are_named() {
        assert!(
            failure_reason("Invalid data found when processing input", Some(1))
                .unwrap()
                .contains("damaged")
        );
        assert!(failure_reason("Unknown encoder 'libx265'", Some(1))
            .unwrap()
            .contains("encoder"));
    }

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
    fn ffmpeg_bin_names_ffmpeg_without_env() {
        // The bare name, or wherever the lookup found it; never another program. Reads the env
        // without mutating it (parallel-test safe).
        if std::env::var_os("KNAIF_FFMPEG_BIN").is_none() {
            let bin = ffmpeg_bin();
            assert_eq!(
                Path::new(&bin).file_stem().and_then(|s| s.to_str()),
                Some("ffmpeg"),
                "{bin}"
            );
        }
    }

    #[test]
    fn ffprobe_bin_honours_its_override() {
        // No other test reads KNAIF_FFPROBE_BIN, so setting it here cannot race.
        std::env::set_var("KNAIF_FFPROBE_BIN", "/opt/custom/ffprobe");
        assert_eq!(ffprobe_bin(), "/opt/custom/ffprobe");
        std::env::remove_var("KNAIF_FFPROBE_BIN");
    }

    #[test]
    fn a_probe_with_no_streams_is_empty() {
        // What ffprobe reports for the 185-byte container a past-the-end trim writes.
        let empty = summarise_probe(
            Path::new("Test2.mov"),
            &serde_json::json!({"streams": [], "format": {"format_name": "mov,mp4"}}),
        );
        assert!(!has_streams(&empty));

        let silent_video = summarise_probe(
            Path::new("clip_silent.mp4"),
            &serde_json::json!({"streams": [{"codec_type": "video", "codec_name": "h264",
                                             "width": 1920, "height": 1080}]}),
        );
        assert!(has_streams(&silent_video));

        let audio_only = summarise_probe(
            Path::new("song.mp3"),
            &serde_json::json!({"streams": [{"codec_type": "audio", "codec_name": "mp3"}]}),
        );
        assert!(has_streams(&audio_only));
    }

    #[test]
    fn the_empty_output_message_matches_python() {
        assert_eq!(
            empty_output_error("Test2_intermediate.mov"),
            "ffmpeg finished but Test2_intermediate.mov has no audio or video in it — the step \
             produced nothing to work with."
        );
    }

    #[test]
    fn a_missing_output_is_not_this_checks_business() {
        assert!(require_streams(Path::new("knaif-no-such-output-xyz.mov")).is_ok());
    }
}
