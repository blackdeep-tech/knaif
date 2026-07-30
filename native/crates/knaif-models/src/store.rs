//! `ModelStore` — the shared `~/.knaif/models` store and its lifecycle operations.
//!
//! No inference dependencies, so a model-management UI can embed this without linking
//! llama.cpp. Downloads go through an injectable [`Fetcher`] so the verify + atomic-install
//! logic is testable offline; the real HTTP fetcher lands with the llama.cpp spike.

use std::collections::HashSet;
use std::io::Read;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::manifest::{is_real, Manifest};

/// Resolve the shared model store directory.
///
/// `$KNAIF_MODELS_DIR` wins; otherwise `~/.knaif/models` (uniform across platforms, like
/// `~/.aws` / `~/.ssh`). The same resolver is used by the CLI, the installer, and any future UI,
/// so every surface sees one store.
pub fn store_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("KNAIF_MODELS_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    default_store_dir()
}

/// Resolve the loadable-backend directory (`ggml-*` libs the runtime `dlopen`s).
///
/// `$KNAIF_BACKENDS_DIR` wins; otherwise `~/.knaif/backends` — a sibling of [`store_dir`], same
/// `~/.knaif` root and same reasoning (per-user, machine-specific, never roamed). Deliberately
/// **outside** the install dir: it's where the opt-in CUDA payload lands, so `backend install`
/// needs no elevation and still works when the install itself is read-only (AppImage mount).
pub fn backends_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("KNAIF_BACKENDS_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    home().join(".knaif").join("backends")
}

fn home() -> PathBuf {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

/// The default model store: a single uniform per-user directory on every platform —
/// `~/.knaif/models` (`%USERPROFILE%\.knaif\models` on Windows). Chosen for simplicity and
/// consistency. [`home`] is the profile root, so on Windows this is deliberately **not** the
/// Roaming profile — multi-GB GGUFs are machine-specific and must never sync across it.
fn default_store_dir() -> PathBuf {
    home().join(".knaif").join("models")
}

/// Progress callback for a streaming download: `(downloaded_bytes, total_bytes)`, where `total`
/// is the server-reported content length when known. Called repeatedly as chunks arrive.
pub type ProgressFn<'a> = dyn FnMut(u64, Option<u64>) + 'a;

/// Downloads a model URL into `dest`, reporting cumulative progress as bytes land. The
/// implementation owns file creation and the writes, so it is free to fetch byte ranges
/// concurrently (seeking to each chunk's offset) and to resume a partial `dest` left by an
/// interrupted run — neither of which a single sequential `Write` sink can express. The caller
/// hashes and renames the finished file. Tests use an on-disk fake.
pub trait Fetcher {
    fn fetch_to_file(
        &self,
        url: &str,
        dest: &Path,
        progress: &mut ProgressFn<'_>,
    ) -> anyhow::Result<()>;
}

/// One row for `knaif models list`.
#[derive(Debug, Clone)]
pub struct ModelEntry {
    pub name: String,
    pub installed: bool,
    pub path: PathBuf,
    pub in_manifest: bool,
    pub size_bytes: Option<u64>,
    /// Skills this model was fine-tuned over (manifest `skills:` tags; empty for orphans).
    pub skills: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VerifyOutcome {
    Ok,
    Mismatch { expected: String, actual: String },
    NotInstalled,
    NoChecksum,
}

pub struct ModelStore {
    dir: PathBuf,
    manifest: Manifest,
}

impl ModelStore {
    /// Open the store at the resolved [`store_dir`] with the given manifest file.
    pub fn open(manifest_path: &Path) -> anyhow::Result<Self> {
        Ok(Self {
            dir: store_dir(),
            manifest: Manifest::load(manifest_path)?,
        })
    }

    /// Construct against an explicit directory (tests, or a caller with its own resolution).
    pub fn with_dir(dir: PathBuf, manifest: Manifest) -> Self {
        Self { dir, manifest }
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }

    fn manifest_path(&self, name: &str) -> Option<PathBuf> {
        self.manifest.get(name).map(|m| self.dir.join(&m.file))
    }

    pub fn is_installed(&self, name: &str) -> bool {
        self.manifest_path(name).is_some_and(|p| p.is_file())
    }

    /// Every manifest model (with installed status) plus any orphan `.gguf` in the store.
    pub fn list(&self) -> Vec<ModelEntry> {
        let mut out: Vec<ModelEntry> = self
            .manifest
            .models
            .iter()
            .map(|(name, spec)| {
                let path = self.dir.join(&spec.file);
                ModelEntry {
                    name: name.clone(),
                    installed: path.is_file(),
                    path,
                    in_manifest: true,
                    size_bytes: spec.size_bytes,
                    skills: spec.skills.clone(),
                }
            })
            .collect();

        let known: HashSet<&str> = self
            .manifest
            .models
            .values()
            .map(|m| m.file.as_str())
            .collect();
        if let Ok(entries) = std::fs::read_dir(&self.dir) {
            for e in entries.flatten() {
                let p = e.path();
                let is_gguf = p.extension().and_then(|x| x.to_str()) == Some("gguf");
                let fname = p.file_name().and_then(|x| x.to_str()).unwrap_or("");
                if is_gguf && !known.contains(fname) {
                    out.push(ModelEntry {
                        name: fname.to_string(),
                        installed: true,
                        path: p,
                        in_manifest: false,
                        size_bytes: None,
                        skills: Vec::new(),
                    });
                }
            }
        }
        out.sort_by(|a, b| a.name.cmp(&b.name));
        out
    }

    /// SHA-256 an installed model against the manifest checksum.
    pub fn verify(&self, name: &str) -> anyhow::Result<VerifyOutcome> {
        let spec = self
            .manifest
            .get(name)
            .ok_or_else(|| anyhow::anyhow!("unknown model {name:?}"))?;
        let path = self.dir.join(&spec.file);
        if !path.is_file() {
            return Ok(VerifyOutcome::NotInstalled);
        }
        let Some(expected) = is_real(spec.sha256.as_deref()) else {
            return Ok(VerifyOutcome::NoChecksum);
        };
        let actual = sha256_file(&path)?;
        if actual.eq_ignore_ascii_case(expected) {
            Ok(VerifyOutcome::Ok)
        } else {
            Ok(VerifyOutcome::Mismatch {
                expected: expected.to_string(),
                actual,
            })
        }
    }

    /// Remove an installed model (by manifest name or raw filename). Returns whether a file
    /// was removed.
    pub fn delete(&self, name: &str) -> anyhow::Result<bool> {
        let path = self
            .manifest_path(name)
            .unwrap_or_else(|| self.dir.join(name));
        if path.is_file() {
            std::fs::remove_file(&path)?;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    /// Remove every `.gguf` in the store. Returns the count removed.
    pub fn delete_all(&self) -> anyhow::Result<usize> {
        let mut n = 0;
        if let Ok(entries) = std::fs::read_dir(&self.dir) {
            for e in entries.flatten() {
                let p = e.path();
                if p.extension().and_then(|x| x.to_str()) == Some("gguf") {
                    std::fs::remove_file(&p)?;
                    n += 1;
                }
            }
        }
        Ok(n)
    }

    /// Download a model, verify its checksum, and install atomically (temp file → rename).
    pub fn pull(&self, name: &str, fetcher: &dyn Fetcher) -> anyhow::Result<PathBuf> {
        self.pull_with_progress(name, fetcher, &mut |_, _| {})
    }

    /// Like [`pull`](Self::pull) but reports download progress via `progress`. Bytes land in a
    /// `.part` file (which an interrupted pull leaves behind for the fetcher to resume), then the
    /// assembled file is hashed and checksum-verified before the atomic rename into place.
    pub fn pull_with_progress(
        &self,
        name: &str,
        fetcher: &dyn Fetcher,
        progress: &mut ProgressFn<'_>,
    ) -> anyhow::Result<PathBuf> {
        let spec = self
            .manifest
            .get(name)
            .ok_or_else(|| anyhow::anyhow!("unknown model {name:?}"))?;
        let url = is_real(spec.url.as_deref())
            .ok_or_else(|| anyhow::anyhow!("model {name:?} has no download URL in the manifest"))?;

        std::fs::create_dir_all(&self.dir)?;
        let final_path = self.dir.join(&spec.file);
        let tmp = self.dir.join(format!("{}.part", spec.file));

        // The fetcher owns the writes so it can download chunks in parallel and resume a partial
        // `.part`. On error we deliberately keep `.part` (+ its resume sidecar) so the next pull
        // continues instead of restarting.
        fetcher.fetch_to_file(url, &tmp, progress)?;

        // The file is assembled out of order (and possibly across runs), so it is hashed here in
        // one pass rather than on the write path.
        if let Some(expected) = is_real(spec.sha256.as_deref()) {
            let actual = sha256_file(&tmp)?;
            if !actual.eq_ignore_ascii_case(expected) {
                // Corrupt bytes: drop both the file and any resume state so a retry starts clean.
                let _ = std::fs::remove_file(&tmp);
                let _ = std::fs::remove_file(crate::fetcher::resume_sidecar_path(&tmp));
                anyhow::bail!("checksum mismatch for {name:?}: expected {expected}, got {actual}");
            }
        }

        std::fs::rename(&tmp, &final_path)?;
        Ok(final_path)
    }

    /// Re-pull installed models whose bytes no longer match the manifest checksum. Returns the
    /// names updated.
    pub fn update(&self, fetcher: &dyn Fetcher) -> anyhow::Result<Vec<String>> {
        let mut updated = Vec::new();
        for name in self.manifest.models.keys() {
            if matches!(self.verify(name)?, VerifyOutcome::Mismatch { .. }) {
                self.pull(name, fetcher)?;
                updated.push(name.clone());
            }
        }
        Ok(updated)
    }

    /// Choose which model a run uses: explicit CLI value → config → skill/surface
    /// recommendation → manifest default. A raw `.gguf` path is passed through as-is.
    pub fn resolve_model(
        &self,
        cli: Option<&str>,
        config: Option<&str>,
        recommended: Option<&str>,
    ) -> Option<String> {
        cli.or(config)
            .or(recommended)
            .map(str::to_string)
            .or_else(|| self.manifest.default_model().map(str::to_string))
    }

    /// Resolve a model identifier (a raw path or a manifest name) to an installed file.
    pub fn path_for(&self, id: &str) -> Option<PathBuf> {
        let raw = Path::new(id);
        if raw.is_file() {
            return Some(raw.to_path_buf());
        }
        self.manifest_path(id).filter(|p| p.is_file())
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
fn sha256_bytes(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    hex(&h.finalize())
}

pub(crate) fn sha256_file(path: &Path) -> anyhow::Result<String> {
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = [0u8; 8192];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(hex(&h.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::manifest::Manifest;

    const MANIFEST: &str = r#"
models:
  demo:
    file: demo.gguf
    url: "https://example.test/demo.gguf"
    sha256: "PLACEHOLDER"
    skills: [alpha, beta]
  no-url:
    file: nourl.gguf
recommendations:
  cli: demo
  mobile: demo
  default: demo
"#;

    struct FakeFetcher {
        bytes: Vec<u8>,
    }
    impl Fetcher for FakeFetcher {
        fn fetch_to_file(
            &self,
            _url: &str,
            dest: &Path,
            progress: &mut ProgressFn<'_>,
        ) -> anyhow::Result<()> {
            // Write in two chunks so progress fires more than once, like a real download.
            let total = self.bytes.len() as u64;
            let mid = self.bytes.len() / 2;
            let mut file = std::fs::File::create(dest)?;
            let mut done = 0u64;
            for chunk in [&self.bytes[..mid], &self.bytes[mid..]] {
                std::io::Write::write_all(&mut file, chunk)?;
                done += chunk.len() as u64;
                progress(done, Some(total));
            }
            Ok(())
        }
    }

    fn tmpdir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("knaif_store_{}_{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn store_with(dir: PathBuf, sha: &str) -> ModelStore {
        let manifest_text = MANIFEST.replace("PLACEHOLDER", sha);
        ModelStore::with_dir(dir, Manifest::from_yaml(&manifest_text).unwrap())
    }

    #[test]
    fn store_dir_honors_env_override() {
        std::env::set_var("KNAIF_MODELS_DIR", "/tmp/custom-knaif-models");
        assert_eq!(store_dir(), PathBuf::from("/tmp/custom-knaif-models"));
        std::env::remove_var("KNAIF_MODELS_DIR");
    }

    // The default store is a single uniform per-user dir on every platform — `~/.knaif/models`.
    // `home()` is the profile root, so on Windows this is NOT the Roaming profile: multi-GB GGUFs
    // never sync.
    #[test]
    fn default_store_dir_is_dot_knaif_under_home_never_roaming() {
        let dir = default_store_dir();
        assert!(
            dir.ends_with(PathBuf::from(".knaif").join("models")),
            "expected ~/.knaif/models, got {}",
            dir.display()
        );
        assert!(
            !dir.to_string_lossy().contains("Roaming"),
            "must not resolve into the Windows Roaming profile: {}",
            dir.display()
        );
    }

    #[test]
    fn pull_verifies_checksum_and_installs_atomically() {
        let dir = tmpdir("pull");
        let bytes = b"the-model-bytes".to_vec();
        let sha = sha256_bytes(&bytes);
        let store = store_with(dir.clone(), &sha);

        let path = store.pull("demo", &FakeFetcher { bytes }).unwrap();
        assert!(path.is_file());
        assert!(store.is_installed("demo"));
        assert_eq!(store.verify("demo").unwrap(), VerifyOutcome::Ok);
        // no leftover temp file
        assert!(!dir.join("demo.gguf.part").exists());
    }

    #[test]
    fn pull_reports_progress_and_leaves_no_partial() {
        let dir = tmpdir("progress");
        let bytes = b"twelve-byte-model-payload".to_vec();
        let store = store_with(dir.clone(), &sha256_bytes(&bytes));

        let mut samples: Vec<(u64, Option<u64>)> = Vec::new();
        let path = store
            .pull_with_progress(
                "demo",
                &FakeFetcher {
                    bytes: bytes.clone(),
                },
                &mut |done, total| {
                    samples.push((done, total));
                },
            )
            .unwrap();

        assert!(path.is_file());
        assert!(
            samples.len() >= 2,
            "progress should fire per chunk: {samples:?}"
        );
        // Monotonic, and the final callback accounts for every byte with the known total.
        assert_eq!(samples.last().unwrap().0, bytes.len() as u64);
        assert_eq!(samples.last().unwrap().1, Some(bytes.len() as u64));
        assert!(!dir.join("demo.gguf.part").exists());
    }

    #[test]
    fn pull_removes_partial_on_checksum_mismatch() {
        let dir = tmpdir("mismatch_partial");
        let store = store_with(dir.clone(), &sha256_bytes(b"expected"));
        let _ = store
            .pull(
                "demo",
                &FakeFetcher {
                    bytes: b"different".to_vec(),
                },
            )
            .unwrap_err();
        // A failed pull must not leave a stale `.part` (or a bogus final file) behind.
        assert!(!dir.join("demo.gguf.part").exists());
        assert!(!dir.join("demo.gguf").exists());
    }

    #[test]
    fn pull_rejects_checksum_mismatch() {
        let dir = tmpdir("mismatch");
        let store = store_with(dir, &sha256_bytes(b"expected"));
        let err = store
            .pull(
                "demo",
                &FakeFetcher {
                    bytes: b"different".to_vec(),
                },
            )
            .unwrap_err();
        assert!(err.to_string().contains("checksum mismatch"));
    }

    #[test]
    fn pull_without_url_errors() {
        let dir = tmpdir("nourl");
        let store = store_with(dir, "x");
        let err = store
            .pull("no-url", &FakeFetcher { bytes: vec![] })
            .unwrap_err();
        assert!(err.to_string().contains("no download URL"));
    }

    #[test]
    fn list_marks_installed_and_delete_all_empties() {
        let dir = tmpdir("list");
        let bytes = b"m".to_vec();
        let store = store_with(dir, &sha256_bytes(&bytes));
        assert!(store.list().iter().all(|e| !e.installed));

        store.pull("demo", &FakeFetcher { bytes }).unwrap();
        let listed = store.list();
        assert!(listed.iter().any(|e| e.name == "demo" && e.installed));
        assert!(listed.iter().any(|e| e.name == "no-url" && !e.installed));
        // manifest skill tags are carried onto the listed entry
        let demo = listed.iter().find(|e| e.name == "demo").unwrap();
        assert_eq!(demo.skills, ["alpha", "beta"]);

        assert_eq!(store.delete_all().unwrap(), 1);
        assert!(store.list().iter().all(|e| !e.installed));
    }

    #[test]
    fn resolve_model_precedence() {
        let store = store_with(tmpdir("resolve"), "x");
        assert_eq!(
            store
                .resolve_model(Some("cli"), Some("cfg"), Some("rec"))
                .as_deref(),
            Some("cli")
        );
        assert_eq!(
            store
                .resolve_model(None, Some("cfg"), Some("rec"))
                .as_deref(),
            Some("cfg")
        );
        assert_eq!(
            store.resolve_model(None, None, Some("rec")).as_deref(),
            Some("rec")
        );
        assert_eq!(
            store.resolve_model(None, None, None).as_deref(),
            Some("demo")
        ); // manifest default
    }
}
