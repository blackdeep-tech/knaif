//! The fs toolkit's write tools, native side. Mirrors `toolkits/fs/python/tests/test_write_tools.py`
//! case for case: stage an output in the run scratch, check it, move it into place; a failure
//! leaves the user's folder untouched.

use std::path::{Path, PathBuf};

use knaif_toolkit_fs::{
    commit_output, ensure_parent, free_output_path, stage_copy, stage_output, user_side_path,
    FreeName,
};

fn dirs(name: &str) -> (PathBuf, PathBuf, PathBuf) {
    let root = std::env::temp_dir().join(format!("knaif-fs-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let (sb, work) = (root.join("sb"), root.join("work"));
    std::fs::create_dir_all(&sb).unwrap();
    std::fs::create_dir_all(&work).unwrap();
    (root, sb, work)
}

// ── stage_output ────────────────────────────────────────────────────────────────────────────

#[test]
fn a_staged_path_is_in_scratch_and_keeps_the_final_name() {
    let (root, sb, work) = dirs("stage");
    let staged = stage_output(&work, &sb.join("out.mkv")).unwrap();
    assert!(staged.starts_with(&work));
    assert_eq!(staged.file_name().unwrap(), "out.mkv");
    assert!(staged.parent().unwrap().is_dir());
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn two_stages_of_one_name_never_share_a_path() {
    let (root, sb, work) = dirs("stage2");
    let a = stage_output(&work, &sb.join("a/x.mp4")).unwrap();
    let b = stage_output(&work, &sb.join("b/x.mp4")).unwrap();
    assert_ne!(a, b);
    let _ = std::fs::remove_dir_all(root);
}

// ── stage_copy ──────────────────────────────────────────────────────────────────────────────

#[test]
fn a_staged_copy_of_a_scratch_file_commits_like_any_staged_file() {
    let (root, sb, work) = dirs("stagecopy");
    let source = work.join("clip.mp4");
    std::fs::write(&source, b"frames").unwrap();
    let staged = stage_copy(&work, &source, &sb.join("clip_compressed.mp4")).unwrap();
    assert!(staged.starts_with(&work));
    assert_eq!(staged.file_name().unwrap(), "clip_compressed.mp4");
    assert_eq!(std::fs::read(&staged).unwrap(), b"frames");
    assert_eq!(std::fs::read(&source).unwrap(), b"frames");
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn a_staged_copy_only_copies_from_the_scratch() {
    let (root, sb, work) = dirs("stagecopy2");
    let users = sb.join("clip.mp4");
    std::fs::write(&users, b"frames").unwrap();
    let err = stage_copy(&work, &users, &sb.join("out.mp4")).unwrap_err();
    assert!(err.to_string().contains("run scratch"), "{err}");
    let _ = std::fs::remove_dir_all(root);
}

// ── commit_output ───────────────────────────────────────────────────────────────────────────

#[test]
fn commit_moves_the_staged_file_into_place() {
    let (root, sb, work) = dirs("commit");
    let fin = sb.join("videos/out.mp4");
    let staged = stage_output(&work, &fin).unwrap();
    std::fs::write(&staged, b"frames").unwrap();
    let bytes = commit_output(&staged, &fin, Some(&sb), &work).unwrap();
    assert_eq!(bytes, 6);
    assert_eq!(std::fs::read(&fin).unwrap(), b"frames");
    assert!(!staged.exists());
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn commit_replaces_a_file_the_user_approved() {
    let (root, sb, work) = dirs("replace");
    let fin = sb.join("out.mp4");
    std::fs::write(&fin, b"old").unwrap();
    let staged = stage_output(&work, &fin).unwrap();
    std::fs::write(&staged, b"new").unwrap();
    commit_output(&staged, &fin, Some(&sb), &work).unwrap();
    assert_eq!(std::fs::read(&fin).unwrap(), b"new");
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn an_empty_or_missing_output_is_refused_and_nothing_lands() {
    for content in [None, Some(&b""[..])] {
        let (root, sb, work) = dirs("empty");
        let fin = sb.join("out.mp4");
        let staged = stage_output(&work, &fin).unwrap();
        if let Some(c) = content {
            std::fs::write(&staged, c).unwrap();
        }
        let err = commit_output(&staged, &fin, Some(&sb), &work).unwrap_err();
        assert!(err.to_string().contains("out.mp4"), "{err}");
        assert!(!fin.exists());
        assert!(!staged.exists());
        let _ = std::fs::remove_dir_all(root);
    }
}

#[test]
fn a_final_output_can_never_land_in_scratch() {
    let (root, sb, work) = dirs("noscratch");
    let staged = stage_output(&work, &sb.join("out.mp4")).unwrap();
    std::fs::write(&staged, b"x").unwrap();
    let err = commit_output(&staged, &work.join("o.mp4"), Some(&sb), &work).unwrap_err();
    assert!(err.to_string().contains("scratch"), "{err}");
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn a_final_output_outside_the_sandbox_is_refused() {
    let (root, sb, work) = dirs("outside");
    let staged = stage_output(&work, &sb.join("out.mp4")).unwrap();
    std::fs::write(&staged, b"x").unwrap();
    let err = commit_output(&staged, &root.join("elsewhere.mp4"), Some(&sb), &work).unwrap_err();
    assert!(err.to_string().contains("outside the sandbox"), "{err}");
    let _ = std::fs::remove_dir_all(root);
}

// ── ensure_parent ───────────────────────────────────────────────────────────────────────────

#[test]
fn ensure_parent_makes_the_destination_folder() {
    let (root, sb, work) = dirs("parent");
    ensure_parent(&sb.join("exports/hevc/clip.mkv"), Some(&sb), Some(&work)).unwrap();
    assert!(sb.join("exports/hevc").is_dir());
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn ensure_parent_creates_the_resolved_parent_only() {
    let (root, sb, work) = dirs("resolved");
    ensure_parent(
        &sb.join("../escaped/../sb/new/out.mp4"),
        Some(&sb),
        Some(&work),
    )
    .unwrap();
    assert!(sb.join("new").is_dir());
    assert!(!root.join("escaped").exists());
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn ensure_parent_refuses_a_path_outside_the_sandbox() {
    let (root, sb, work) = dirs("parentout");
    let err = ensure_parent(&root.join("x/y.mp4"), Some(&sb), Some(&work)).unwrap_err();
    assert!(err.to_string().contains("outside the sandbox"), "{err}");
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn ensure_parent_accepts_the_run_scratch() {
    let (root, sb, work) = dirs("parentwork");
    ensure_parent(&work.join("videos/x.mp4"), Some(&sb), Some(&work)).unwrap();
    assert!(work.join("videos").is_dir());
    let _ = std::fs::remove_dir_all(root);
}

// ── free_output_path ────────────────────────────────────────────────────────────────────────

fn name(p: &Path) -> String {
    p.file_name().unwrap().to_string_lossy().into_owned()
}

#[test]
fn documents_style_numbering() {
    let (root, sb, _) = dirs("docs");
    std::fs::write(sb.join("notes.md"), b"x").unwrap();
    std::fs::write(sb.join("notes-1.md"), b"x").unwrap();
    let rule = FreeName {
        separator: "-",
        first_number: 1,
        keep_requested: true,
        ..FreeName::default()
    };
    assert_eq!(
        name(&free_output_path(&sb.join("notes.md"), &[], &rule)),
        "notes-2.md"
    );
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn ffmpeg_style_numbering_never_returns_the_requested_name() {
    let (root, sb, _) = dirs("ffmpeg");
    let rule = FreeName {
        suffix: "_converted",
        ..FreeName::default()
    };
    let taken = [sb.join("clip_converted.mp4")];
    assert_eq!(
        name(&free_output_path(&sb.join("clip.mp4"), &taken, &rule)),
        "clip_converted_2.mp4"
    );
    let _ = std::fs::remove_dir_all(root);
}

// ── user_side_path ──────────────────────────────────────────────────────────────────────────

#[test]
fn a_scratch_path_maps_back_to_its_place_in_the_users_folder() {
    let (root, sb, work) = dirs("userside");
    let got = user_side_path(&work.join("docs").join("a.pdf"), Some(&work), Some(&sb));
    assert_eq!(got, sb.join("docs").join("a.pdf"));
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn a_path_outside_the_scratch_is_returned_as_it_is() {
    let (root, sb, work) = dirs("userside2");
    let p = sb.join("a.pdf");
    assert_eq!(user_side_path(&p, Some(&work), Some(&sb)), p);
    assert_eq!(
        user_side_path(&work.join("a.pdf"), None, Some(&sb)),
        work.join("a.pdf")
    );
    let _ = std::fs::remove_dir_all(root);
}
