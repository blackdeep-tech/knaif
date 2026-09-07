//! Filesystem-aware sandbox containment — shared by every native skill.
//!
//! A purely lexical `.`/`..` collapse (the previous approach here and duplicated in every
//! skill's own `lexical_abs`) never touches the filesystem, so it cannot see that a path
//! *inside* the sandbox is actually a symlink or (on Windows) a directory junction pointing
//! *outside* it. Python's equivalent check (`Path.resolve()`, in
//! `knaif.steps._resolve_inputs` and `knaif.planner._resolve_path`) calls into the OS and
//! already rejects that case — this module closes the native gap so both runtimes enforce
//! the same boundary. See docs/audits/2026-09-07-core-principles-and-rtx5080.md, F4.

use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};

/// Collapse `.`/`..` segments lexically (no filesystem access), so a path that doesn't exist
/// yet can still be normalized. Symlinks/junctions in the path are NOT resolved by this alone
/// — use [`resolve_real`] when the result feeds a containment check.
pub fn lexical_normalize(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for comp in p.components() {
        match comp {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// Resolve `p` to its real, filesystem-canonical absolute form: every existing ancestor is
/// canonicalized — following symlinks and, on Windows, directory junctions/reparse points —
/// and any trailing components that don't exist yet (e.g. an output file about to be created)
/// are appended lexically on top of the deepest existing, resolved ancestor. Falls back to
/// pure lexical normalization if nothing in `p` exists at all — still boundary-checkable,
/// just not link-aware (there is nothing on disk to follow).
///
/// `p` is made absolute against `base` first when relative; an absolute `p` ignores `base`.
pub fn resolve_real(p: &Path, base: &Path) -> PathBuf {
    let abs = if p.is_absolute() {
        p.to_path_buf()
    } else {
        base.join(p)
    };
    let normalized = lexical_normalize(&abs);

    let mut existing = normalized.clone();
    let mut tail: Vec<OsString> = Vec::new();
    loop {
        if let Ok(real) = existing.canonicalize() {
            let mut out = real;
            for part in tail.iter().rev() {
                out.push(part);
            }
            return out;
        }
        match existing.file_name() {
            Some(name) => {
                tail.push(name.to_os_string());
                if !existing.pop() {
                    return normalized;
                }
            }
            None => return normalized, // exhausted the path; nothing on it exists
        }
    }
}

/// Raise if `p` is not inside `sandbox`. Both are resolved filesystem-real via
/// [`resolve_real`] (relative to the current working directory) before comparison, so a
/// link that lexically reads as "inside" but really points outside is caught.
pub fn assert_in_sandbox(p: &Path, sandbox: &Path) -> anyhow::Result<()> {
    let cwd = std::env::current_dir().unwrap_or_default();
    let rp = resolve_real(p, &cwd);
    let rs = resolve_real(sandbox, &cwd);
    if !rp.starts_with(&rs) {
        anyhow::bail!("Path {:?} is outside the sandbox {:?}", rp, rs);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_real_lexical_fallback_for_nonexistent_tail() {
        // `tmp` exists (so it gets a real, canonicalized resolution — on Windows that
        // means picking up the `\\?\` extended prefix, which is why this doesn't compare
        // against a hand-built string); "sub/../escape/x.txt" under it does not — the `..`
        // must still collapse lexically against the real, existing ancestor.
        let tmp = std::env::temp_dir().join(format!(
            "knaif-sandbox-test-{}-{}",
            std::process::id(),
            "lexical-fallback"
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let resolved = resolve_real(Path::new("sub/../escape/x.txt"), &tmp);
        assert_eq!(
            resolved,
            tmp.canonicalize().unwrap().join("escape").join("x.txt")
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn resolve_real_resolves_existing_ancestor_and_appends_tail() {
        let tmp = std::env::temp_dir().join(format!(
            "knaif-sandbox-test-{}-{}",
            std::process::id(),
            "resolve-real"
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let resolved = resolve_real(Path::new("not-yet-created.txt"), &tmp);
        // The existing ancestor (tmp) must be canonicalized (e.g. drive letter case, UNC
        // prefix normalized); the not-yet-existing tail is appended lexically on top of it.
        assert_eq!(resolved.file_name().unwrap(), "not-yet-created.txt");
        assert!(resolved.starts_with(tmp.canonicalize().unwrap()));
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn different_directories_sharing_a_basename_are_not_conflated() {
        let tmp = std::env::temp_dir().join(format!(
            "knaif-sandbox-test-{}-{}",
            std::process::id(),
            "diff-dirs"
        ));
        let a = tmp.join("a");
        let b = tmp.join("b");
        std::fs::create_dir_all(&a).unwrap();
        std::fs::create_dir_all(&b).unwrap();
        std::fs::write(a.join("clip.mp4"), b"a").unwrap();
        std::fs::write(b.join("clip.mp4"), b"b").unwrap();

        let ra = resolve_real(&a.join("clip.mp4"), &tmp);
        let rb = resolve_real(&b.join("clip.mp4"), &tmp);
        assert_ne!(ra, rb, "different directories must not resolve equal");
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[cfg(windows)]
    #[test]
    fn assert_in_sandbox_rejects_a_junction_escape() {
        // The audit's literal F4 repro (Windows junction): a junction placed INSIDE the
        // sandbox, pointing to a directory OUTSIDE it. A purely lexical check sees the
        // junction's own path (inside the sandbox) and accepts it; resolve_real must follow
        // the reparse point and reject the read.
        let tmp = std::env::temp_dir().join(format!(
            "knaif-sandbox-test-{}-{}",
            std::process::id(),
            "junction"
        ));
        let sandbox = tmp.join("sandbox");
        let outside = tmp.join("outside");
        std::fs::create_dir_all(&sandbox).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(outside.join("secret.txt"), b"secret").unwrap();

        let link = sandbox.join("escape_link");
        let status = std::process::Command::new("cmd")
            .args([
                "/C",
                "mklink",
                "/J",
                &link.display().to_string(),
                &outside.display().to_string(),
            ])
            .status()
            .expect("mklink must run on Windows");
        assert!(
            status.success(),
            "junction creation must succeed (no admin needed)"
        );

        let escaped = link.join("secret.txt");
        let err = assert_in_sandbox(&escaped, &sandbox).unwrap_err();
        assert!(err.to_string().contains("outside the sandbox"), "{err}");

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[cfg(unix)]
    #[test]
    fn assert_in_sandbox_rejects_a_symlink_escape() {
        let tmp = std::env::temp_dir().join(format!(
            "knaif-sandbox-test-{}-{}",
            std::process::id(),
            "symlink"
        ));
        let sandbox = tmp.join("sandbox");
        let outside = tmp.join("outside");
        std::fs::create_dir_all(&sandbox).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(outside.join("secret.txt"), b"secret").unwrap();

        let link = sandbox.join("escape_link");
        std::os::unix::fs::symlink(&outside, &link).unwrap();

        let escaped = link.join("secret.txt");
        let err = assert_in_sandbox(&escaped, &sandbox).unwrap_err();
        assert!(err.to_string().contains("outside the sandbox"), "{err}");

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn assert_in_sandbox_allows_a_plain_inside_path() {
        let tmp = std::env::temp_dir().join(format!(
            "knaif-sandbox-test-{}-{}",
            std::process::id(),
            "inside"
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        std::fs::write(tmp.join("clip.mp4"), b"x").unwrap();
        assert!(assert_in_sandbox(&tmp.join("clip.mp4"), &tmp).is_ok());
        std::fs::remove_dir_all(&tmp).ok();
    }
}
