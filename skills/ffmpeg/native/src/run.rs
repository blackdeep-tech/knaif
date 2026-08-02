//! Native intent expansion → dry-run command rendering (Phase 7 execution layer, first slice).
//!
//! [`expand_dry_run`] is the native equivalent of a Python ffmpeg `Intent.expand` followed by
//! the deterministic `build_recipes` → `render_batch_commands` steps, collapsed into one pass
//! because the native `run` verb owns the whole workflow (no intermediate model-visible steps).
//! Given a validated intent step (`tool` + `args`) it maps the args to engine [`Options`], probes
//! each input with the deterministic [`dummy_probe`], and renders the full `ffmpeg` argv per input
//! via `build_one_recipe → build_flags → render_command`. No subprocess, no file access — the
//! execution + confirmation gates land in the next slice.
//!
//! Scope: the single-recipe intents (one `ffmpeg` command per input). `join_videos` (concat /
//! `-filter_complex`) is deferred with real execution.

use std::path::Path;

use serde_json::Value;

use crate::engine::{build_flags, build_one_recipe, dummy_probe, render_command, Options, Probe};
use crate::intent::{normalize_platform, parse_crf_spec};
use crate::profile::{PlatformProfile, QualityProfile};
use crate::FfmpegData;

/// The outcome of expanding an intent for a dry run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Expansion {
    /// One rendered `ffmpeg` argv per input file.
    Commands(Vec<Vec<String>>),
    /// The intent could not proceed and needs a clarifying answer (e.g. an unknown platform).
    Clarify(String),
}

/// How inputs are probed when rendering commands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProbeMode {
    /// Preview: real-probe existing files, but stub a missing/unprobeable one so a chain can still
    /// render (mirrors `inspect_media` under `ctx.dry_run`).
    DryRun,
    /// Execution: real-probe every input; a missing or unprobeable file is a hard error.
    Execute,
}

/// Probe one input per [`ProbeMode`]: real `ffprobe` for an existing file; a [`dummy_probe`]
/// fallback in dry-run; a hard error in execute mode. Port of the `inspect_media` probe policy.
fn probe_input(path: &Path, data: &FfmpegData, mode: ProbeMode) -> anyhow::Result<Probe> {
    if path.exists() {
        match crate::exec::run_ffprobe(path) {
            Ok(probe) => return Ok(probe),
            Err(e) => {
                if mode == ProbeMode::Execute {
                    return Err(e);
                }
            }
        }
    } else if mode == ProbeMode::Execute {
        anyhow::bail!("input not found: {}", path.display());
    }
    Ok(dummy_probe(path, &data.vocab))
}

/// The resolved plan for one intent: the inputs, engine [`Options`], and the profiles to build
/// each recipe against. Intermediate between the arg-mapping and the per-input render loop.
struct Resolved {
    inputs: Vec<String>,
    options: Options,
    platform: Option<PlatformProfile>,
    quality: Option<QualityProfile>,
}

/// Expand a validated ffmpeg intent step into the `ffmpeg` command(s) it would run, previewing with
/// stubbed probes for missing files (dry-run). See [`expand`] for the general form.
pub fn expand_dry_run(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    data: &FfmpegData,
    sandbox: Option<&Path>,
) -> anyhow::Result<Expansion> {
    expand(tool, args, data, sandbox, ProbeMode::DryRun)
}

/// Expand a validated ffmpeg intent step, real-probing every input (execute mode: a missing or
/// unprobeable file is a hard error). The rendered commands are ready to run.
pub fn expand_execute(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    data: &FfmpegData,
    sandbox: Option<&Path>,
) -> anyhow::Result<Expansion> {
    expand(tool, args, data, sandbox, ProbeMode::Execute)
}

/// Expand a validated ffmpeg intent step into the `ffmpeg` command(s) it would run.
///
/// `tool` is the intent name (`compress_video`, …); `args` is the step's already-validated arg
/// object. `sandbox` mirrors `ctx.sandbox` — `None` is open/CLI mode. `mode` selects the probe
/// policy. Errors surface the same deterministic failures the Python engine raises (unknown
/// profile, unrenderable scale, …).
pub fn expand(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    data: &FfmpegData,
    sandbox: Option<&Path>,
    mode: ProbeMode,
) -> anyhow::Result<Expansion> {
    // concat_video joins N inputs into ONE command — it doesn't fit the per-input recipe shape.
    if tool == "concat_video" {
        return expand_concat(args, data, sandbox, mode);
    }
    let resolved = match resolve_intent(tool, args, data)? {
        Ok(r) => r,
        Err(clarify) => return Ok(Expansion::Clarify(clarify)),
    };
    let mut commands = Vec::with_capacity(resolved.inputs.len());
    for input in &resolved.inputs {
        let probe = probe_input(Path::new(input), data, mode)?;
        let recipe = build_one_recipe(
            &probe,
            resolved.platform.as_ref(),
            resolved.quality.as_ref(),
            &resolved.options,
            &data.vocab,
            sandbox,
        )?;
        let (pre, post) = build_flags(&recipe, &data.vocab)?;
        commands.push(render_command(&recipe, &pre, &post, None));
    }
    Ok(Expansion::Commands(commands))
}

/// Expand `concat_video`: assemble the ordered input list (`inputs`, or `base` + `append`), probe
/// each, resolve the output path (against the sandbox/cwd, boundary-checked), and render the single
/// `-filter_complex concat` command. Port of `ConcatVideoIntent` + `RunConcatStep`.
fn expand_concat(
    args: &serde_json::Map<String, Value>,
    data: &FfmpegData,
    sandbox: Option<&Path>,
    mode: ProbeMode,
) -> anyhow::Result<Expansion> {
    let inputs = assemble_concat_inputs(args)?;
    if inputs.is_empty() {
        anyhow::bail!("concat_video requires either 'inputs' or 'base'/'append' args.");
    }
    let output = str_arg(args, "output").unwrap_or_else(|| "combined.mp4".to_string());
    let out_path = resolve_output(&output, sandbox)?;
    crate::engine::assert_in_sandbox(&out_path, sandbox)?;

    let mut infos = Vec::with_capacity(inputs.len());
    for input in &inputs {
        let probe = probe_input(Path::new(input), data, mode)?;
        infos.push(crate::concat::ConcatInfo::from_probe(&probe));
    }
    let cmd = crate::concat::build_concat_command(
        &inputs,
        &infos,
        str_arg(args, "target_resolution").as_deref(),
        str_arg(args, "target_fps").as_deref(),
        &out_path.to_string_lossy(),
        &data.vocab,
    )?;
    Ok(Expansion::Commands(vec![cmd]))
}

/// The ordered concat input list: `base` ++ `append` when either is present, else `inputs`. Port of
/// `_assemble_concat_inputs` (native single-shot: no `$var` / `{files:…}` runtime forms).
fn assemble_concat_inputs(args: &serde_json::Map<String, Value>) -> anyhow::Result<Vec<String>> {
    let non_empty = |v: &Value| !v.is_null();
    if args.contains_key("base") || args.contains_key("append") {
        let mut inputs = Vec::new();
        if let Some(base) = args.get("base").filter(|v| non_empty(v)) {
            inputs.extend(coerce_inputs(Some(base))?);
        }
        if let Some(append) = args.get("append").filter(|v| non_empty(v)) {
            inputs.extend(coerce_inputs(Some(append))?);
        }
        Ok(inputs)
    } else {
        coerce_inputs(args.get("inputs"))
    }
}

/// Resolve a (possibly relative) output path against the sandbox, else cwd (open mode).
fn resolve_output(output: &str, sandbox: Option<&Path>) -> anyhow::Result<std::path::PathBuf> {
    let p = Path::new(output);
    if p.is_absolute() {
        return Ok(p.to_path_buf());
    }
    let base = match sandbox {
        Some(s) => s.to_path_buf(),
        None => std::env::current_dir()?,
    };
    Ok(base.join(p))
}

/// Map one intent's args to its [`Resolved`] plan. `Ok(Ok(..))` = ready to render; `Ok(Err(msg))` =
/// a clarify is needed (e.g. an unknown platform); `Err` = a hard/unknown-tool error.
fn resolve_intent(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    data: &FfmpegData,
) -> anyhow::Result<Result<Resolved, String>> {
    let mut options = Options::default();
    let mut platform: Option<PlatformProfile> = None;
    let mut quality: Option<QualityProfile> = None;

    let inputs = match tool {
        // `trim_video` / `create_thumbnail` take a singular `input`; the rest take `inputs`.
        "trim_video" | "create_thumbnail" => coerce_inputs(args.get("input"))?,
        _ => coerce_inputs(args.get("inputs"))?,
    };

    match tool {
        "prepare_for_platform" => {
            let raw = str_arg(args, "platform").unwrap_or_default();
            match resolve_platform_profile(&raw, data) {
                Some((name, profile)) => {
                    options.mode = Some("platform".into());
                    options.platform = Some(name);
                    platform = Some(profile);
                }
                None => return Ok(Err(platform_clarify(&raw, data))),
            }
            quality = Some(resolve_quality_profile(
                &str_arg(args, "quality").unwrap_or_else(|| "visually_good".into()),
                data,
            )?);
        }
        "compress_video" => {
            options.mode = Some("compress".into());
            if let Some(raw) = str_arg(args, "target") {
                match resolve_platform_profile(&raw, data) {
                    Some((name, profile)) => {
                        options.platform = Some(name);
                        platform = Some(profile);
                    }
                    None => return Ok(Err(platform_clarify(&raw, data))),
                }
            }
            if let Some(size) = f64_arg(args, "target_size_mb") {
                options.target_size_mb = Some(size);
            }
            set_output(&mut options, args);
            let q = quality_from_crf(args.get("crf"), str_arg(args, "quality"))
                .unwrap_or_else(|| "small_file".into());
            quality = Some(resolve_quality_profile(&q, data)?);
        }
        "convert_video" => {
            let output = str_arg(args, "output");
            let mut container = str_arg(args, "container");
            let mut video_codec = str_arg(args, "video_codec");
            // A model sometimes slots a video codec token ("av1", "hevc") into the container arg.
            if let Some(c) = &container {
                if video_codec.is_none() && data.vocab.video_encoder_map.contains_key(&c.to_lowercase())
                {
                    video_codec = Some(c.to_lowercase());
                    container = None;
                }
            }
            let container = container
                .or_else(|| container_from_output(output.as_deref(), data))
                .unwrap_or_else(|| "mp4".into());
            let audio_codec = str_arg(args, "audio_codec");
            let q = quality_from_crf(args.get("crf"), str_arg(args, "quality"));
            let remux = video_codec.is_none() && audio_codec.is_none() && q.is_none();

            options.mode = Some("convert".into());
            options.container = Some(container);
            set_output(&mut options, args);
            if remux {
                options.remux = true;
            }
            if !remux && audio_codec.is_none() {
                options.copy_audio = true;
            }
            if let Some(ac) = audio_codec {
                options.audio_codec = Some(ac);
            }
            if let Some(vc) = video_codec {
                options.video_encoder =
                    Some(data.vocab.video_encoder_map.get(&vc).cloned().unwrap_or(vc));
            }
            if let (Some(q), false) = (q, remux) {
                quality = Some(resolve_quality_profile(&q, data)?);
            }
        }
        "resize_video" => {
            options.mode = Some("resize".into());
            options.copy_audio = true;
            options.width = dimension_arg(args, "width");
            options.height = dimension_arg(args, "height");
            let mut fit = str_arg(args, "fit");
            // Legacy keep_aspect_ratio=false → stretch (explicit distort request).
            if !bool_arg(args, "keep_aspect_ratio").unwrap_or(true) {
                fit = fit.or_else(|| Some("stretch".into()));
            }
            options.fit = fit;
            options.aspect = str_arg(args, "aspect");
            set_output(&mut options, args);
            quality = Some(resolve_quality_profile(
                &str_arg(args, "quality").unwrap_or_else(|| "visually_good".into()),
                data,
            )?);
        }
        "trim_video" => {
            options.mode = Some("trim".into());
            options.start = str_arg(args, "start");
            options.duration = str_arg(args, "duration");
            options.end = str_arg(args, "end");
            set_output(&mut options, args);
            quality = Some(resolve_quality_profile(
                &str_arg(args, "quality").unwrap_or_else(|| "visually_good".into()),
                data,
            )?);
        }
        "extract_audio" => {
            let output = str_arg(args, "output");
            let audio_format = str_arg(args, "audio_format")
                .or_else(|| str_arg(args, "format"))
                .or_else(|| str_arg(args, "container"))
                .or_else(|| audio_format_from_output(output.as_deref(), data))
                .unwrap_or_else(|| "mp3".into());
            let bitrate = coerce_bitrate(str_arg(args, "bitrate").as_deref())
                .or_else(|| bitrate_from_quality(str_arg(args, "quality").as_deref()));
            options.mode = Some("extract_audio".into());
            options.audio_format = Some(audio_format);
            options.audio_bitrate = bitrate;
            options.start = str_arg(args, "start");
            options.end = str_arg(args, "end");
            set_output(&mut options, args);
        }
        "create_thumbnail" => {
            let output = str_arg(args, "output");
            let image_format = image_format_from_output(
                output.as_deref(),
                &str_arg(args, "image_format").unwrap_or_else(|| "jpg".into()),
                data,
            );
            options.mode = Some("thumbnail".into());
            options.at_time = Some(str_arg(args, "at_time").unwrap_or_else(|| "00:00:01".into()));
            options.image_format = Some(image_format);
            options.scale = str_arg(args, "scale");
            set_output(&mut options, args);
        }
        "strip_audio" => {
            options.mode = Some("strip_audio".into());
            set_output(&mut options, args);
        }
        "adjust_speed" => {
            options.mode = Some("adjust_speed".into());
            options.speed = Some(
                f64_arg(args, "speed")
                    .ok_or_else(|| anyhow::anyhow!("adjust_speed: 'speed' must be a number"))?,
            );
            set_output(&mut options, args);
            quality = Some(resolve_quality_profile(
                &str_arg(args, "quality").unwrap_or_else(|| "visually_good".into()),
                data,
            )?);
        }
        "adjust_volume" => {
            options.mode = Some("adjust_volume".into());
            options.level = str_arg(args, "level");
            options.normalize = bool_arg(args, "normalize").unwrap_or(false);
            set_output(&mut options, args);
            quality = Some(resolve_quality_profile(
                &str_arg(args, "quality").unwrap_or_else(|| "balanced".into()),
                data,
            )?);
        }
        "rotate_video" => {
            options.mode = Some("rotate".into());
            options.angle = args.get("angle").and_then(Value::as_u64).map(|a| a as u32);
            options.flip = str_arg(args, "flip");
            set_output(&mut options, args);
            quality = Some(resolve_quality_profile(
                &str_arg(args, "quality").unwrap_or_else(|| "balanced".into()),
                data,
            )?);
        }
        other => anyhow::bail!(
            "ffmpeg intent {other:?} has no native dry-run expansion yet (join_videos + execution land next)"
        ),
    }

    Ok(Ok(Resolved {
        inputs,
        options,
        platform,
        quality,
    }))
}

// ── arg accessors + coercions (ports of the `_engine.py` helpers) ────────────────────────────

/// `_coerce_inputs`: a string → one-element list; a list → its stringified items.
fn coerce_inputs(value: Option<&Value>) -> anyhow::Result<Vec<String>> {
    match value {
        Some(Value::String(s)) => Ok(vec![s.clone()]),
        Some(Value::Array(items)) => Ok(items.iter().map(value_to_string).collect()),
        _ => anyhow::bail!("'inputs' must be a string or list of strings."),
    }
}

fn value_to_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn str_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<String> {
    args.get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|s| !s.is_empty())
}

fn bool_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<bool> {
    args.get(key).and_then(Value::as_bool)
}

/// A number arg, tolerating a numeric string (`"2.0"`) the way Python's `float(...)` would.
fn f64_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<f64> {
    match args.get(key) {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => s.trim().parse().ok(),
        _ => None,
    }
}

/// `_coerce_dimension`: int passes through; a string like `"480"` / `"480p"` parses to an int.
fn dimension_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<u32> {
    match args.get(key) {
        Some(Value::Number(n)) => n.as_u64().map(|v| v as u32),
        Some(Value::String(s)) => {
            let s = s.trim().to_lowercase();
            let s = s.strip_suffix('p').unwrap_or(&s);
            s.parse().ok()
        }
        _ => None,
    }
}

/// `_quality_from_crf`: a numeric crf → `"crf N"`; a leaked word/string passes through; `None` →
/// the fallback (which may itself be `None`).
fn quality_from_crf(crf: Option<&Value>, fallback: Option<String>) -> Option<String> {
    match crf {
        None | Some(Value::Null) => fallback,
        Some(Value::Number(n)) => Some(format!("crf {}", n.as_i64().unwrap_or(0))),
        Some(Value::String(s)) => {
            let s = s.trim();
            if s.is_empty() {
                fallback
            } else {
                Some(s.to_string())
            }
        }
        Some(other) => Some(other.to_string()),
    }
}

fn ext_of(output: &str) -> Option<String> {
    Path::new(output)
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_lowercase)
}

/// `_container_from_output`: the output extension iff it is a known video container.
fn container_from_output(output: Option<&str>, data: &FfmpegData) -> Option<String> {
    let ext = output.and_then(ext_of)?;
    data.vocab.video_containers.contains(&ext).then_some(ext)
}

/// `_audio_format_from_output`: the output extension iff it is a known audio extension.
fn audio_format_from_output(output: Option<&str>, data: &FfmpegData) -> Option<String> {
    let ext = output.and_then(ext_of)?;
    data.vocab.audio_ext_codec.contains_key(&ext).then_some(ext)
}

/// `_image_format_from_output`: infer only when the caller left the default `"jpg"`.
fn image_format_from_output(output: Option<&str>, default: &str, data: &FfmpegData) -> String {
    if default == "jpg" {
        if let Some(ext) = output.and_then(ext_of) {
            if data.vocab.image_extensions.contains(&ext) {
                return ext;
            }
        }
    }
    default.to_string()
}

/// `_BITRATE_RE`: `^\s*(\d{1,4})\s*k(?:b(?:ps)?)?\s*$` (case-insensitive) → `"<n>k"`.
fn bitrate_shaped(s: &str) -> Option<String> {
    let t = s.trim().to_lowercase();
    let digits: String = t.chars().take_while(|c| c.is_ascii_digit()).collect();
    if digits.is_empty() || digits.len() > 4 {
        return None;
    }
    let rest = t[digits.len()..].trim_start();
    if matches!(rest, "k" | "kb" | "kbps") {
        Some(format!("{digits}k"))
    } else {
        None
    }
}

/// `_coerce_bitrate` / `_bitrate_from_quality` share the same shape guard.
fn coerce_bitrate(value: Option<&str>) -> Option<String> {
    value.and_then(bitrate_shaped)
}

fn bitrate_from_quality(quality: Option<&str>) -> Option<String> {
    quality.and_then(bitrate_shaped)
}

/// If `output` is set, route it into `options.output_path`.
fn set_output(options: &mut Options, args: &serde_json::Map<String, Value>) {
    if let Some(out) = str_arg(args, "output") {
        options.output_path = Some(out);
    }
}

// ── profile resolution (ports of the load_*_profile steps) ───────────────────────────────────

/// Resolve a platform name (after alias normalization) to `(canonical_name, profile)`, or `None`
/// when there is no such profile — the caller turns `None` into a clarify.
fn resolve_platform_profile(raw: &str, data: &FfmpegData) -> Option<(String, PlatformProfile)> {
    let name = normalize_platform(raw, &data.vocab);
    data.platforms.get(&name).cloned().map(|p| (name, p))
}

/// `_platform_clarify` message: names the unknown platform + the supported set.
fn platform_clarify(raw: &str, data: &FfmpegData) -> String {
    let mut supported: Vec<&str> = data.platforms.keys().map(String::as_str).collect();
    supported.sort_unstable();
    format!(
        "I don't have a platform profile for {raw:?}. Supported platforms: {}. Which would you like?",
        supported.join(", ")
    )
}

/// `LoadQualityProfileStep`: a `crf N` spec loads the nearest profile and overrides its CRF with
/// the exact value; otherwise an exact profile-name lookup (error if unknown).
fn resolve_quality_profile(quality: &str, data: &FfmpegData) -> anyhow::Result<QualityProfile> {
    if let Some(crf) = parse_crf_spec(quality) {
        let name = crate::intent::crf_to_profile_name(crf as i64);
        let mut profile = data
            .quality
            .get(name)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("Unknown quality profile: {name:?}"))?;
        profile.video_crf = crf as i32;
        Ok(profile)
    } else {
        data.quality
            .get(quality)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("Unknown quality profile: {quality:?}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn data() -> FfmpegData {
        FfmpegData::load(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg"))
            .unwrap()
    }

    fn args(json: Value) -> serde_json::Map<String, Value> {
        json.as_object().unwrap().clone()
    }

    fn only_cmd(exp: Expansion) -> Vec<String> {
        match exp {
            Expansion::Commands(mut c) => {
                assert_eq!(c.len(), 1, "expected exactly one command");
                c.remove(0)
            }
            Expansion::Clarify(q) => panic!("expected commands, got clarify: {q}"),
        }
    }

    fn cmd(tool: &str, json: Value) -> Vec<String> {
        only_cmd(expand_dry_run(tool, &args(json), &data(), None).unwrap())
    }

    // Ground truth captured from the Python engine (skills/ffmpeg/python/_engine.py) for the same
    // intent args — the native path must render byte-identical argv.

    #[test]
    fn prepare_for_platform_youtube() {
        assert_eq!(
            cmd(
                "prepare_for_platform",
                serde_json::json!({"inputs": "clip.mp4", "platform": "youtube"})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "clip.mp4",
                "-vf",
                "scale=3840:2160",
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "clip_youtube.mp4"
            ]
        );
    }

    #[test]
    fn compress_basic() {
        assert_eq!(
            cmd("compress_video", serde_json::json!({"inputs": "v.mp4"})),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "v.mp4",
                "-c:v",
                "libx264",
                "-crf",
                "28",
                "-preset",
                "slow",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                "v_compressed.mp4"
            ]
        );
    }

    #[test]
    fn convert_webm_re_encodes_rather_than_remuxing() {
        // webm accepts only vp8/vp9/av1, so `-c copy` from an h264 source makes ffmpeg exit 1 and
        // leave a truncated file behind.
        //
        // This test used to assert exactly that broken command, under the name
        // `convert_webm_remux`. It locked in what the engine did rather than what ffmpeg accepts,
        // which is how the defect survived a suite that otherwise covers conversion thoroughly —
        // and why it took running the packaged artifact against a real file to find it. The corpus
        // never agreed with it: all five webm rows re-encode, and ffmpeg_095's success_criteria
        // demands vp9 + opus.
        assert_eq!(
            cmd(
                "convert_video",
                serde_json::json!({"inputs": "a.mov", "container": "webm"})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "a.mov",
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "libopus",
                "-b:a",
                "128k",
                "a_converted.webm"
            ]
        );
    }

    #[test]
    fn convert_mkv_still_remuxes() {
        // The guard must not over-correct. mkv and mov accept h264, so a container-only change
        // stays a verbatim stream copy — fast, lossless, and what ffmpeg_076 locks in. Pairing this
        // with the webm case is what distinguishes "stop emitting an impossible command" from
        // "stop remuxing", which would be a real regression.
        assert_eq!(
            cmd(
                "convert_video",
                serde_json::json!({"inputs": "a.mov", "container": "mkv"})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "a.mov",
                "-c",
                "copy",
                "a_converted.mkv"
            ]
        );
    }

    #[test]
    fn resize_height_720() {
        assert_eq!(
            cmd(
                "resize_video",
                serde_json::json!({"inputs": "r.mp4", "height": 720})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "r.mp4",
                "-vf",
                "scale=-2:720",
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                "r_resized.mp4"
            ]
        );
    }

    #[test]
    fn trim_start_duration() {
        assert_eq!(
            cmd(
                "trim_video",
                serde_json::json!({"input": "t.mp4", "start": "00:00:05", "duration": "10"})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-ss",
                "00:00:05",
                "-i",
                "t.mp4",
                "-t",
                "10",
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "t_trimmed.mp4"
            ]
        );
    }

    #[test]
    fn extract_audio_mp3() {
        assert_eq!(
            cmd(
                "extract_audio",
                serde_json::json!({"inputs": "s.mp4", "audio_format": "mp3"})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "s.mp4",
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                "s_audio.mp3"
            ]
        );
    }

    #[test]
    fn thumbnail_at_time() {
        assert_eq!(
            cmd(
                "create_thumbnail",
                serde_json::json!({"input": "m.mp4", "at_time": "00:00:03"})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "m.mp4",
                "-ss",
                "00:00:03",
                "-vframes",
                "1",
                "m_thumb.jpg"
            ]
        );
    }

    #[test]
    fn strip_audio_basic() {
        assert_eq!(
            cmd("strip_audio", serde_json::json!({"inputs": "n.mp4"})),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "n.mp4",
                "-an",
                "-c:v",
                "copy",
                "n_silent.mp4"
            ]
        );
    }

    #[test]
    fn adjust_speed_2x() {
        assert_eq!(
            cmd(
                "adjust_speed",
                serde_json::json!({"inputs": "f.mp4", "speed": 2.0})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "f.mp4",
                "-vf",
                "setpts=0.5*PTS",
                "-af",
                "atempo=2.0",
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-preset",
                "medium",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "f_speed.mp4"
            ]
        );
    }

    #[test]
    fn rotate_90() {
        assert_eq!(
            cmd(
                "rotate_video",
                serde_json::json!({"inputs": "g.mp4", "angle": 90})
            ),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "g.mp4",
                "-vf",
                "transpose=1",
                "-c:v",
                "libx264",
                "-crf",
                "25",
                "-preset",
                "medium",
                "-c:a",
                "aac",
                "g_rotated.mp4"
            ]
        );
    }

    #[test]
    fn multiple_inputs_render_one_command_each() {
        let exp = expand_dry_run(
            "strip_audio",
            &args(serde_json::json!({"inputs": ["a.mp4", "b.mp4"]})),
            &data(),
            None,
        )
        .unwrap();
        match exp {
            Expansion::Commands(c) => {
                assert_eq!(c.len(), 2);
                assert_eq!(c[0].last().unwrap(), "a_silent.mp4");
                assert_eq!(c[1].last().unwrap(), "b_silent.mp4");
            }
            Expansion::Clarify(q) => panic!("unexpected clarify: {q}"),
        }
    }

    #[test]
    fn unknown_platform_clarifies() {
        let exp = expand_dry_run(
            "prepare_for_platform",
            &args(serde_json::json!({"inputs": "c.mp4", "platform": "myspace"})),
            &data(),
            None,
        )
        .unwrap();
        match exp {
            Expansion::Clarify(q) => {
                assert!(q.contains("myspace"), "clarify names the platform: {q}")
            }
            Expansion::Commands(_) => panic!("expected a clarify for an unknown platform"),
        }
    }

    #[test]
    fn unknown_tool_errors() {
        assert!(expand_dry_run("not_a_tool", &args(serde_json::json!({})), &data(), None).is_err());
    }

    #[test]
    fn sandbox_escape_is_rejected() {
        let sandbox = Path::new("/work/sandbox");
        let err = expand_dry_run(
            "strip_audio",
            &args(serde_json::json!({"inputs": "/etc/passwd.mp4"})),
            &data(),
            Some(sandbox),
        );
        assert!(err.is_err(), "output outside the sandbox must be rejected");
    }
}
