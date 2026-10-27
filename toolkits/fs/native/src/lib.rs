//! knaif-toolkit-fs: the native half of the `fs` toolkit (`toolkits/fs/tools.yaml`).
//!
//! Port of `toolkits/fs/python/write.py`. Nothing a step writes reaches the user's folder until it
//! is known to be good: [`stage_output`] hands out a path in the run scratch with the final file's
//! name, the step writes there, and [`commit_output`] checks it and moves it into place. A failure
//! at any point leaves the user's folder as it was; the run scratch is removed when the run ends
//! (`knaif_core::scratch`).
//!
//! A plain library that skill crates call (no generic Handler trait yet, audit F11). Every
//! containment check goes through `knaif_core::sandbox`, which resolves links and junctions.

use std::path::{Path, PathBuf};

use knaif_core::sandbox::{assert_in_sandbox, resolve_real};

/// The order a folder or a glob expands in (`clip1`, `clip2`, `clip10`), for a skill that lists
/// files itself. `knaif_core::batch_chain::natural_key`.
pub use knaif_core::batch_chain::natural_key;

fn cwd() -> PathBuf {
    std::env::current_dir().unwrap_or_default()
}

/// Is `path` inside `folder`, both resolved for real? False when there is no folder.
pub fn within(path: &Path, folder: Option<&Path>) -> bool {
    match folder {
        Some(f) => resolve_real(path, &cwd()).starts_with(resolve_real(f, &cwd())),
        None => false,
    }
}

/// A path in `work_dir` that will become `path`: the same file name, in a folder of its own, so a
/// tool that reads the format from the extension (ffmpeg) still sees it. Creates the folder.
pub fn stage_output(work_dir: &Path, path: &Path) -> anyhow::Result<PathBuf> {
    let name = path
        .file_name()
        .ok_or_else(|| anyhow::anyhow!("stage_output: {} has no file name", path.display()))?;
    let stage = work_dir.join("stage");
    for n in 0.. {
        let folder = stage.join(n.to_string());
        if folder.exists() {
            continue;
        }
        std::fs::create_dir_all(&folder)?;
        return Ok(folder.join(name));
    }
    unreachable!("an unbounded range always yields a free folder")
}

/// Check a staged file (it exists and is not empty), then move it to `path`. Returns its size.
///
/// `path` must be inside `sandbox` (when there is one) and never inside `work_dir`: a final
/// output in the scratch would be deleted with it. On a failed check the staged file is removed
/// and the user's folder is left as it was. An existing `path` is replaced: the overwrite gate
/// has already asked before anything ran.
pub fn commit_output(
    staged: &Path,
    path: &Path,
    sandbox: Option<&Path>,
    work_dir: &Path,
) -> anyhow::Result<u64> {
    let final_path = match sandbox {
        Some(sb) => resolve_real(path, sb),
        None => resolve_real(path, &cwd()),
    };
    if within(&final_path, Some(work_dir)) {
        anyhow::bail!(
            "commit_output: a final output cannot go into the run scratch: {}",
            final_path.display()
        );
    }
    if let Some(sb) = sandbox {
        assert_in_sandbox(&final_path, sb)?;
    }
    let size = std::fs::metadata(staged).map(|m| m.len()).unwrap_or(0);
    if size == 0 {
        let what = if staged.is_file() {
            "an empty file"
        } else {
            "no file"
        };
        let _ = std::fs::remove_file(staged);
        let name = final_path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        anyhow::bail!("The step produced {what} for {name}; nothing was written.");
    }
    if let Some(parent) = final_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    // `rename` cannot cross volumes, and the scratch lives in the OS temp folder, which may be on
    // another drive than the user's folder: copy, then remove.
    if std::fs::rename(staged, &final_path).is_err() {
        std::fs::copy(staged, &final_path)?;
        std::fs::remove_file(staged)?;
    }
    Ok(size)
}

/// Make the folder `path` will be written into: its RESOLVED parent, never its spelling.
///
/// `../escaped/../sb/out.mp4` resolves inside the sandbox, but creating its unnormalised parent
/// walks through `../escaped` and creates it on POSIX. `path` must be inside the sandbox (when
/// there is one) or the run scratch. Returns the resolved path.
pub fn ensure_parent(
    path: &Path,
    sandbox: Option<&Path>,
    work_dir: Option<&Path>,
) -> anyhow::Result<PathBuf> {
    let resolved = match sandbox {
        Some(sb) => resolve_real(path, sb),
        None => resolve_real(path, &cwd()),
    };
    if !within(&resolved, work_dir) {
        if let Some(sb) = sandbox {
            assert_in_sandbox(&resolved, sb)?;
        }
    }
    if let Some(parent) = resolved.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(resolved)
}

/// The rule [`free_output_path`] follows. The calling skill chooses it: ffmpeg's collision repair
/// is `_converted`, `_converted_2`, … (the default); documents' default output is the name itself
/// when free, then `-1`, `-2`, ….
#[derive(Debug, Clone)]
pub struct FreeName<'a> {
    /// Inserted after the stem for the first candidate.
    pub suffix: &'a str,
    /// Between the suffixed stem and the number.
    pub separator: &'a str,
    /// The first number tried.
    pub first_number: u32,
    /// Return `path` itself when it is free.
    pub keep_requested: bool,
}

impl Default for FreeName<'_> {
    fn default() -> Self {
        Self {
            suffix: "",
            separator: "_",
            first_number: 2,
            keep_requested: false,
        }
    }
}

/// The first free name for `path`: neither in `taken` nor on disk. Port of Python
/// `free_output_path` (`toolkits/fs/python/write.py`).
pub fn free_output_path(path: &Path, taken: &[PathBuf], rule: &FreeName) -> PathBuf {
    let free = |candidate: &Path| !taken.iter().any(|t| t == candidate) && !candidate.exists();
    let stem = path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let ext = path
        .extension()
        .map(|e| format!(".{}", e.to_string_lossy()))
        .unwrap_or_default();
    let base = format!("{stem}{}", rule.suffix);
    let first = if rule.keep_requested {
        path.to_path_buf()
    } else {
        path.with_file_name(format!("{base}{ext}"))
    };
    if free(&first) {
        return first;
    }
    (rule.first_number..)
        .map(|n| path.with_file_name(format!("{base}{}{n}{ext}", rule.separator)))
        .find(|c| free(c))
        .expect("an unbounded range always yields a free name")
}

/// `path`, or where it would sit in the user's folder (`home`) if it is in the run scratch. Port
/// of Python `user_side_path` (`toolkits/fs/python/write.py`). Routing keeps an intermediate's
/// place relative to the user's folder (`docs/a.pdf` -> `<work>/docs/a.pdf`), so the mapping back
/// is exact; a skill anchors a default output on it, so nothing derived lands in the scratch.
pub fn user_side_path(path: &Path, work_dir: Option<&Path>, home: Option<&Path>) -> PathBuf {
    let (Some(work), Some(home)) = (work_dir, home) else {
        return path.to_path_buf();
    };
    if !within(path, Some(work)) {
        return path.to_path_buf();
    }
    match resolve_real(path, &cwd()).strip_prefix(resolve_real(work, &cwd())) {
        Ok(rel) => home.join(rel),
        Err(_) => path.to_path_buf(),
    }
}

/// Resolve input paths (files, folders, globs) inside the sandbox: the fs toolkit's
/// `resolve_inputs`, port of `toolkits/fs/python/resolve_inputs.py`.
///
/// A folder or a glob comes back in natural order (`clip1`, `clip2`, `clip10`), the order a batch
/// runs its files in, so a merge and a batch agree; the entries keep their own order
/// (`contracts/parity/batch_cases.json`). `extensions` keeps only those files; `recursive`
/// descends into a folder's subfolders. `exclude` drops a folder's or a glob's match that is one
/// of the given paths (a join's own output); a path named on its own is kept, and a path that
/// does not exist is returned as it is, for the probe to report. Relative paths resolve against
/// `base`; each must stay inside `sandbox` unless it is in the run scratch (`work_dir`).
pub fn resolve_inputs(
    paths: &[String],
    base: &Path,
    sandbox: Option<&Path>,
    work_dir: Option<&Path>,
    extensions: &[String],
    recursive: bool,
    exclude: &[String],
) -> anyhow::Result<Vec<PathBuf>> {
    let wanted: Vec<String> = extensions
        .iter()
        .map(|e| e.trim_start_matches('.').to_lowercase())
        .collect();
    let keep = |p: &Path| {
        wanted.is_empty()
            || p.extension()
                .and_then(|x| x.to_str())
                .is_some_and(|x| wanted.contains(&x.to_lowercase()))
    };
    let excluded: Vec<PathBuf> = exclude
        .iter()
        .map(|e| resolve_real(Path::new(e), base))
        .collect();
    let mut resolved = Vec::new();
    for raw in paths {
        let p = resolve_real(Path::new(raw), base);
        if let (Some(sandbox), false) = (sandbox, within(&p, work_dir)) {
            assert_in_sandbox(&p, sandbox)?;
        }
        let globbed = !p.exists() && raw.chars().any(|c| matches!(c, '*' | '?' | '['));
        if p.is_dir() || globbed {
            // A folder, or a glob in one: the one listing core's batch uses too.
            let listed = if p.is_dir() {
                knaif_core::batch_chain::list_files(&p, "*", recursive)
            } else {
                let (Some(parent), Some(pattern)) = (p.parent(), p.file_name()) else {
                    continue;
                };
                knaif_core::batch_chain::list_files(parent, &pattern.to_string_lossy(), false)
            };
            for (_, child) in listed {
                if !excluded.contains(&child) && keep(&child) {
                    resolved.push(child);
                }
            }
        } else if p.exists() {
            resolved.push(p);
        } else {
            resolved.push(p); // left to the probe to report missing
        }
    }
    Ok(resolved)
}
