//! `concat_video` command construction — the one ffmpeg intent that joins N inputs into a single
//! `-filter_complex concat` command (every other intent renders one command per input). Faithful
//! port of `_concat_filter_args` + `RunConcatStep`'s command assembly. Pure over per-input
//! [`ConcatInfo`]; the arg wiring + probe policy live in [`crate::run`].

use crate::engine::{parse_scale, Probe};
use crate::Vocab;

/// Per-input info the concat normalizer reads (subset of a [`Probe`]).
#[derive(Debug, Clone)]
pub struct ConcatInfo {
    pub has_audio: bool,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub fps: Option<f64>,
    pub duration: Option<f64>,
}

impl ConcatInfo {
    pub fn from_probe(p: &Probe) -> Self {
        Self {
            has_audio: p.has_audio,
            width: p.width,
            height: p.height,
            fps: p.fps,
            duration: p.duration,
        }
    }
}

/// Format an fps like Python `str(int(f)) if f == int(f) else str(f)`: `30.0` → `"30"`, else `"29.97"`.
fn fps_str(fps: f64) -> String {
    if fps.fract() == 0.0 {
        format!("{}", fps as i64)
    } else {
        format!("{fps}")
    }
}

/// Format a duration keeping Python's trailing `.0` for whole numbers (`45.0` → `"45.0"`).
fn dur_str(d: f64) -> String {
    let s = format!("{d}");
    if s.contains(['.', 'e', 'E']) {
        s
    } else {
        format!("{s}.0")
    }
}

/// Build the `-filter_complex … -map …` args for a concat over `infos`. Port of `_concat_filter_args`
/// (probes-supplied branch): normalize (scale+fps+aresample, silence-pad missing audio) when any
/// input mismatches the target or a target is forced; otherwise the minimal concat.
pub fn concat_filter_args(
    infos: &[ConcatInfo],
    target_resolution: Option<&str>,
    target_fps: Option<&str>,
    vocab: &Vocab,
) -> anyhow::Result<Vec<String>> {
    let any_audio = infos.iter().any(|i| i.has_audio);
    let n = infos.len();

    // ── Resolve target resolution ────────────────────────────────────────────
    let mut forced_res = false;
    let (mut target_w, mut target_h): (Option<u32>, Option<u32>) = (None, None);
    if let Some(tr) = target_resolution.filter(|t| *t != "first") {
        forced_res = true;
        if tr == "second" && infos.len() >= 2 {
            target_w = infos[1].width;
            target_h = infos[1].height;
        } else if let Some(parsed) = parse_scale(Some(tr), vocab)? {
            if let Some((w, h)) = parsed.split_once(':') {
                target_w = w.parse().ok();
                target_h = h.parse().ok();
            }
        }
    }
    if target_w.is_none() {
        target_w = infos.iter().find_map(|i| i.width);
        target_h = infos.iter().find_map(|i| i.height);
    }

    // ── Resolve target fps ───────────────────────────────────────────────────
    let mut forced_fps = false;
    let mut target_fps_val: Option<f64> = None;
    if let Some(tf) = target_fps.filter(|t| *t != "first") {
        forced_fps = true;
        if tf == "second" && infos.len() >= 2 {
            target_fps_val = infos[1].fps;
        } else {
            target_fps_val = tf.parse().ok();
        }
    }
    if target_fps_val.is_none() {
        target_fps_val = infos.iter().find_map(|i| i.fps);
    }

    // ── Decide whether to normalize ──────────────────────────────────────────
    let any_res_mismatch = infos.iter().any(|i| {
        matches!(
            (target_w, target_h, i.width, i.height),
            (Some(tw), Some(th), Some(iw), Some(ih)) if iw != tw || ih != th
        )
    });
    let any_fps_mismatch = infos
        .iter()
        .any(|i| matches!((target_fps_val, i.fps), (Some(tf), Some(f)) if f != tf));
    let normalize = forced_res || forced_fps || any_res_mismatch || any_fps_mismatch;

    // ── Build filter_complex ─────────────────────────────────────────────────
    let mut filter_parts: Vec<String> = Vec::new();
    let mut video_labels: Vec<String> = Vec::new();
    let mut audio_labels: Vec<String> = Vec::new();

    let silence = |i: usize, dur: Option<f64>| {
        let d = dur
            .map(|d| format!(":d={}", dur_str(d)))
            .unwrap_or_default();
        (
            format!("anullsrc=r=44100:cl=stereo{d}[_silence{i}]"),
            format!("[_silence{i}]"),
        )
    };

    if normalize {
        let fps_label = target_fps_val.map(fps_str);
        for (i, info) in infos.iter().enumerate() {
            let mut v_filters: Vec<String> = Vec::new();
            if let (Some(w), Some(h)) = (target_w, target_h) {
                v_filters.push(format!("scale={w}:{h}"));
            }
            if let Some(f) = &fps_label {
                v_filters.push(format!("fps={f}"));
            }
            if v_filters.is_empty() {
                video_labels.push(format!("[{i}:v]"));
            } else {
                filter_parts.push(format!("[{i}:v]{}[v{i}]", v_filters.join(",")));
                video_labels.push(format!("[v{i}]"));
            }
            if any_audio {
                if info.has_audio {
                    filter_parts.push(format!("[{i}:a]aresample=44100[a{i}]"));
                    audio_labels.push(format!("[a{i}]"));
                } else {
                    let (part, label) = silence(i, info.duration);
                    filter_parts.push(part);
                    audio_labels.push(label);
                }
            }
        }
    } else {
        for (i, info) in infos.iter().enumerate() {
            video_labels.push(format!("[{i}:v]"));
            if any_audio && !info.has_audio {
                let (part, label) = silence(i, info.duration);
                filter_parts.push(part);
                audio_labels.push(label);
            } else {
                audio_labels.push(if any_audio {
                    format!("[{i}:a]")
                } else {
                    String::new()
                });
            }
        }
    }

    let concat_inputs: String = (0..n)
        .map(|i| format!("{}{}", video_labels[i], audio_labels[i]))
        .collect();
    let a_flag = if any_audio { 1 } else { 0 };
    let out_labels = if any_audio { "[outv][outa]" } else { "[outv]" };
    filter_parts.push(format!(
        "{concat_inputs}concat=n={n}:v=1:a={a_flag}{out_labels}"
    ));

    let mut result = vec![
        "-filter_complex".to_string(),
        filter_parts.join(";"),
        "-map".to_string(),
        "[outv]".to_string(),
    ];
    if any_audio {
        result.push("-map".to_string());
        result.push("[outa]".to_string());
    }
    Ok(result)
}

/// Assemble the full `ffmpeg` argv for a concat: `ffmpeg -y -i in0 -i in1 … <filter args> <output>`.
pub fn build_concat_command(
    inputs: &[String],
    infos: &[ConcatInfo],
    target_resolution: Option<&str>,
    target_fps: Option<&str>,
    output: &str,
    vocab: &Vocab,
) -> anyhow::Result<Vec<String>> {
    let mut cmd = vec!["ffmpeg".to_string(), "-y".to_string()];
    for inp in inputs {
        cmd.push("-i".to_string());
        cmd.push(inp.clone());
    }
    cmd.extend(concat_filter_args(
        infos,
        target_resolution,
        target_fps,
        vocab,
    )?);
    cmd.push(output.to_string());
    Ok(cmd)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn vocab() -> Vocab {
        Vocab::load(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg/vocab.yaml"),
        )
        .unwrap()
    }

    fn info(has_audio: bool, w: u32, h: u32, fps: f64, dur: f64) -> ConcatInfo {
        ConcatInfo {
            has_audio,
            width: Some(w),
            height: Some(h),
            fps: Some(fps),
            duration: Some(dur),
        }
    }

    fn cmd(infos: &[ConcatInfo], tr: Option<&str>, tf: Option<&str>) -> Vec<String> {
        build_concat_command(
            &["a.mp4".into(), "b.mp4".into()],
            infos,
            tr,
            tf,
            "combined.mp4",
            &vocab(),
        )
        .unwrap()
    }

    // Ground truth captured from the Python engine (`_concat_filter_args`) for the same infos.

    #[test]
    fn two_same_size_minimal_concat() {
        let v = info(true, 1920, 1080, 30.0, 60.0);
        assert_eq!(
            cmd(&[v.clone(), v], None, None),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "a.mp4",
                "-i",
                "b.mp4",
                "-filter_complex",
                "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]",
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "combined.mp4"
            ]
        );
    }

    #[test]
    fn size_mismatch_forces_scale_and_fps_normalize() {
        let infos = [
            info(true, 1920, 1080, 30.0, 60.0),
            info(true, 1280, 720, 30.0, 45.0),
        ];
        assert_eq!(
            cmd(&infos, None, None),
            vec!["ffmpeg","-y","-i","a.mp4","-i","b.mp4","-filter_complex","[0:v]scale=1920:1080,fps=30[v0];[0:a]aresample=44100[a0];[1:v]scale=1920:1080,fps=30[v1];[1:a]aresample=44100[a1];[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]","-map","[outv]","-map","[outa]","combined.mp4"]
        );
    }

    #[test]
    fn forced_target_resolution() {
        let v = info(true, 1920, 1080, 30.0, 60.0);
        assert_eq!(
            cmd(&[v.clone(), v], Some("720p"), None),
            vec!["ffmpeg","-y","-i","a.mp4","-i","b.mp4","-filter_complex","[0:v]scale=1280:720,fps=30[v0];[0:a]aresample=44100[a0];[1:v]scale=1280:720,fps=30[v1];[1:a]aresample=44100[a1];[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]","-map","[outv]","-map","[outa]","combined.mp4"]
        );
    }

    #[test]
    fn missing_audio_input_gets_silence_padding() {
        let infos = [
            info(true, 1920, 1080, 30.0, 60.0),
            info(false, 1920, 1080, 30.0, 45.0),
        ];
        assert_eq!(
            cmd(&infos, None, None),
            vec!["ffmpeg","-y","-i","a.mp4","-i","b.mp4","-filter_complex","anullsrc=r=44100:cl=stereo:d=45.0[_silence1];[0:v][0:a][1:v][_silence1]concat=n=2:v=1:a=1[outv][outa]","-map","[outv]","-map","[outa]","combined.mp4"]
        );
    }

    #[test]
    fn no_audio_anywhere_video_only_concat() {
        let v = info(false, 1920, 1080, 30.0, 60.0);
        assert_eq!(
            cmd(&[v.clone(), v], None, None),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "a.mp4",
                "-i",
                "b.mp4",
                "-filter_complex",
                "[0:v][1:v]concat=n=2:v=1:a=0[outv]",
                "-map",
                "[outv]",
                "combined.mp4"
            ]
        );
    }
}
