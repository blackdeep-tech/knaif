//! The imperative ffmpeg engine (Tier 3 port of `_engine.py`).
//!
//! The recipe→flags logic is genuinely algorithmic (arithmetic, enum→filter mappings,
//! probe-driven branches), so it is ported as code rather than a declarative DSL; the shared
//! *data* it reads lives in [`crate::Vocab`] / profiles (Tier 1). Correctness against the
//! Python engine is pinned by the eval-parity job. The full recipe pipeline is ported:
//! [`build_one_recipe`] (probe + profiles + options → [`Recipe`]) → [`build_flags`] →
//! [`render_command`] (full `ffmpeg` argv).

use std::path::{Component, Path, PathBuf};

use crate::{PlatformProfile, QualityProfile, Vocab};

/// True when `s` matches Python `_VOLUME_NUMERIC_RE` = `^-?\d+(?:\.\d+)?(?:db)?$` (case-insensitive):
/// an optional sign, digits, optional fraction, optional `db` suffix.
fn is_numeric_volume(s: &str) -> bool {
    let core = s.strip_prefix('-').unwrap_or(s);
    let core = match core.len().checked_sub(2) {
        Some(cut) if core[cut..].eq_ignore_ascii_case("db") => &core[..cut],
        _ => core,
    };
    let mut parts = core.splitn(2, '.');
    let int_part = parts.next().unwrap_or("");
    if int_part.is_empty() || !int_part.bytes().all(|c| c.is_ascii_digit()) {
        return false;
    }
    match parts.next() {
        Some(frac) => !frac.is_empty() && frac.bytes().all(|c| c.is_ascii_digit()),
        None => true,
    }
}

/// Coerce a model-supplied volume `level` into a valid ffmpeg `volume=` value. Faithful port of
/// `_coerce_volume_level`: numeric/dB levels pass through (after normalizing a Unicode minus);
/// known direction words (from [`Vocab`]) map to a gain/attenuation; anything else is a no-op 1.0.
pub fn coerce_volume_level(value: Option<&str>, vocab: &Vocab) -> String {
    let Some(value) = value else {
        return "1.0".to_string();
    };
    // Unicode minus / en-dash / em-dash → ASCII hyphen (models emit '−6dB' with U+2212).
    let normalized: String = value
        .chars()
        .map(|c| match c {
            '\u{2212}' | '\u{2013}' | '\u{2014}' => '-',
            c => c,
        })
        .collect();
    let s = normalized.trim();
    let compact: String = s.chars().filter(|c| *c != ' ').collect();
    if is_numeric_volume(&compact) {
        return compact;
    }
    let key = s.to_lowercase();
    if vocab.volume_louder.contains(&key) {
        return "6dB".to_string();
    }
    if vocab.volume_quieter.contains(&key) {
        return "0.5".to_string();
    }
    "1.0".to_string()
}

/// Parse a `"aw:ah"` / `"aw/ah"` ratio into its two integer strings, or `None` if malformed
/// (mirrors Python `_ASPECT_RE` = `^(\d+)[:/](\d+)$`).
fn parse_ratio(s: &str) -> Option<(&str, &str)> {
    let s = s.trim();
    let sep = s.find([':', '/'])?;
    let (a, b) = (&s[..sep], &s[sep + 1..]);
    let ok = |x: &str| !x.is_empty() && x.bytes().all(|c| c.is_ascii_digit());
    (ok(a) && ok(b)).then_some((a, b))
}

/// The `-vf` filter chain for a geometry operation, or `None`. Faithful port of `_geometry_vf`:
///
/// 1. aspect (without both dims) → center-crop to that aspect ratio.
/// 2. both dims → `fit` resolves: unset/`crop` = cover, `pad` = letterbox, `stretch` = force.
/// 3. single dim → proportional scale.
/// 4. nothing → `None`.
pub fn geometry_vf(
    width: Option<u32>,
    height: Option<u32>,
    fit: Option<&str>,
    aspect: Option<&str>,
) -> anyhow::Result<Option<String>> {
    let has_both = width.is_some() && height.is_some();

    if let Some(aspect) = aspect {
        if !has_both {
            let (aw, ah) = parse_ratio(aspect).ok_or_else(|| {
                anyhow::anyhow!("Invalid aspect value {aspect:?}. Expected 'aw:ah'.")
            })?;
            return Ok(Some(format!(
                "crop=min(iw\\,ih*{aw}/{ah}):min(ih\\,iw*{ah}/{aw})"
            )));
        }
    }

    if let (Some(w), Some(h)) = (width, height) {
        let effective_fit = fit.unwrap_or("crop");
        return Ok(Some(match effective_fit {
            "stretch" => format!("scale={w}:{h}"),
            "pad" => format!(
                "scale={w}:{h}:force_original_aspect_ratio=decrease,\
                 pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
            ),
            _ => format!("scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"),
        }));
    }

    if let Some(w) = width {
        return Ok(Some(format!("scale=min({w}\\,iw):-2")));
    }
    if let Some(h) = height {
        return Ok(Some(format!("scale=-2:{h}")));
    }
    Ok(None)
}

/// Trim window (values are already stringified, e.g. `"5"` or `"00:00:05"`).
#[derive(Debug, Clone, Default)]
pub struct Trim {
    pub start: Option<String>,
    pub duration: Option<String>,
    pub end: Option<String>,
}

/// Resolved video settings for a recipe.
#[derive(Debug, Clone, Default)]
pub struct Video {
    pub encoder: Option<String>,
    pub crf: Option<i32>,
    pub preset: Option<String>,
    pub pixel_format: Option<String>,
    pub max_width: Option<u32>,
    pub max_height: Option<u32>,
}

/// Resolved audio settings for a recipe.
#[derive(Debug, Clone, Default)]
pub struct Audio {
    pub codec: Option<String>,
    pub bitrate: Option<String>,
}

/// A fully-resolved ffmpeg recipe — the input to [`build_flags`]. Built by the recipe
/// orchestrator (`_build_one_recipe`, ported next); tests construct it directly.
#[derive(Debug, Clone, Default)]
pub struct Recipe {
    pub mode: String,
    pub input: String,
    pub output: String,
    pub trim: Trim,
    pub video: Video,
    pub audio: Audio,
    pub include_audio: bool,
    pub has_audio: bool,
    pub audio_format: Option<String>,
    pub at_time: Option<String>,
    pub scale: Option<String>,
    pub speed: Option<f64>,
    pub angle: Option<u32>,
    pub flip: Option<String>,
    pub normalize: bool,
    pub level: Option<String>,
    pub audio_only: bool,
    pub container: Option<String>,
    pub remux: bool,
    pub faststart: bool,
    pub fit: Option<String>,
    pub aspect: Option<String>,
    pub image_format: Option<String>,
    pub target_size_mb: Option<f64>,
    /// Human-readable operation summary (drives the plan preview); not used by `build_flags`.
    pub operations: Vec<String>,
}

/// Requested audio format → ffmpeg encoder (default `copy`); port of `_audio_encoder_for`.
fn audio_encoder_for(vocab: &Vocab, audio_format: &str) -> String {
    vocab
        .audio_format_encoder
        .get(audio_format)
        .cloned()
        .unwrap_or_else(|| "copy".to_string())
}

/// Format an f64 the way Python's `str(float)` does — shortest round-trip, but a whole number
/// keeps a trailing `.0` (Rust prints `2.0` as `2`). Needed for `setpts=`/`atempo=` parity.
fn py_float_str(f: f64) -> String {
    let s = format!("{f}");
    if s.contains(['.', 'e', 'E']) || s.contains("inf") || s.contains("NaN") {
        s
    } else {
        format!("{s}.0")
    }
}

/// Emit `-c:v/-crf/-preset[/-pix_fmt]` for a video block (pixel format only when `pixfmt`).
fn push_video(post: &mut Vec<String>, v: &Video, pixfmt: bool) {
    if let Some(e) = &v.encoder {
        post.extend(["-c:v".to_string(), e.clone()]);
    }
    if let Some(c) = v.crf {
        // `crf` uses an is-not-None check (crf 0 = lossless is emitted), not truthiness.
        post.extend(["-crf".to_string(), c.to_string()]);
    }
    if let Some(p) = &v.preset {
        post.extend(["-preset".to_string(), p.clone()]);
    }
    if pixfmt {
        if let Some(pf) = &v.pixel_format {
            post.extend(["-pix_fmt".to_string(), pf.clone()]);
        }
    }
}

/// Emit `-c:a[/-b:a]` for an audio block (bitrate only when `bitrate`).
fn push_audio(post: &mut Vec<String>, a: &Audio, bitrate: bool) {
    if let Some(c) = &a.codec {
        post.extend(["-c:a".to_string(), c.clone()]);
    }
    if bitrate {
        if let Some(b) = &a.bitrate {
            post.extend(["-b:a".to_string(), b.clone()]);
        }
    }
}

/// Return `(pre_input_flags, post_input_flags)` for a recipe — faithful port of `_build_flags`.
/// All mode knowledge lives here; the command renderer just assembles around `-i`.
pub fn build_flags(recipe: &Recipe, vocab: &Vocab) -> anyhow::Result<(Vec<String>, Vec<String>)> {
    let mode = recipe.mode.as_str();
    let mut pre: Vec<String> = Vec::new();
    let mut post: Vec<String> = Vec::new();

    // Trim: fast-seek before -i; duration/end after -i (then falls through to the encode arm).
    if mode == "trim" {
        if let Some(start) = &recipe.trim.start {
            pre.extend(["-ss".to_string(), start.clone()]);
        }
        if let Some(dur) = &recipe.trim.duration {
            post.extend(["-t".to_string(), dur.clone()]);
        } else if let Some(end) = &recipe.trim.end {
            post.extend(["-to".to_string(), end.clone()]);
        }
    }

    if mode == "reverse" {
        post.extend(["-vf".to_string(), "reverse".to_string()]);
        push_video(&mut post, &recipe.video, true);
        if recipe.include_audio && recipe.has_audio {
            post.extend(["-af".to_string(), "areverse".to_string()]);
            push_audio(&mut post, &recipe.audio, true);
        } else {
            post.push("-an".to_string());
        }
    } else if mode == "extract_audio" {
        if let Some(start) = &recipe.trim.start {
            pre.extend(["-ss".to_string(), start.clone()]);
        }
        if let Some(end) = &recipe.trim.end {
            post.extend(["-to".to_string(), end.clone()]);
        }
        let fmt = recipe.audio_format.as_deref().unwrap_or("mp3");
        post.extend([
            "-vn".to_string(),
            "-c:a".to_string(),
            audio_encoder_for(vocab, fmt),
        ]);
        if let Some(b) = &recipe.audio.bitrate {
            post.extend(["-b:a".to_string(), b.clone()]);
        }
    } else if mode == "thumbnail" {
        post.extend([
            "-ss".to_string(),
            recipe
                .at_time
                .clone()
                .unwrap_or_else(|| "00:00:01".to_string()),
        ]);
        if let Some(scale) = &recipe.scale {
            post.extend(["-vf".to_string(), format!("scale={scale}")]);
        }
        post.extend(["-vframes".to_string(), "1".to_string()]);
    } else if mode == "strip_audio" {
        post.extend(["-an".to_string(), "-c:v".to_string(), "copy".to_string()]);
    } else if mode == "adjust_speed" {
        let speed = recipe.speed.unwrap_or(1.0);
        let pts = ((1.0 / speed) * 1e6).round() / 1e6;
        post.extend([
            "-vf".to_string(),
            format!("setpts={}*PTS", py_float_str(pts)),
            "-af".to_string(),
            format!("atempo={}", py_float_str(speed)),
        ]);
        push_video(&mut post, &recipe.video, false);
        push_audio(&mut post, &recipe.audio, true);
    } else if mode == "rotate" {
        let mut filters: Vec<&str> = Vec::new();
        match recipe.angle {
            Some(90) => filters.push("transpose=1"),
            Some(180) => filters.push("hflip,vflip"),
            Some(270) => filters.push("transpose=2"),
            _ => {}
        }
        match recipe.flip.as_deref() {
            Some("horizontal") => filters.push("hflip"),
            Some("vertical") => filters.push("vflip"),
            _ => {}
        }
        if filters.is_empty() {
            anyhow::bail!("rotate_video: at least one of angle or flip must be set");
        }
        post.extend(["-vf".to_string(), filters.join(",")]);
        push_video(&mut post, &recipe.video, false);
        push_audio(&mut post, &recipe.audio, false); // rotate keeps audio codec but not bitrate
    } else if mode == "adjust_volume" {
        if recipe.normalize {
            post.extend(["-af".to_string(), "loudnorm".to_string()]);
        } else {
            let level = coerce_volume_level(recipe.level.as_deref(), vocab);
            post.extend(["-af".to_string(), format!("volume={level}")]);
        }
        if !recipe.audio_only {
            post.extend(["-c:v".to_string(), "copy".to_string()]);
        }
        push_audio(&mut post, &recipe.audio, false);
    } else if recipe.container.as_deref() == Some("gif") {
        let scale_h = recipe.video.max_height.filter(|h| *h != 0).unwrap_or(480);
        post.extend([
            "-vf".to_string(),
            format!("fps=10,scale=-1:{scale_h}:flags=lanczos"),
            "-an".to_string(),
        ]);
    } else if recipe.remux {
        post.extend(["-c".to_string(), "copy".to_string()]);
        if recipe.faststart {
            post.extend(["-movflags".to_string(), "+faststart".to_string()]);
        }
    } else if mode == "resize" {
        let vf = geometry_vf(
            recipe.video.max_width,
            recipe.video.max_height,
            recipe.fit.as_deref(),
            recipe.aspect.as_deref(),
        )?;
        if let Some(vf) = vf {
            post.extend(["-vf".to_string(), vf]);
        }
        push_video(&mut post, &recipe.video, true);
        push_audio(&mut post, &recipe.audio, true);
        if recipe.faststart {
            post.extend(["-movflags".to_string(), "+faststart".to_string()]);
        }
    } else {
        // platform, compress, convert, trim (encode part), batch. `and` truthiness → treat 0 as unset.
        let max_w = recipe.video.max_width.filter(|x| *x != 0);
        let max_h = recipe.video.max_height.filter(|x| *x != 0);
        match (max_w, max_h) {
            (Some(w), Some(h)) => post.extend(["-vf".to_string(), format!("scale={w}:{h}")]),
            (Some(w), None) => post.extend(["-vf".to_string(), format!("scale='min({w},iw)':-2")]),
            (None, Some(h)) => post.extend(["-vf".to_string(), format!("scale=-2:{h}")]),
            (None, None) => {}
        }
        push_video(&mut post, &recipe.video, true);
        push_audio(&mut post, &recipe.audio, true);
        if recipe.faststart {
            post.extend(["-movflags".to_string(), "+faststart".to_string()]);
        }
    }

    Ok((pre, post))
}

/// Optional preview overrides for a short thumbnail/sample render (port of `_render_command`'s
/// `preview` kwarg): a fast-seek start, a short duration, and an output override.
#[derive(Debug, Clone, Default)]
pub struct Preview {
    pub start: Option<String>,
    pub duration: Option<String>,
    pub output_override: Option<String>,
}

/// Assemble the full argv for a recipe from its pre/post flags — port of `_render_command`:
/// `ffmpeg -y [preview -ss] <pre> -i <input> [preview -t] <post> <output>`.
pub fn render_command(
    recipe: &Recipe,
    pre: &[String],
    post: &[String],
    preview: Option<&Preview>,
) -> Vec<String> {
    let mut cmd = vec!["ffmpeg".to_string(), "-y".to_string()];
    if let Some(start) = preview.and_then(|p| p.start.as_ref()) {
        cmd.extend(["-ss".to_string(), start.clone()]);
    }
    cmd.extend(pre.iter().cloned());
    cmd.extend(["-i".to_string(), recipe.input.clone()]);
    if let Some(dur) = preview.and_then(|p| p.duration.as_ref()) {
        cmd.extend(["-t".to_string(), dur.clone()]);
    }
    cmd.extend(post.iter().cloned());
    let out = preview
        .and_then(|p| p.output_override.clone())
        .unwrap_or_else(|| recipe.output.clone());
    cmd.push(out);
    cmd
}

/// ffmpeg encoder → short codec token, else the encoder unchanged. Port of `_codec_from_encoder`.
pub fn codec_from_encoder<'a>(vocab: &'a Vocab, encoder: &'a str) -> &'a str {
    vocab
        .encoder_codec_map
        .get(encoder)
        .map(String::as_str)
        .unwrap_or(encoder)
}

/// Parse a `"WxH"` / `"W:H"` literal into its two integer strings (mirrors `_WxH_RE`).
fn parse_wxh(s: &str) -> Option<(&str, &str)> {
    let sep = s.find(['x', ':'])?;
    let (a, b) = (&s[..sep], &s[sep + 1..]);
    let ok = |x: &str| !x.is_empty() && x.bytes().all(|c| c.is_ascii_digit());
    (ok(a) && ok(b)).then_some((a, b))
}

/// Resolve a scale shorthand (preset name) or `WxH`/`W:H` literal to a `"W:H"` string. `None` in →
/// `None` out; unrecognised → error. Port of `_parse_scale`.
pub fn parse_scale(scale: Option<&str>, vocab: &Vocab) -> anyhow::Result<Option<String>> {
    let Some(scale) = scale else {
        return Ok(None);
    };
    let key = scale.trim().to_lowercase();
    if let Some(preset) = vocab.scale_presets.get(&key) {
        return Ok(Some(preset.clone()));
    }
    if let Some((w, h)) = parse_wxh(scale.trim()) {
        return Ok(Some(format!("{w}:{h}")));
    }
    anyhow::bail!(
        "Unrecognised scale value {scale:?}. Use a preset (4k, 1080p, 720p, 480p) or WxH / W:H format."
    )
}

/// Normalized media probe (subset `build_one_recipe` reads). `width == 0` / empty codec means
/// "absent", mirroring Python truthiness on the probe dict.
#[derive(Debug, Clone, Default)]
pub struct Probe {
    pub file: String,
    pub container: Option<String>,
    pub video_codec: Option<String>,
    pub audio_codec: Option<String>,
    pub has_audio: bool,
    pub width: Option<u32>,
    pub height: Option<u32>,
    /// Media duration in seconds (used by summaries + concat silence padding; not by `build_one_recipe`).
    pub duration: Option<f64>,
    /// Frames per second (used by concat normalization; not by `build_one_recipe`).
    pub fps: Option<f64>,
}

/// Deterministic placeholder probe for dry-run (no ffprobe / file needed). An audio extension
/// (mp3, wav, …) probes as audio-only so audio ops don't render as video; everything else probes
/// as 1080p H.264/AAC. Port of `_dummy_probe`.
pub fn dummy_probe(file: &Path, vocab: &Vocab) -> Probe {
    let suffix = file
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_lowercase)
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "mp4".to_string());
    let file_str = file.to_string_lossy().into_owned();
    if let Some(audio_codec) = vocab.audio_ext_codec.get(&suffix) {
        Probe {
            file: file_str,
            container: Some(suffix),
            audio_codec: Some(audio_codec.clone()),
            has_audio: true,
            duration: Some(60.0),
            ..Default::default() // video_codec / width / height = None → audio-only
        }
    } else {
        Probe {
            file: file_str,
            container: Some(suffix),
            video_codec: Some("h264".to_string()),
            audio_codec: Some("aac".to_string()),
            has_audio: true,
            width: Some(1920),
            height: Some(1080),
            duration: Some(60.0),
            fps: Some(30.0),
        }
    }
}

/// Parse a fraction fps string like `"30000/1001"` or `"25/1"` to a float. `None` for absent,
/// unparsable, or zero-denominator values. Port of `_parse_fps`.
pub(crate) fn parse_fps(rate: Option<&str>) -> Option<f64> {
    let rate = rate?.trim();
    if rate.is_empty() {
        return None;
    }
    match rate.split_once('/') {
        Some((num, den)) => {
            let (num, den): (f64, f64) = (num.trim().parse().ok()?, den.trim().parse().ok()?);
            (den != 0.0).then_some(num / den)
        }
        None => rate.parse().ok(),
    }
}

/// Normalize a raw `ffprobe -show_streams -show_format -of json` document into a [`Probe`]. Port of
/// `_summarise_probe`: container = first `format_name`; dims/codec from the first video stream;
/// `has_audio` from the first audio stream; duration parsed from `format.duration`.
pub fn summarise_probe(file: &Path, doc: &serde_json::Value) -> Probe {
    let streams = doc.get("streams").and_then(|s| s.as_array());
    let stream_of = |kind: &str| {
        streams.and_then(|ss| {
            ss.iter()
                .find(|s| s.get("codec_type").and_then(|c| c.as_str()) == Some(kind))
        })
    };
    let v_stream = stream_of("video");
    let a_stream = stream_of("audio");
    let fmt = doc.get("format");

    let dim = |s: Option<&serde_json::Value>, key: &str| {
        s.and_then(|s| s.get(key))
            .and_then(|d| d.as_u64())
            .map(|d| d as u32)
    };
    let codec = |s: Option<&serde_json::Value>| {
        s.and_then(|s| s.get("codec_name"))
            .and_then(|c| c.as_str())
            .map(str::to_string)
    };
    let container = fmt
        .and_then(|f| f.get("format_name"))
        .and_then(|n| n.as_str())
        .and_then(|n| n.split(',').next())
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    // ffprobe reports duration as a JSON string ("12.34"); tolerate a bare number too.
    let duration = fmt.and_then(|f| f.get("duration")).and_then(|d| {
        d.as_str()
            .and_then(|s| s.trim().parse::<f64>().ok())
            .or_else(|| d.as_f64())
    });
    // Prefer avg_frame_rate, fall back to r_frame_rate (both are `"num/den"` strings).
    let rate = |key: &str| v_stream.and_then(|s| s.get(key)).and_then(|r| r.as_str());
    let fps = parse_fps(rate("avg_frame_rate")).or_else(|| parse_fps(rate("r_frame_rate")));

    Probe {
        file: file.to_string_lossy().into_owned(),
        container,
        video_codec: codec(v_stream),
        audio_codec: codec(a_stream),
        has_audio: a_stream.is_some(),
        width: dim(v_stream, "width"),
        height: dim(v_stream, "height"),
        duration,
        fps,
    }
}

/// Resolved, expanded options for one recipe (the per-input option bag `build_one_recipe` reads).
#[derive(Debug, Clone, Default)]
pub struct Options {
    pub mode: Option<String>,
    pub container: Option<String>,
    pub video_encoder: Option<String>,
    pub audio_codec: Option<String>,
    pub audio_bitrate: Option<String>,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub output_path: Option<String>,
    pub remux: bool,
    pub copy_audio: bool,
    pub fit: Option<String>,
    pub aspect: Option<String>,
    pub angle: Option<u32>,
    pub flip: Option<String>,
    pub level: Option<String>,
    pub normalize: bool,
    pub start: Option<String>,
    pub duration: Option<String>,
    pub end: Option<String>,
    pub audio_format: Option<String>,
    pub at_time: Option<String>,
    pub image_format: Option<String>,
    pub scale: Option<String>,
    pub target_size_mb: Option<f64>,
    pub include_audio: Option<bool>,
    pub speed: Option<f64>,
    pub platform: Option<String>,
}

fn non_empty(s: &str) -> Option<String> {
    (!s.is_empty()).then(|| s.to_string())
}

/// `Some(x)` → `"x"`, `None` → `"None"` — matches Python f-string formatting of an optional dim.
fn dim_str(d: Option<u32>) -> String {
    d.map(|x| x.to_string())
        .unwrap_or_else(|| "None".to_string())
}

/// Derive the output path from the input stem + a per-mode suffix + the right extension. Port of
/// `_derive_output_path` (suffix template from `output_suffix_by_mode`, `{platform}` substituted).
pub fn derive_output_path(
    input: &Path,
    mode: &str,
    vocab: &Vocab,
    opts: &Options,
    container: &str,
) -> PathBuf {
    let template = vocab
        .output_suffix_by_mode
        .get(mode)
        .map(String::as_str)
        .unwrap_or("_out");
    let platform = opts.platform.as_deref().unwrap_or("");
    let suffix = template.replace("{platform}", platform);
    let stem = input.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    let ext = match mode {
        "extract_audio" => opts.audio_format.as_deref().unwrap_or("mp3"),
        "thumbnail" => opts.image_format.as_deref().unwrap_or("jpg"),
        _ => container,
    };
    let name = format!("{stem}{suffix}.{ext}");
    match input.parent() {
        Some(p) => p.join(name),
        None => PathBuf::from(name),
    }
}

/// Lexically absolutize a path (relative to cwd) and collapse `.`/`..` — no filesystem access, so
/// it works on not-yet-created outputs (mirrors the knaif-core sandbox resolution).
fn lexical_abs(p: &Path) -> PathBuf {
    let base = if p.is_absolute() {
        p.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_default().join(p)
    };
    let mut out = PathBuf::new();
    for comp in base.components() {
        match comp {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            c => out.push(c.as_os_str()),
        }
    }
    out
}

/// Raise if `p` is not inside `sandbox` (both lexically resolved). No-op when `sandbox` is `None`
/// (open / CLI mode). Port of `_assert_in_sandbox`.
pub fn assert_in_sandbox(p: &Path, sandbox: Option<&Path>) -> anyhow::Result<()> {
    let Some(sandbox) = sandbox else {
        return Ok(());
    };
    let (rp, rs) = (lexical_abs(p), lexical_abs(sandbox));
    if !rp.starts_with(&rs) {
        anyhow::bail!(
            "Path {:?} is outside the sandbox {:?}",
            p.display().to_string(),
            rs.display().to_string()
        );
    }
    Ok(())
}

/// Build a fully-resolved [`Recipe`] from a probe + optional platform/quality profiles + options.
/// Faithful port of `_build_one_recipe`: fallback chains for container/codecs/dims/bitrate, the
/// audio-only + gif + single-codec-container edge cases, the operations summary, output-path
/// derivation, and the sandbox gate. `build_flags`/`render_command` consume the result.
pub fn build_one_recipe(
    probe: &Probe,
    platform: Option<&PlatformProfile>,
    quality: Option<&QualityProfile>,
    options: &Options,
    vocab: &Vocab,
    sandbox: Option<&Path>,
) -> anyhow::Result<Recipe> {
    let mode = options
        .mode
        .clone()
        .unwrap_or_else(|| "platform".to_string());
    let input_path = PathBuf::from(&probe.file);

    let mut container = options
        .container
        .clone()
        .or_else(|| platform.map(|p| p.container.clone()))
        .unwrap_or_else(|| "mp4".to_string());
    if mode == "reverse" && options.container.is_none() {
        container = probe
            .container
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| {
                input_path
                    .extension()
                    .and_then(|e| e.to_str())
                    .map(String::from)
            })
            .unwrap_or(container);
    }

    let mut video_encoder = options
        .video_encoder
        .clone()
        .or_else(|| platform.map(|p| p.video_encoder.clone()))
        .unwrap_or_else(|| "libx264".to_string());
    let mut pixel_format = platform
        .and_then(|p| p.pixel_format.clone())
        .unwrap_or_else(|| "yuv420p".to_string());
    let audio_default = match container.as_str() {
        "webm" => "libopus",
        "ogg" => "libvorbis",
        _ => "aac",
    };
    let mut audio_codec = options
        .audio_codec
        .clone()
        .or_else(|| platform.map(|p| p.audio_codec.clone()))
        .unwrap_or_else(|| audio_default.to_string());
    let max_w = options.width.or_else(|| platform.and_then(|p| p.max_width));
    let max_h = options
        .height
        .or_else(|| platform.and_then(|p| p.max_height));
    let mut faststart = platform.map(|p| p.faststart).unwrap_or(container == "mp4");
    let crf = quality.map(|q| q.video_crf);
    let preset = quality.map(|q| q.encoder_preset.clone());
    let mut audio_bitrate = options
        .audio_bitrate
        .clone()
        .or_else(|| quality.and_then(|q| q.audio_bitrate.clone()))
        .or_else(|| platform.and_then(|p| p.max_audio_bitrate.clone()))
        .or_else(|| Some("128k".to_string()));

    // Audio-only inputs routed through adjust_volume produce an audio file, not a video container.
    let audio_only = mode == "adjust_volume"
        && probe.video_codec.as_deref().is_none_or(str::is_empty)
        && probe.width.is_none_or(|w| w == 0);
    if audio_only {
        container = options
            .container
            .clone()
            .or_else(|| probe.container.clone().filter(|s| !s.is_empty()))
            .or_else(|| {
                input_path
                    .extension()
                    .and_then(|e| e.to_str())
                    .map(String::from)
            })
            .unwrap_or(container);
        audio_codec = audio_encoder_for(vocab, &container);
    }

    if container == "gif" {
        video_encoder = String::new();
        pixel_format = String::new();
        audio_codec = String::new();
        faststart = false;
    }

    let output_path = if let Some(raw) = options.output_path.as_deref().filter(|s| !s.is_empty()) {
        let out = PathBuf::from(raw);
        if out.is_absolute() {
            out
        } else {
            input_path.parent().map(|p| p.join(&out)).unwrap_or(out)
        }
    } else {
        derive_output_path(&input_path, &mode, vocab, options, &container)
    };
    assert_in_sandbox(&output_path, sandbox)?;

    let mut operations: Vec<String> = Vec::new();
    if matches!(
        mode.as_str(),
        "platform" | "compress" | "convert" | "resize" | "trim" | "batch"
    ) {
        // width cap exceeded, else height cap exceeded (both format {max_w}x{max_h}, "None" if unset)
        let downscale_w = max_w.zip(probe.width).is_some_and(|(mw, pw)| pw > mw);
        let downscale_h = max_h.zip(probe.height).is_some_and(|(mh, ph)| ph > mh);
        if downscale_w || downscale_h {
            operations.push(format!(
                "downscale_to_fit_{}x{}",
                dim_str(max_w),
                dim_str(max_h)
            ));
        }
        if probe.container.as_deref() != Some(container.as_str()) {
            operations.push(format!("convert_container_to_{container}"));
        }
        let target_codec = codec_from_encoder(vocab, &video_encoder).to_string();
        if probe.video_codec.as_deref() != Some(target_codec.as_str()) {
            operations.push(format!("convert_video_to_{target_codec}"));
        }
        if probe.has_audio && probe.audio_codec.as_deref() != Some(audio_codec.as_str()) {
            operations.push(format!("ensure_{audio_codec}_audio"));
        }
        operations.push(format!("ensure_{pixel_format}"));
        if faststart {
            operations.push("enable_faststart".to_string());
        }
    }

    let mut remux = options.remux;
    let mut copy_audio = remux || options.copy_audio;

    // A container that accepts only a restricted set of VIDEO codecs cannot stream-copy an
    // incompatible source. `-c copy` into webm from an h264 source makes ffmpeg exit 1 and leave a
    // truncated file behind — which the user reads as "knaif produced a broken file", not as an
    // unsupported request.
    //
    // Unlike the audio guard below, this one MUST override a remux rather than skip it: the remux
    // is precisely how the bad command gets built. A remux copying everything verbatim holds for
    // mkv/mov, which accept h264; it does not hold for webm.
    //
    // Dropping the remux alone is not enough — the encoder would fall back to `copy` and rebuild
    // the same command — so the container's default encoder has to be named here.
    //
    // ogg carries the same restriction (theora/vp8 only) and is deliberately absent: vocab.yaml has
    // no theora entry to fall back to and the corpus has no ogg rows, so listing it would be an
    // untested guess. Closing that one means adding the encoder first.
    if remux {
        let compatible: Option<&[&str]> = match container.as_str() {
            "webm" => Some(&["vp8", "vp9", "av1"]),
            _ => None,
        };
        if let (Some(src), Some(compat)) = (
            probe.video_codec.as_deref().filter(|s| !s.is_empty()),
            compatible,
        ) {
            if !compat.contains(&src) {
                remux = false;
                copy_audio = false;
                if options.video_encoder.is_none() {
                    video_encoder = match container.as_str() {
                        "webm" => "libvpx-vp9".to_string(),
                        _ => video_encoder,
                    };
                }
            }
        }
    }

    // Stream-copy into a container that can't hold the source audio codec → re-encode instead.
    if copy_audio && !remux {
        let compatible: Option<&[&str]> = match container.as_str() {
            "webm" => Some(&["opus", "vorbis"]),
            "ogg" => Some(&["vorbis", "opus", "flac"]),
            _ => None,
        };
        if let (Some(src), Some(compat)) = (
            probe.audio_codec.as_deref().filter(|s| !s.is_empty()),
            compatible,
        ) {
            if !compat.contains(&src) {
                copy_audio = false;
            }
        }
    }

    // A single-codec audio container can't remux a mismatched audio-only source → re-encode, drop video.
    let mandated: Option<&str> = match container.as_str() {
        "mp3" => Some("mp3"),
        "aac" | "m4a" => Some("aac"),
        "flac" => Some("flac"),
        "wav" => Some("pcm_s16le"),
        "opus" => Some("opus"),
        _ => None,
    };
    let input_is_audio_only = probe.video_codec.as_deref().is_none_or(str::is_empty)
        && probe.width.is_none_or(|w| w == 0);
    let mut drop_video = false;
    if copy_audio && input_is_audio_only {
        if let Some(mand) = mandated {
            if probe.audio_codec.as_deref() != Some(mand) {
                remux = false;
                copy_audio = false;
                audio_codec = audio_encoder_for(vocab, &container);
                drop_video = true;
                if matches!(audio_codec.as_str(), "flac" | "pcm_s16le" | "alac") {
                    audio_bitrate = None;
                }
            }
        }
    }

    let mut recipe = Recipe {
        mode: mode.clone(),
        input: input_path.to_string_lossy().into_owned(),
        output: output_path.to_string_lossy().into_owned(),
        operations,
        video: Video {
            encoder: if remux {
                Some("copy".to_string())
            } else {
                non_empty(&video_encoder)
            },
            crf: if remux { None } else { crf },
            preset: if remux { None } else { preset },
            pixel_format: if remux {
                None
            } else {
                non_empty(&pixel_format)
            },
            max_width: max_w,
            max_height: max_h,
        },
        audio: Audio {
            codec: if copy_audio {
                Some("copy".to_string())
            } else {
                non_empty(&audio_codec)
            },
            bitrate: if copy_audio { None } else { audio_bitrate },
        },
        container: non_empty(&container),
        faststart,
        remux,
        ..Default::default()
    };

    match mode.as_str() {
        "resize" => {
            recipe.fit = options.fit.clone();
            recipe.aspect = options.aspect.clone();
        }
        "rotate" => {
            recipe.angle = options.angle;
            recipe.flip = options.flip.clone();
        }
        "adjust_volume" => {
            recipe.level = options.level.clone();
            recipe.normalize = options.normalize;
            recipe.audio_only = audio_only;
        }
        "trim" => {
            recipe.trim = Trim {
                start: options.start.clone(),
                duration: options.duration.clone(),
                end: options.end.clone(),
            };
        }
        "extract_audio" => {
            recipe.audio_format = Some(
                options
                    .audio_format
                    .clone()
                    .unwrap_or_else(|| "mp3".to_string()),
            );
            if options.start.is_some() || options.end.is_some() {
                recipe.trim = Trim {
                    start: options.start.clone(),
                    duration: None,
                    end: options.end.clone(),
                };
            }
            recipe.video = Video::default(); // no video stream in an audio extract
        }
        "thumbnail" => {
            recipe.at_time = Some(
                options
                    .at_time
                    .clone()
                    .unwrap_or_else(|| "00:00:01".to_string()),
            );
            recipe.image_format = Some(
                options
                    .image_format
                    .clone()
                    .unwrap_or_else(|| "jpg".to_string()),
            );
            recipe.scale = parse_scale(options.scale.as_deref(), vocab)?;
            recipe.audio = Audio::default();
        }
        "compress" => {
            recipe.target_size_mb = options.target_size_mb;
        }
        "reverse" => {
            recipe.include_audio = options.include_audio.unwrap_or(true);
            recipe.has_audio = probe.has_audio;
        }
        "strip_audio" => {
            recipe.audio = Audio::default();
        }
        "adjust_speed" => {
            recipe.speed = Some(options.speed.unwrap_or(1.0));
        }
        _ => {}
    }

    if drop_video {
        recipe.video = Video::default();
    }

    Ok(recipe)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vf(
        w: Option<u32>,
        h: Option<u32>,
        fit: Option<&str>,
        aspect: Option<&str>,
    ) -> Option<String> {
        geometry_vf(w, h, fit, aspect).unwrap()
    }

    #[test]
    fn both_dims_default_cover_crop() {
        assert_eq!(
            vf(Some(640), Some(480), None, None).as_deref(),
            Some("scale=640:480:force_original_aspect_ratio=increase,crop=640:480")
        );
    }

    #[test]
    fn both_dims_pad_letterbox() {
        assert_eq!(
            vf(Some(640), Some(480), Some("pad"), None).as_deref(),
            Some("scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2")
        );
    }

    #[test]
    fn both_dims_stretch_forces() {
        assert_eq!(
            vf(Some(640), Some(480), Some("stretch"), None).as_deref(),
            Some("scale=640:480")
        );
    }

    #[test]
    fn single_dim_scales_proportionally() {
        assert_eq!(
            vf(Some(1280), None, None, None).as_deref(),
            Some("scale=min(1280\\,iw):-2")
        );
        assert_eq!(
            vf(None, Some(720), None, None).as_deref(),
            Some("scale=-2:720")
        );
    }

    #[test]
    fn aspect_only_center_crops() {
        assert_eq!(
            vf(None, None, None, Some("16:9")).as_deref(),
            Some("crop=min(iw\\,ih*16/9):min(ih\\,iw*9/16)")
        );
        // `/` separator also accepted
        assert_eq!(
            vf(None, None, None, Some("4/3")).as_deref(),
            Some("crop=min(iw\\,ih*4/3):min(ih\\,iw*3/4)")
        );
    }

    #[test]
    fn aspect_ignored_when_both_dims_present() {
        // both dims set → fall through to the cover-crop branch, aspect ignored
        assert_eq!(
            vf(Some(100), Some(100), None, Some("16:9")).as_deref(),
            Some("scale=100:100:force_original_aspect_ratio=increase,crop=100:100")
        );
    }

    #[test]
    fn nothing_returns_none() {
        assert_eq!(vf(None, None, None, None), None);
    }

    #[test]
    fn invalid_aspect_errors() {
        assert!(geometry_vf(None, None, None, Some("wide")).is_err());
    }

    fn bundle_vocab() -> Vocab {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../skills/ffmpeg/vocab.yaml");
        Vocab::load(&path).unwrap()
    }

    #[test]
    fn volume_numeric_and_db_pass_through() {
        let v = bundle_vocab();
        assert_eq!(coerce_volume_level(Some("6dB"), &v), "6dB");
        assert_eq!(coerce_volume_level(Some("0.5"), &v), "0.5");
        assert_eq!(coerce_volume_level(Some("-6 dB"), &v), "-6dB"); // space removed, sign kept
        assert_eq!(coerce_volume_level(Some("\u{2212}6dB"), &v), "-6dB"); // unicode minus
    }

    #[test]
    fn volume_direction_words_and_fallback() {
        let v = bundle_vocab();
        assert_eq!(coerce_volume_level(Some("boost"), &v), "6dB"); // volume_louder
        assert_eq!(coerce_volume_level(Some("Reduce"), &v), "0.5"); // volume_quieter, case-folded
        assert_eq!(coerce_volume_level(Some("sideways"), &v), "1.0"); // unknown → no-op
        assert_eq!(coerce_volume_level(None, &v), "1.0");
    }

    fn recipe(mode: &str) -> Recipe {
        Recipe {
            mode: mode.to_string(),
            ..Default::default()
        }
    }

    fn flags(r: &Recipe) -> (Vec<String>, Vec<String>) {
        build_flags(r, &bundle_vocab()).unwrap()
    }

    fn s(items: &[&str]) -> Vec<String> {
        items.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn strip_audio_copies_video_drops_audio() {
        let (pre, post) = flags(&recipe("strip_audio"));
        assert!(pre.is_empty());
        assert_eq!(post, s(&["-an", "-c:v", "copy"]));
    }

    #[test]
    fn reverse_with_and_without_audio() {
        let mut r = recipe("reverse");
        r.video.encoder = Some("libx264".into());
        r.video.crf = Some(23);
        r.include_audio = true;
        r.has_audio = true;
        r.audio.codec = Some("aac".into());
        r.audio.bitrate = Some("128k".into());
        let (_, post) = flags(&r);
        assert_eq!(
            post,
            s(&[
                "-vf", "reverse", "-c:v", "libx264", "-crf", "23", "-af", "areverse", "-c:a",
                "aac", "-b:a", "128k"
            ])
        );
        // no audio → -an, no -af areverse
        r.include_audio = false;
        let (_, post) = flags(&r);
        assert_eq!(
            post,
            s(&["-vf", "reverse", "-c:v", "libx264", "-crf", "23", "-an"])
        );
    }

    #[test]
    fn adjust_speed_formats_floats_like_python() {
        let mut r = recipe("adjust_speed");
        r.speed = Some(2.0);
        let (_, post) = flags(&r);
        // 1/2 = 0.5 (setpts), atempo keeps the whole-number .0
        assert_eq!(post, s(&["-vf", "setpts=0.5*PTS", "-af", "atempo=2.0"]));

        r.speed = Some(0.5);
        let (_, post) = flags(&r);
        assert_eq!(post, s(&["-vf", "setpts=2.0*PTS", "-af", "atempo=0.5"]));
    }

    #[test]
    fn rotate_maps_angle_and_requires_one() {
        let mut r = recipe("rotate");
        r.angle = Some(90);
        let (_, post) = flags(&r);
        assert_eq!(post, s(&["-vf", "transpose=1"]));

        r.angle = Some(180);
        r.flip = Some("horizontal".into());
        let (_, post) = flags(&r);
        assert_eq!(post, s(&["-vf", "hflip,vflip,hflip"]));

        // neither angle nor flip → error
        assert!(build_flags(&recipe("rotate"), &bundle_vocab()).is_err());
    }

    #[test]
    fn trim_seeks_pre_then_falls_through_to_encode() {
        let mut r = recipe("trim");
        r.trim.start = Some("5".into());
        r.trim.duration = Some("10".into());
        r.video.encoder = Some("libx264".into());
        let (pre, post) = flags(&r);
        assert_eq!(pre, s(&["-ss", "5"]));
        // -t from the trim block, then the encode-arm video flags
        assert_eq!(post, s(&["-t", "10", "-c:v", "libx264"]));
    }

    #[test]
    fn extract_audio_uses_encoder_map() {
        let mut r = recipe("extract_audio");
        r.audio_format = Some("mp3".into());
        let (_, post) = flags(&r);
        assert_eq!(post, s(&["-vn", "-c:a", "libmp3lame"])); // mp3 → libmp3lame from vocab
    }

    #[test]
    fn gif_branch_defaults_scale_height() {
        let mut r = recipe("convert");
        r.container = Some("gif".into());
        let (_, post) = flags(&r);
        assert_eq!(
            post,
            s(&["-vf", "fps=10,scale=-1:480:flags=lanczos", "-an"])
        );
    }

    #[test]
    fn encode_arm_scale_expression_and_faststart() {
        let mut r = recipe("compress");
        r.video.max_width = Some(1280);
        r.video.encoder = Some("libx264".into());
        r.video.crf = Some(28);
        r.video.preset = Some("slow".into());
        r.video.pixel_format = Some("yuv420p".into());
        r.audio.codec = Some("aac".into());
        r.audio.bitrate = Some("96k".into());
        r.faststart = true;
        let (_, post) = flags(&r);
        assert_eq!(
            post,
            s(&[
                "-vf",
                "scale='min(1280,iw)':-2",
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
                "+faststart"
            ])
        );
    }

    #[test]
    fn adjust_volume_normalize_keeps_video() {
        let mut r = recipe("adjust_volume");
        r.normalize = true;
        let (_, post) = flags(&r);
        assert_eq!(post, s(&["-af", "loudnorm", "-c:v", "copy"]));
    }

    // ── build_one_recipe ──────────────────────────────────────────────────────

    fn platform(name: &str) -> PlatformProfile {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../skills/ffmpeg/profiles/platforms");
        crate::load_platforms(&dir).unwrap().remove(name).unwrap()
    }

    fn quality(name: &str) -> QualityProfile {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../skills/ffmpeg/profiles/quality");
        crate::load_quality(&dir).unwrap().remove(name).unwrap()
    }

    fn video_probe() -> Probe {
        Probe {
            file: "video.mp4".into(),
            container: Some("mp4".into()),
            video_codec: Some("h264".into()),
            audio_codec: Some("aac".into()),
            has_audio: true,
            width: Some(1920),
            height: Some(1080),
            duration: Some(60.0),
            fps: Some(30.0),
        }
    }

    #[test]
    fn compress_pulls_quality_profile_and_derives_output() {
        let v = bundle_vocab();
        let opts = Options {
            mode: Some("compress".into()),
            ..Default::default()
        };
        let r = build_one_recipe(
            &video_probe(),
            None,
            Some(&quality("balanced")),
            &opts,
            &v,
            None,
        )
        .unwrap();
        assert_eq!(r.video.encoder.as_deref(), Some("libx264"));
        assert_eq!(r.video.crf, Some(25));
        assert_eq!(r.video.preset.as_deref(), Some("medium"));
        assert_eq!(r.audio.bitrate.as_deref(), Some("128k"));
        assert_eq!(r.container.as_deref(), Some("mp4"));
        assert!(r.output.ends_with("video_compressed.mp4"));
        assert!(r.operations.iter().any(|o| o == "ensure_yuv420p"));
        assert!(r.operations.iter().any(|o| o == "enable_faststart"));
    }

    #[test]
    fn audio_only_into_single_codec_container_drops_video_and_reencodes() {
        let v = bundle_vocab();
        let probe = Probe {
            file: "song.wav".into(),
            container: Some("wav".into()),
            audio_codec: Some("pcm_s16le".into()),
            has_audio: true,
            ..Default::default() // no video_codec / width → audio-only
        };
        let opts = Options {
            mode: Some("convert".into()),
            container: Some("mp3".into()),
            copy_audio: true,
            ..Default::default()
        };
        let r = build_one_recipe(&probe, None, None, &opts, &v, None).unwrap();
        assert!(!r.remux);
        assert_eq!(r.video.encoder, None); // video dropped
        assert_eq!(r.audio.codec.as_deref(), Some("libmp3lame")); // re-encoded, not copy
        assert_eq!(r.container.as_deref(), Some("mp3"));
    }

    #[test]
    fn gif_container_clears_codecs() {
        let v = bundle_vocab();
        let opts = Options {
            mode: Some("convert".into()),
            container: Some("gif".into()),
            ..Default::default()
        };
        let r = build_one_recipe(&video_probe(), None, None, &opts, &v, None).unwrap();
        assert_eq!(r.container.as_deref(), Some("gif"));
        assert_eq!(r.video.encoder, None);
        assert!(!r.faststart);
    }

    #[test]
    fn sandbox_rejects_output_escaping_it() {
        let v = bundle_vocab();
        let cwd = std::env::current_dir().unwrap();
        let opts = Options {
            mode: Some("compress".into()),
            output_path: Some("../escape.mp4".into()),
            ..Default::default()
        };
        let err = build_one_recipe(&video_probe(), None, None, &opts, &v, Some(&cwd)).unwrap_err();
        assert!(err.to_string().contains("outside the sandbox"));
    }

    #[test]
    fn dummy_probe_distinguishes_audio_and_video() {
        let v = bundle_vocab();
        let video = dummy_probe(Path::new("clip.mp4"), &v);
        assert_eq!(video.video_codec.as_deref(), Some("h264"));
        assert_eq!(video.width, Some(1920));

        let audio = dummy_probe(Path::new("song.mp3"), &v);
        assert_eq!(audio.video_codec, None); // audio-only
        assert_eq!(audio.audio_codec.as_deref(), Some("mp3"));
        assert_eq!(audio.container.as_deref(), Some("mp3"));

        // no extension → mp4 video default
        assert_eq!(
            dummy_probe(Path::new("noext"), &v).container.as_deref(),
            Some("mp4")
        );
    }

    #[test]
    fn summarise_probe_maps_ffprobe_json() {
        // A realistic ffprobe -show_streams -show_format -of json document.
        let doc = serde_json::json!({
            "streams": [
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "video", "codec_name": "h264", "width": 3840, "height": 2160,
                 "avg_frame_rate": "30000/1001"}
            ],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.34", "size": "9999"}
        });
        let p = summarise_probe(Path::new("/media/holiday.mp4"), &doc);
        assert_eq!(p.container.as_deref(), Some("mov")); // first of the comma list
        assert_eq!(p.video_codec.as_deref(), Some("h264"));
        assert_eq!(p.audio_codec.as_deref(), Some("aac"));
        assert!(p.has_audio);
        assert_eq!(p.width, Some(3840));
        assert_eq!(p.height, Some(2160));
        assert_eq!(p.duration, Some(12.34));
        assert!((p.fps.unwrap() - 30000.0 / 1001.0).abs() < 1e-6); // avg_frame_rate parsed
    }

    #[test]
    fn summarise_probe_video_only_has_no_audio() {
        let doc = serde_json::json!({
            "streams": [{"codec_type": "video", "codec_name": "vp9", "width": 1280, "height": 720}],
            "format": {"format_name": "matroska,webm"}
        });
        let p = summarise_probe(Path::new("silent.webm"), &doc);
        assert!(!p.has_audio);
        assert_eq!(p.audio_codec, None);
        assert_eq!(p.container.as_deref(), Some("matroska"));
        assert_eq!(p.duration, None); // absent duration → None, not a parse error
        assert_eq!(p.fps, None); // no frame-rate field → None
    }

    #[test]
    fn dry_run_pipeline_from_filename_only() {
        // filename → dummy probe → recipe → flags → full command, no ffprobe/ffmpeg needed
        let v = bundle_vocab();
        let probe = dummy_probe(Path::new("holiday.mp4"), &v);
        let opts = Options {
            mode: Some("compress".into()),
            ..Default::default()
        };
        let r =
            build_one_recipe(&probe, None, Some(&quality("small_file")), &opts, &v, None).unwrap();
        let (pre, post) = build_flags(&r, &v).unwrap();
        let cmd = render_command(&r, &pre, &post, None);
        assert_eq!(cmd[0], "ffmpeg");
        assert!(cmd.windows(2).any(|w| w == ["-c:v", "libx264"]));
        assert!(cmd.windows(2).any(|w| w == ["-crf", "28"])); // small_file crf
        assert!(cmd.last().unwrap().ends_with("holiday_compressed.mp4"));
    }

    #[test]
    fn build_one_recipe_feeds_build_flags_and_render_end_to_end() {
        let v = bundle_vocab();
        let opts = Options {
            mode: Some("platform".into()),
            ..Default::default()
        };
        // whatsapp caps 1280x720; source 1920x1080 → both-dim scale
        let r = build_one_recipe(
            &video_probe(),
            Some(&platform("whatsapp")),
            Some(&quality("balanced")),
            &opts,
            &v,
            None,
        )
        .unwrap();
        let (pre, post) = build_flags(&r, &v).unwrap();
        let cmd = render_command(&r, &pre, &post, None);
        assert_eq!(cmd[0], "ffmpeg");
        assert!(cmd.windows(2).any(|w| w == ["-vf", "scale=1280:720"]));
        assert!(cmd.windows(2).any(|w| w == ["-c:v", "libx264"]));
        assert!(cmd.windows(2).any(|w| w == ["-crf", "25"]));
        assert!(cmd.contains(&"+faststart".to_string()));
    }

    #[test]
    fn render_command_assembles_full_argv() {
        let mut r = recipe("strip_audio");
        r.input = "in.mp4".into();
        r.output = "out.mp4".into();
        let (pre, post) = flags(&r);
        assert_eq!(
            render_command(&r, &pre, &post, None),
            s(&["ffmpeg", "-y", "-i", "in.mp4", "-an", "-c:v", "copy", "out.mp4"])
        );
    }

    #[test]
    fn render_command_puts_pre_flags_before_input() {
        let mut r = recipe("trim");
        r.input = "in.mp4".into();
        r.output = "out.mp4".into();
        r.trim.start = Some("5".into());
        let (pre, post) = flags(&r);
        // -ss 5 (fast seek) lands before -i; no post flags for a bare trim
        assert_eq!(
            render_command(&r, &pre, &post, None),
            s(&["ffmpeg", "-y", "-ss", "5", "-i", "in.mp4", "out.mp4"])
        );
    }

    #[test]
    fn codec_from_encoder_maps_or_passes_through() {
        let v = bundle_vocab();
        assert_eq!(codec_from_encoder(&v, "libx264"), "h264");
        assert_eq!(codec_from_encoder(&v, "libsvtav1"), "av1");
        assert_eq!(codec_from_encoder(&v, "copy"), "copy"); // unknown → unchanged
    }

    #[test]
    fn parse_scale_presets_literals_and_errors() {
        let v = bundle_vocab();
        assert_eq!(parse_scale(None, &v).unwrap(), None);
        assert_eq!(
            parse_scale(Some("1080p"), &v).unwrap().as_deref(),
            Some("1920:1080")
        );
        assert_eq!(
            parse_scale(Some("1280x720"), &v).unwrap().as_deref(),
            Some("1280:720")
        );
        assert_eq!(
            parse_scale(Some("1280:720"), &v).unwrap().as_deref(),
            Some("1280:720")
        );
        assert!(parse_scale(Some("huge"), &v).is_err());
    }

    #[test]
    fn render_command_applies_preview_overrides() {
        let mut r = recipe("strip_audio");
        r.input = "in.mp4".into();
        r.output = "out.mp4".into();
        let (pre, post) = flags(&r);
        let preview = Preview {
            start: Some("00:00:02".into()),
            duration: Some("1".into()),
            output_override: Some("thumb.jpg".into()),
        };
        assert_eq!(
            render_command(&r, &pre, &post, Some(&preview)),
            s(&[
                "ffmpeg",
                "-y",
                "-ss",
                "00:00:02",
                "-i",
                "in.mp4",
                "-t",
                "1",
                "-an",
                "-c:v",
                "copy",
                "thumb.jpg"
            ])
        );
    }
}
