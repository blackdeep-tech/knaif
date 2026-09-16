//! Native intent expansion → command rendering and execution.
//!
//! [`expand`] is the native equivalent of a Python ffmpeg `Intent.expand` followed by the
//! deterministic `build_recipes` → `render_batch_commands` steps, collapsed into one pass
//! because the native `run` verb owns the whole workflow (no intermediate model-visible steps).
//! Given a validated intent step (`tool` + `args`) it maps the args to engine [`Options`],
//! resolves and sandbox-checks every input via [`resolve_input_in_sandbox`], probes it, and
//! renders the full `ffmpeg` argv per input via
//! `build_one_recipe → build_flags → render_command`.
//!
//! Two probe modes: [`expand_dry_run`] uses the deterministic [`dummy_probe`] (no file access),
//! while [`expand_execute`] runs real `ffprobe` against the resolved input — so this module
//! *does* touch the filesystem and spawn subprocesses on the execute path. `join_videos`
//! (concat / `-filter_complex`) is implemented in [`expand_concat`].
//!
//! Sandbox note: inputs are validated *and read* through the resolved path (audit F3/R1) —
//! never validate one representation and open another.

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

/// Is this input one `extract_audio` must skip? Port of `BuildRecipesStep`'s skip.
///
/// ffmpeg cannot extract audio from a file that has none: it exits with "Output file does not
/// contain any stream", and one silent video in a folder of ten takes the whole batch down. The
/// probe already answered this, so building the command anyway discards a fact we hold.
///
/// Scoped to `extract_audio` on measurement, not on principle: `adjust_volume` and `strip_audio`
/// both succeed on a real silent file, so skipping there would drop work that completes today.
pub fn skips_silent_input(mode: Option<&str>, has_audio: bool) -> bool {
    mode == Some("extract_audio") && !has_audio
}

/// The message when every input was skipped. Byte-identical to Python's, because it reaches the
/// user on both runtimes and two spellings of one refusal is how the two drift apart.
pub fn no_audio_error(skipped: &[String]) -> String {
    if skipped.len() == 1 {
        format!("No audio to extract: {} has no audio track.", skipped[0])
    } else {
        format!(
            "No audio to extract — none of these files has an audio track: {}.",
            skipped.join(", ")
        )
    }
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
    // Globs become one input per matching file BEFORE the render loop, so each match gets its own
    // command and its own derived output name (N1).
    let inputs = expand_input_globs(&resolved.inputs, sandbox, &data.vocab.media_extensions)?;
    // Build every recipe first: only the batch can see two inputs landing on one output
    // path, and every rendered command carries -y, so an unresolved clash is a silent
    // overwrite rather than an error.
    let mut recipes = Vec::with_capacity(inputs.len());
    let mut outs: Vec<(std::path::PathBuf, std::path::PathBuf)> = Vec::with_capacity(inputs.len());
    let mut skipped: Vec<String> = Vec::new();
    for input in &inputs {
        // Probe and render the RESOLVED path — checking one representation and reading another
        // is not a boundary (see resolve_input_in_sandbox; fix review R1).
        let input_path = resolve_input_in_sandbox(input, sandbox)?;
        let probe = probe_input(&input_path, data, mode)?;
        // ffmpeg cannot extract audio from a file that has none: it exits with "Output file does
        // not contain any stream", and one silent video in a folder of ten takes the whole batch
        // down. The probe already answered this, so building the command anyway discards a fact
        // we hold. Port of `BuildRecipesStep`'s skip.
        //
        // Scoped to `extract_audio` on measurement, not on principle: `adjust_volume` and
        // `strip_audio` both succeed on a real silent file, so skipping there would drop work
        // that currently completes.
        if skips_silent_input(resolved.options.mode.as_deref(), probe.has_audio) {
            skipped.push(
                input_path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| input_path.to_string_lossy().into_owned()),
            );
            continue;
        }
        let recipe = build_one_recipe(
            &probe,
            resolved.platform.as_ref(),
            resolved.quality.as_ref(),
            &resolved.options,
            &data.vocab,
            sandbox,
        )?;
        outs.push((input_path.clone(), std::path::PathBuf::from(&recipe.output)));
        recipes.push(recipe);
    }
    // Skipping every input is not success with zero results: an empty command list executes
    // cleanly and reports done, telling the user the work happened.
    if recipes.is_empty() && !skipped.is_empty() {
        anyhow::bail!("{}", no_audio_error(&skipped));
    }
    // Returning fewer files than asked for without saying why reads as a tool that lost a file.
    if !skipped.is_empty() {
        eprintln!("note: skipped {} (no audio track)", skipped.join(", "));
    }
    crate::engine::disambiguate_outputs(&mut outs);
    let mut commands = Vec::with_capacity(recipes.len());
    for (mut recipe, (_, out)) in recipes.into_iter().zip(outs) {
        recipe.output = out.to_string_lossy().into_owned();
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

    // Resolve + boundary-check every concat input, then probe AND render the resolved paths —
    // the raw list must not reach the rendered command (see resolve_input_in_sandbox; R1).
    let mut infos = Vec::with_capacity(inputs.len());
    let mut resolved_inputs = Vec::with_capacity(inputs.len());
    for input in &inputs {
        let input_path = resolve_input_in_sandbox(input, sandbox)?;
        let probe = probe_input(&input_path, data, mode)?;
        infos.push(crate::concat::ConcatInfo::from_probe(&probe));
        resolved_inputs.push(input_path.to_string_lossy().into_owned());
    }
    let cmd = crate::concat::build_concat_command(
        &resolved_inputs,
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

/// Resolve one `inputs` entry against the sandbox, boundary-check it, and return **the path the
/// caller must then probe and render** — port of Python's `ResolveInputs` step (a relative path
/// resolves against the sandbox, not cwd; both it and the sandbox are then resolved
/// filesystem-real, so a symlink/junction inside the sandbox pointing outside it is caught the
/// same way Python's `Path.resolve()` already catches it).
///
/// `expand`/`expand_concat` originally probed `inputs` with no resolution or containment check
/// at all — only the *derived output* path was ever checked, which doesn't protect a read
/// (audit F3). Returning the resolved path, rather than merely validating the raw string,
/// closes the second half of that gap: validating one representation while reading another is
/// not a boundary. With a working directory different from the sandbox, `clip.mp4` checks
/// `<sandbox>/clip.mp4` while ffprobe/ffmpeg open `<cwd>/clip.mp4` — a different file (2026-09-07
/// fix review, R1). In open/CLI mode (`sandbox` is `None`) there is no boundary to enforce and
/// nothing to re-base, so the raw string is returned unchanged.
/// True when *s* carries fnmatch magic — the same three characters Python's `ResolveInputs`
/// treats as "this is a pattern, not a path".
fn has_glob_magic(s: &str) -> bool {
    s.contains('*') || s.contains('?') || s.contains('[')
}

/// fnmatch one path component, via the `glob` crate.
///
/// The requirement is **"match Python's `fnmatch`"**, not "glob correctly", so the crate is
/// adopted behind this function with one correction. `glob` treats `**` as a *recursive wildcard*
/// — a directory-descent extension fnmatch does not have — and **rejects the pattern outright**
/// when `**` is not a whole path component: `**.mp4` and `a**b` are syntax errors to it and
/// ordinary patterns to Python. Under fnmatch a run of `*` is just `*`, so collapsing the run
/// before handing the pattern over restores Python's reading exactly.
///
/// Found by diffing the crate against `fnmatch.fnmatchcase` rather than by trusting it; the
/// disagreement is pinned in `the_glob_crate_still_differs_on_recursive_wildcards`.
///
/// Matches a NAME, never a path, so separator handling never comes into it.
fn name_matches(pattern: &str, name: &str) -> bool {
    let collapsed = collapse_star_runs(pattern);
    match glob::Pattern::new(&collapsed) {
        Ok(p) => p.matches_with(name, GLOB_OPTS),
        // An unparseable pattern matches nothing rather than aborting the whole expansion — the
        // same shape as a pattern that simply found no files.
        Err(_) => false,
    }
}

/// `glob`'s matching options, pinned to fnmatch's: case-sensitive, and no special treatment of a
/// leading dot (Python's `fnmatch` happily matches `.mp4` against `*.mp4`).
const GLOB_OPTS: glob::MatchOptions = glob::MatchOptions {
    case_sensitive: true,
    require_literal_separator: true,
    require_literal_leading_dot: false,
};

/// Collapse runs of `*` to a single `*` — fnmatch's reading, and what keeps `**` from being a
/// syntax error to the `glob` crate.
fn collapse_star_runs(pattern: &str) -> String {
    let mut out = String::with_capacity(pattern.len());
    let mut prev_star = false;
    for c in pattern.chars() {
        if c == '*' {
            if !prev_star {
                out.push(c);
            }
            prev_star = true;
        } else {
            out.push(c);
            prev_star = false;
        }
    }
    out
}

/// Expand any glob patterns in *inputs* against the sandbox, mirroring Python's `ResolveInputs`.
///
/// Native passed `*.mp4` straight to ffmpeg, which does not glob — so "convert all mp4 files in
/// this folder" rendered one command against a literal `*.mp4` and did nothing useful. Measured on
/// the 2026-09-11 L4 re-run it was 29 of 52 native-only failures, and the whole of the `batch`
/// slice (0.034 against Python's 1.000).
///
/// The semantics are Python's, and each one is load-bearing:
/// * the pattern applies to the **name component only** — `videos/*.mp4` globs inside `videos/`,
///   never recursively, so a glob cannot quietly pull in a whole tree;
/// * results are **sorted**, so the rendered command order is reproducible;
/// * **files only** — a matching directory is not an input;
/// * a path with no magic is passed through **untouched even when missing**, leaving "not found"
///   to the probe rather than silently expanding to nothing.
///
/// Not applied to `concat_video`: Python's `ConcatVideoIntent` does not route its inputs through
/// `ResolveInputs`, so globbing there would be a divergence, not a fix.
/// Expand globs to one input per matching file, keeping only media.
///
/// `media_extensions` mirrors Python, which passes the same list to `resolve_inputs` at all 13
/// of its call sites: a bare `*` is a legitimate way to say "all my media", and unfiltered it
/// handed ffmpeg the .txt and .json sitting beside the clips. An EMPTY list disables the
/// filter, so a vocab without the key behaves as before rather than matching nothing.
fn expand_input_globs(
    inputs: &[String],
    sandbox: Option<&Path>,
    media_extensions: &[String],
) -> anyhow::Result<Vec<String>> {
    let mut out = Vec::with_capacity(inputs.len());
    for raw in inputs {
        if !has_glob_magic(raw) {
            out.push(raw.clone());
            continue;
        }
        let as_path = Path::new(raw);
        let Some(file_name) = as_path.file_name().and_then(|n| n.to_str()) else {
            out.push(raw.clone());
            continue;
        };
        let parent = as_path.parent().unwrap_or(Path::new(""));
        // A bare `*.mp4` has an EMPTY parent, and `read_dir("")` fails — so in open/CLI mode the
        // glob would match nothing at all. Python never sees this because it re-bases every
        // relative path onto the sandbox (or root) first, making the parent concrete. `.` is the
        // same base the rest of CLI mode already resolves against.
        let parent = if parent.as_os_str().is_empty() {
            Path::new(".")
        } else {
            parent
        };
        // The directory is resolved and boundary-checked like any other input, so a pattern
        // cannot read outside the sandbox.
        let dir = resolve_input_in_sandbox(&parent.to_string_lossy(), sandbox)?;
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue; // No such directory: the pattern matches nothing, exactly as Python's does.
        };
        // Emit each match in the shape the pattern was written in — `a.mp4` for `*.mp4`,
        // `videos/a.mp4` for `videos/*.mp4` — rather than the resolved directory joined to the
        // name. The render loop resolves every input against the sandbox anyway, so re-basing
        // here would resolve twice and leave a synthesized `.\` in the rendered command.
        let as_written = as_path.parent().unwrap_or(Path::new(""));
        let mut matched: Vec<String> = entries
            .flatten()
            .filter(|e| e.path().is_file())
            .filter(|e| {
                e.file_name()
                    .to_str()
                    .is_some_and(|n| name_matches(file_name, n))
            })
            .filter(|e| {
                if media_extensions.is_empty() {
                    return true;
                }
                e.path()
                    .extension()
                    .and_then(|x| x.to_str())
                    .is_some_and(|x| {
                        let x = x.to_ascii_lowercase();
                        media_extensions.iter().any(|m| m.to_ascii_lowercase() == x)
                    })
            })
            .map(|e| {
                as_written
                    .join(e.file_name())
                    .to_string_lossy()
                    .into_owned()
            })
            .collect();
        matched.sort();
        out.extend(matched);
    }
    Ok(out)
}

fn resolve_input_in_sandbox(
    raw: &str,
    sandbox: Option<&Path>,
) -> anyhow::Result<std::path::PathBuf> {
    let Some(sb) = sandbox else {
        // Open/CLI mode: no boundary to enforce and nothing to re-base — keep the raw string so
        // cwd-relative behavior is unchanged.
        return Ok(std::path::PathBuf::from(raw));
    };
    let resolved = knaif_skill_api::sandbox::resolve_real(Path::new(raw), sb);
    knaif_skill_api::sandbox::assert_in_sandbox(&resolved, sb)?;
    Ok(resolved)
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
/// Public tools this runtime dispatches, declared rather than inferred from the match below.
///
/// `documents` has had an `is_supported` list since its port; ffmpeg had only the match arms, so
/// the one tool with no arm (`reverse_video`) was invisible — nothing could enumerate what was
/// missing, and the first L4 run recorded it as 25 execution errors rather than as an
/// unimplemented capability. A contract test asserts this list equals the bundle's public tools,
/// so a tool added to `tools.yaml` without a native implementation fails the build, not the user.
pub fn is_supported(tool: &str) -> bool {
    matches!(
        tool,
        "adjust_speed"
            | "adjust_volume"
            | "compress_video"
            | "concat_video"
            | "convert_video"
            | "create_thumbnail"
            | "extract_audio"
            | "prepare_for_platform"
            | "resize_video"
            | "reverse_video"
            | "rotate_video"
            | "strip_audio"
            | "trim_video"
    )
}

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
                if video_codec.is_none()
                    && data.vocab.video_encoder_map.contains_key(&c.to_lowercase())
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
            options.frames = i64_arg(args, "frames");
            // Port of `_preflight_trim_frames`. Without it the two runtimes hold opposite
            // answers to a decision the plan took explicitly: Python refuses `frames`
            // alongside a range, native silently dropped the range and rendered the count.
            if let Some(n) = options.frames {
                if n < 1 {
                    anyhow::bail!("'frames' must be at least 1, got {n}.");
                }
                let conflicting: Vec<&str> = ["end", "duration"]
                    .into_iter()
                    .filter(|k| args.get(*k).is_some_and(|v| !v.is_null()))
                    .collect();
                if !conflicting.is_empty() {
                    anyhow::bail!(
                        "'frames' cannot be combined with {} - a frame count and a time                          range are two different requests. Give one or the other.",
                        conflicting
                            .iter()
                            .map(|k| format!("'{k}'"))
                            .collect::<Vec<_>>()
                            .join(" and ")
                    );
                }
            } else if args.get("frames").is_some_and(|v| !v.is_null()) {
                anyhow::bail!("'frames' must be a whole number of frames.");
            }
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
        // The engine has implemented `mode = "reverse"` (filters, audio handling, container
        // default) and tested it all along; only this dispatch arm was missing, so the tool was
        // unreachable. Python defaults quality to `visually_good` here, not `balanced`.
        "reverse_video" => {
            options.mode = Some("reverse".into());
            options.include_audio = args.get("include_audio").and_then(Value::as_bool);
            set_output(&mut options, args);
            quality = Some(resolve_quality_profile(
                &str_arg(args, "quality").unwrap_or_else(|| "visually_good".into()),
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
        // The `not_implemented:` marker, not a bare error: this is a capability the native
        // runtime does not have, which is a different fact from a failure and has to stay
        // countable in the machine-readable output. Without it the L4 lane records the row as
        // `error`, coverage cannot see the gap, and the quality average is computed over a
        // population that silently excludes what the port cannot do (L4d, N6).
        other => anyhow::bail!(knaif_skill_api::capability::not_implemented_message(
            &format!("the ffmpeg intent {other:?} is not built into the native runtime yet")
        )),
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

/// A whole-number arg, tolerating a numeric string (`"3"`) the way Python's `int(...)` would.
/// Used for `frames`, which is a COUNT: a fractional value is not a smaller request, it is a
/// different kind of thing, so it is rejected here rather than truncated.
fn i64_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<i64> {
    match args.get(key) {
        Some(Value::Number(n)) => n.as_i64(),
        Some(Value::String(s)) => s.trim().parse().ok(),
        _ => None,
    }
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

/// The contract the port owes Python, as data: every expectation generated from
/// `fnmatch.fnmatchcase`, the function behind `Path.glob`. Shared by the two matcher tests so
/// the hand-rolled matcher and the `glob` crate are judged against the SAME cases.
#[cfg(test)]
const PYTHON_FNMATCH_CASES: &[(&str, &str, bool)] = &[
    ("*.mp4", "a.mp4", true),
    ("*.mp4", "a.mkv", false),
    ("*.mp4", ".mp4", true),
    ("*", "x", true),
    ("*", "", true),
    ("a*b", "ab", true),
    ("a*b", "axxb", true),
    ("a*b", "axxc", false),
    ("a*b*c", "axbyc", true),
    ("a*b*c", "abc", true),
    ("?.mp4", "a.mp4", true),
    ("?.mp4", "ab.mp4", false),
    ("clip?.mp4", "clip1.mp4", true),
    ("[ab].mp4", "a.mp4", true),
    ("[ab].mp4", "c.mp4", false),
    ("[a-c].mp4", "b.mp4", true),
    ("[a-c].mp4", "d.mp4", false),
    ("[!a].mp4", "b.mp4", true),
    ("[!a].mp4", "a.mp4", false),
    ("[^a].mp4", "b.mp4", false),
    ("[^a].mp4", "^.mp4", true),
    ("*.MP4", "a.mp4", false),
    ("clip*.mp4", "clip_4k.mp4", true),
    ("*.*", "a.b", true),
    ("*.*", "ab", false),
    ("**.mp4", "a.mp4", true),
    ("a**b", "aXb", true),
];

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

    // `reverse_video` was the one public ffmpeg tool with no native dispatch arm, even though
    // the engine has implemented and tested `mode = "reverse"` all along (engine.rs). Ground
    // truth from Python for the same intent args.
    #[test]
    fn reverse_keeps_audio_by_default() {
        assert_eq!(
            cmd("reverse_video", serde_json::json!({"inputs": "clip.mp4"})),
            vec![
                "ffmpeg",
                "-y",
                "-i",
                "clip.mp4",
                "-vf",
                "reverse",
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-af",
                "areverse",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                // `.mp4`, not `.mov`, and the difference is the STUB PROBE - not a divergence.
                // Reverse preserves the source container (engine.rs, `mode == "reverse"`), taking
                // `probe.container` first and falling back to the input's extension. These tests
                // expand without a real file, so the stub reports no container and the extension
                // wins. Against the real `clip.mp4`, ffprobe reports the container as `mov` and
                // BOTH runtimes render `clip_reversed.mov` - verified end to end.
                "clip_reversed.mp4"
            ]
        );
    }

    #[test]
    fn reverse_without_audio_drops_the_track() {
        let got = cmd(
            "reverse_video",
            serde_json::json!({"inputs": "clip.mp4", "include_audio": false}),
        );
        assert!(got.contains(&"-an".to_string()), "got {got:?}");
        assert!(!got.contains(&"areverse".to_string()), "got {got:?}");
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

    #[test]
    fn the_glob_crate_still_differs_on_recursive_wildcards() {
        // WHY `collapse_star_runs` EXISTS, pinned as a fact about the crate rather than left as
        // a comment. `glob` reads `**` as a recursive wildcard and REJECTS it outside a whole
        // path component; Python's fnmatch reads it as an ordinary run of stars. Found by
        // diffing, not by reading docs.
        //
        // If a future `glob` release starts accepting these, this test fails and the
        // normalization can be reconsidered — which is the point of pinning it.
        for pattern in ["**.mp4", "a**b"] {
            assert!(
                glob::Pattern::new(pattern).is_err(),
                "{pattern:?} now parses; re-evaluate collapse_star_runs"
            );
            assert!(
                glob::Pattern::new(&collapse_star_runs(pattern)).is_ok(),
                "collapsing must make {pattern:?} parseable"
            );
        }
    }

    #[test]
    fn the_matcher_agrees_with_pythons_fnmatch() {
        for (pattern, name, expected) in PYTHON_FNMATCH_CASES {
            assert_eq!(
                name_matches(pattern, name),
                *expected,
                "{pattern} vs {name}"
            );
        }
    }

    // ---- N1: glob expansion (the largest single cause of the L4 outcome gap) ----
    //
    // Python's `ResolveInputs` expands a pattern against the sandbox and yields ONE command per
    // matching file; native passed the literal `*.mp4` to ffmpeg, which does not glob. Measured
    // on the 2026-09-11 L4 re-run: 29 of the 52 native-only failures, and the `batch` slice at
    // 0.034 against Python's 1.000.
    //
    // Semantics ported deliberately (python/core/knaif/steps/_resolve_inputs.py):
    //   * the pattern applies to the NAME component only - `videos/*.mp4` globs inside
    //     `videos/`, never recursively;
    //   * results are SORTED, so the command order is deterministic;
    //   * directories match nothing (files only);
    //   * a path with no magic characters is passed through untouched, even if missing, so
    //     "file not found" stays the probe's error to report.

    fn glob_sandbox(tag: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("knaif-ffmpeg-glob-{}-{}", std::process::id(), tag));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_glob_expands_to_one_command_per_matching_file() {
        let sandbox = glob_sandbox("basic");
        for name in ["b.mp4", "a.mp4", "notes.txt"] {
            std::fs::write(sandbox.join(name), b"x").unwrap();
        }
        let exp = expand_dry_run(
            "convert_video",
            &args(serde_json::json!({"inputs": ["*.mp4"], "container": "mkv"})),
            &data(),
            Some(&sandbox),
        )
        .unwrap();
        let cmds = match exp {
            Expansion::Commands(c) => c,
            Expansion::Clarify(q) => panic!("expected commands, got clarify: {q}"),
        };
        assert_eq!(cmds.len(), 2, "one command per matching file: {cmds:?}");
        // Sorted, so the order is reproducible across runs and platforms.
        assert!(cmds[0].iter().any(|a| a.ends_with("a.mp4")), "{cmds:?}");
        assert!(cmds[1].iter().any(|a| a.ends_with("b.mp4")), "{cmds:?}");
        // The non-matching file is not swept in.
        assert!(
            !cmds.iter().flatten().any(|a| a.ends_with("notes.txt")),
            "{cmds:?}"
        );
        std::fs::remove_dir_all(&sandbox).ok();
    }

    #[test]
    fn a_glob_never_leaves_its_own_directory() {
        // `videos/*.mp4` must not reach a sibling directory, and must not recurse.
        let sandbox = glob_sandbox("scoped");
        std::fs::create_dir_all(sandbox.join("videos/nested")).unwrap();
        std::fs::create_dir_all(sandbox.join("other")).unwrap();
        std::fs::write(sandbox.join("videos/in.mp4"), b"x").unwrap();
        std::fs::write(sandbox.join("videos/nested/deep.mp4"), b"x").unwrap();
        std::fs::write(sandbox.join("other/sibling.mp4"), b"x").unwrap();

        let exp = expand_dry_run(
            "convert_video",
            &args(serde_json::json!({"inputs": ["videos/*.mp4"], "container": "mkv"})),
            &data(),
            Some(&sandbox),
        )
        .unwrap();
        let cmds = match exp {
            Expansion::Commands(c) => c,
            Expansion::Clarify(q) => panic!("clarify: {q}"),
        };
        assert_eq!(cmds.len(), 1, "only videos/in.mp4 matches: {cmds:?}");
        assert!(cmds[0].iter().any(|a| a.ends_with("in.mp4")), "{cmds:?}");
        std::fs::remove_dir_all(&sandbox).ok();
    }

    #[test]
    fn a_plain_path_is_passed_through_even_when_missing() {
        // No magic characters: not a glob, so a missing file stays the probe's error to report
        // rather than silently expanding to nothing.
        let sandbox = glob_sandbox("plain");
        let exp = expand_dry_run(
            "convert_video",
            &args(serde_json::json!({"inputs": ["ghost.mp4"], "container": "mkv"})),
            &data(),
            Some(&sandbox),
        )
        .unwrap();
        match exp {
            Expansion::Commands(c) => {
                assert_eq!(c.len(), 1, "the missing path still renders one command")
            }
            Expansion::Clarify(q) => panic!("clarify: {q}"),
        }
        std::fs::remove_dir_all(&sandbox).ok();
    }

    #[test]
    fn sandbox_input_escape_with_in_sandbox_output_is_rejected() {
        // F3's precise gap: `sandbox_escape_is_rejected` above passes, but only because the
        // DERIVED output (from the out-of-sandbox input's own directory) also lands outside
        // the sandbox — the output-side check catches it by accident. Here the explicit
        // output is genuinely inside the sandbox, so the output check alone would pass; this
        // only fails if `inputs` is itself resolved + boundary-checked before being probed
        // (the audit's literal repro: an absolute input outside the sandbox with an explicit
        // output inside it reached `ffprobe`/render with no rejection).
        let tmp = std::env::temp_dir().join(format!(
            "knaif-ffmpeg-run-test-{}-input-escape",
            std::process::id()
        ));
        let sandbox = tmp.join("sandbox");
        let outside = tmp.join("outside");
        std::fs::create_dir_all(&sandbox).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        let outside_input = outside.join("secret.mp4");
        let in_sandbox_output = sandbox.join("escaped-read.mp4");

        let result = expand_dry_run(
            "strip_audio",
            &args(serde_json::json!({
                "inputs": outside_input.to_string_lossy(),
                "output": in_sandbox_output.to_string_lossy(),
            })),
            &data(),
            Some(&sandbox),
        );
        assert!(
            result.is_err(),
            "an out-of-sandbox input must be rejected even with an in-sandbox explicit output"
        );

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn relative_input_renders_the_sandbox_file_not_the_cwd_one() {
        // Fix review R1: validating one representation while probing/rendering another is not a
        // boundary. With a working directory that is NOT the sandbox and a same-named file in
        // each, checking `<sandbox>/clip.mp4` while ffprobe/ffmpeg open `<cwd>/clip.mp4` reads a
        // different file than the one cleared. The rendered command must name the resolved,
        // checked path — this test fails if the raw relative string is rendered instead.
        let tmp = std::env::temp_dir().join(format!(
            "knaif-ffmpeg-run-test-{}-cwd-vs-sandbox",
            std::process::id()
        ));
        let sandbox = tmp.join("sandbox");
        let outside = tmp.join("outside");
        std::fs::create_dir_all(&sandbox).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        // Same basename in both; only the sandbox one is legitimately reachable.
        std::fs::write(sandbox.join("clip.mp4"), b"sandbox").unwrap();
        std::fs::write(outside.join("clip.mp4"), b"outside").unwrap();

        let exp = expand_dry_run(
            "strip_audio",
            &args(serde_json::json!({"inputs": "clip.mp4"})),
            &data(),
            Some(&sandbox),
        )
        .unwrap();
        let commands = match exp {
            Expansion::Commands(c) => c,
            Expansion::Clarify(q) => panic!("expected commands, got clarify: {q}"),
        };
        let rendered = commands[0].join(" ").replace('\\', "/");
        let sandbox_str = sandbox.to_string_lossy().replace('\\', "/");
        let outside_str = outside.to_string_lossy().replace('\\', "/");
        assert!(
            rendered.contains(sandbox_str.trim_start_matches("//?/")),
            "rendered command must name the resolved sandbox input: {rendered}"
        );
        assert!(
            !rendered.contains(&outside_str),
            "rendered command must never name the cwd/outside file: {rendered}"
        );

        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── a silent input must not fail the batch it is in ──────────────────────
    //
    // Mirrors `skills/ffmpeg/python/tests/test_silent_input_batch.py`. Tested at this level
    // rather than through `expand_dry_run` deliberately: dry-run stubs a missing file with
    // `dummy_probe`, which reports `has_audio: true`, so a test written that way would pass
    // without ever exercising the skip — and on a machine without ffprobe it would silently
    // stop testing anything at all.

    #[test]
    fn extract_audio_skips_a_silent_input() {
        assert!(skips_silent_input(Some("extract_audio"), false));
        assert!(!skips_silent_input(Some("extract_audio"), true));
    }

    #[test]
    fn other_modes_keep_silent_inputs() {
        // Measured on a real silent file: both succeed, so skipping would drop real work.
        for mode in ["adjust_volume", "strip_audio", "convert", "compress"] {
            assert!(
                !skips_silent_input(Some(mode), false),
                "{mode} dropped a silent input"
            );
        }
        assert!(!skips_silent_input(None, false));
    }

    #[test]
    fn the_no_audio_message_matches_python_byte_for_byte() {
        assert_eq!(
            no_audio_error(&["clip_no_audio.mp4".to_string()]),
            "No audio to extract: clip_no_audio.mp4 has no audio track."
        );
        assert_eq!(
            no_audio_error(&["a.mp4".to_string(), "b.mp4".to_string()]),
            "No audio to extract — none of these files has an audio track: a.mp4, b.mp4."
        );
    }
}
