//! ffmpeg expansion with a run scratch: a routed chain intermediate in `<work>` is a valid input,
//! the output named after it lands in the user's folder (never in the scratch), and a preview
//! clip renders into the scratch. Mirrors the Python behaviour in
//! `skills/ffmpeg/python/tests/test_no_leftover_files.py` without executing anything.

use std::path::{Path, PathBuf};

use knaif_skill_ffmpeg::run::{expand_in, preview_command, Expansion, ProbeMode, ScratchCtx};
use knaif_skill_ffmpeg::FfmpegData;
use serde_json::{json, Value};

fn data() -> FfmpegData {
    FfmpegData::load(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg")).unwrap()
}

fn args(v: Value) -> serde_json::Map<String, Value> {
    v.as_object().unwrap().clone()
}

fn dirs(name: &str) -> (PathBuf, PathBuf, PathBuf) {
    let root = std::env::temp_dir().join(format!("knaif-ffx-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let (sb, work) = (root.join("sb"), root.join("work"));
    std::fs::create_dir_all(&sb).unwrap();
    std::fs::create_dir_all(&work).unwrap();
    (root, sb, work)
}

fn commands(exp: Expansion) -> Vec<Vec<String>> {
    match exp {
        Expansion::Commands(c) => c,
        Expansion::Clarify(q) => panic!("clarify: {q}"),
    }
}

#[test]
fn a_scratch_input_is_accepted_and_its_output_lands_in_the_users_folder() {
    let (root, sb, work) = dirs("anchor");
    let mid = work.join("videos").join("clip_trimmed.mp4");
    let scratch = ScratchCtx {
        work_dir: &work,
        home: &sb,
    };
    let cmds = commands(
        expand_in(
            "resize_video",
            &args(json!({"inputs": [mid.to_string_lossy()], "height": 240})),
            &data(),
            Some(&sb),
            ProbeMode::DryRun,
            Some(&scratch),
        )
        .unwrap(),
    );
    let out = PathBuf::from(cmds[0].last().unwrap());
    assert_eq!(out.file_name().unwrap(), "clip_trimmed_resized.mp4");
    assert!(
        out.starts_with(sb.join("videos")),
        "the deliverable must land in the user's folder, got {}",
        out.display()
    );
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn without_a_scratch_a_path_in_it_is_outside_the_sandbox() {
    let (root, sb, work) = dirs("noscratch");
    let mid = work.join("clip_trimmed.mp4");
    let err = expand_in(
        "resize_video",
        &args(json!({"inputs": [mid.to_string_lossy()], "height": 240})),
        &data(),
        Some(&sb),
        ProbeMode::DryRun,
        None,
    )
    .unwrap_err();
    assert!(err.to_string().contains("outside the sandbox"), "{err}");
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn a_routed_output_may_be_in_the_scratch() {
    let (root, sb, work) = dirs("routedout");
    let scratch = ScratchCtx {
        work_dir: &work,
        home: &sb,
    };
    let mid = work.join("clip_trimmed.mp4");
    let cmds = commands(
        expand_in(
            "trim_video",
            &args(json!({"input": "clip.mp4", "end": "00:00:02", "output": mid.to_string_lossy()})),
            &data(),
            Some(&sb),
            ProbeMode::DryRun,
            Some(&scratch),
        )
        .unwrap(),
    );
    assert_eq!(PathBuf::from(cmds[0].last().unwrap()), mid);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn a_preview_clip_renders_into_the_scratch() {
    let (root, sb, work) = dirs("preview");
    let scratch = ScratchCtx {
        work_dir: &work,
        home: &sb,
    };
    let preview = preview_command(
        "reverse_video",
        &args(json!({"inputs": ["clip.mp4"]})),
        &data(),
        Some(&sb),
        ProbeMode::DryRun,
        &scratch,
    )
    .unwrap()
    .expect("reverse_video previews");
    let out = PathBuf::from(preview.last().unwrap());
    assert_eq!(out, work.join("preview").join("clip_preview.mp4"));
    // First input, 10 s from the start: Python's `render_preview_command`.
    let pos = |flag: &str| preview.iter().position(|a| a == flag);
    assert_eq!(preview[pos("-ss").unwrap() + 1], "0");
    assert_eq!(preview[pos("-t").unwrap() + 1], "10");
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn a_tool_that_does_not_preview_renders_none() {
    let (root, sb, work) = dirs("nopreview");
    let scratch = ScratchCtx {
        work_dir: &work,
        home: &sb,
    };
    let none = preview_command(
        "convert_video",
        &args(json!({"inputs": ["clip.mp4"], "container": "mkv"})),
        &data(),
        Some(&sb),
        ProbeMode::DryRun,
        &scratch,
    )
    .unwrap();
    assert!(none.is_none());
    let asked = preview_command(
        "convert_video",
        &args(json!({"inputs": ["clip.mp4"], "container": "mkv", "preview": true})),
        &data(),
        Some(&sb),
        ProbeMode::DryRun,
        &scratch,
    )
    .unwrap();
    assert!(asked.is_some());
    let _ = std::fs::remove_dir_all(root);
}
