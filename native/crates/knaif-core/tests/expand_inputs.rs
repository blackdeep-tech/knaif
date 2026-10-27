//! What a batch glob or folder expands to on disk, before the lift. The same cases
//! as Python's `python/core/tests/test_batch_chain_agent.py` expansion tests.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use knaif_core::batch_chain::{expand_inputs, natural_key};

fn folder(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("knaif-expand-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn touch(dir: &Path, names: &[&str]) {
    std::fs::create_dir_all(dir).unwrap();
    for n in names {
        std::fs::write(dir.join(n), b"x").unwrap();
    }
}

fn owned(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| s.to_string()).collect()
}

#[test]
fn natural_order_counts_numbers_as_numbers() {
    let mut names = owned(&[
        "clip10.mov",
        "Clip2.mov",
        "clip1.mov",
        "clip.mov",
        "clip02.mov",
    ]);
    names.sort_by_key(|n| natural_key(n));
    assert_eq!(
        names,
        owned(&[
            "clip.mov",
            "clip1.mov",
            "Clip2.mov",
            "clip02.mov",
            "clip10.mov"
        ])
    );
}

#[test]
fn a_glob_expands_in_natural_order_with_the_models_spelling() {
    let base = folder("glob");
    touch(
        &base.join("videos"),
        &["clip10.mov", "clip2.mov", "clip1.mov", "notes.txt"],
    );
    let got = expand_inputs(
        &owned(&["videos/*.mov", "poster.png"]),
        &base,
        &HashMap::new(),
    );
    let _ = std::fs::remove_dir_all(&base);
    assert_eq!(
        got,
        HashMap::from([(
            "videos/*.mov".to_string(),
            owned(&["videos/clip1.mov", "videos/clip2.mov", "videos/clip10.mov"])
        )])
    );
}

#[test]
fn a_folder_expands_to_its_files_of_the_skills_kinds() {
    let base = folder("kinds");
    touch(&base.join("clips"), &["b.mp4", "a.mov", "notes.txt"]);
    std::fs::create_dir_all(base.join("clips").join("sub")).unwrap();
    let kinds = HashMap::from([
        ("mp4".to_string(), "video".to_string()),
        ("mov".to_string(), "video".to_string()),
    ]);
    let by_folder = expand_inputs(&owned(&["clips/"]), &base, &kinds);
    let by_star = expand_inputs(&owned(&["*"]), &base.join("clips"), &kinds);
    let _ = std::fs::remove_dir_all(&base);
    assert_eq!(
        by_folder,
        HashMap::from([("clips/".to_string(), owned(&["clips/a.mov", "clips/b.mp4"]))])
    );
    assert_eq!(
        by_star,
        HashMap::from([("*".to_string(), owned(&["a.mov", "b.mp4"]))])
    );
}

#[test]
fn a_glob_without_matches_expands_to_nothing() {
    let base = folder("none");
    let got = expand_inputs(&owned(&["*.webm"]), &base, &HashMap::new());
    let _ = std::fs::remove_dir_all(&base);
    assert_eq!(got, HashMap::from([("*.webm".to_string(), Vec::new())]));
}
