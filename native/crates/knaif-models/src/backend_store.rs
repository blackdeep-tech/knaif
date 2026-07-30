//! `BackendStore` — the `~/.knaif/backends` payload store and its lifecycle operations.
//!
//! Parallels [`ModelStore`](crate::ModelStore) and reuses its [`Fetcher`] trait and its
//! verify-then-install discipline, but the two stores are not the same shape, because a backend
//! payload is not a model (see [`crate::backend_manifest`]). Two differences drive the code here:
//!
//! **Installing is one operation over many files.** `ModelStore::pull` is `.part` -> hash ->
//! `rename` of a single path, which is atomic per file. Doing that four times in a row is four
//! atomic operations, not one, so an interrupted install would leave a directory holding a mix of
//! two payloads — the failure that reads as a driver bug. So: **stage all, verify all, then swap**.
//!
//! **A stale payload has to be refused at LOAD time, and install-time pinning cannot do it.**
//! `~/.knaif/backends` sits outside the install dir by design (that is what makes `backend install`
//! elevation-free and keeps it working when the install is read-only), it survives an app upgrade,
//! and the loader scans it *first*. So an upgraded `knaif` would reach the previous release's
//! `ggml-cuda` before any `backend install` could run. The install writes a **receipt** stamped with
//! the knaif release that produced it, and [`backend_dir_state`] lets the loader refuse the
//! directory when that stamp does not match the running binary.
//!
//! A directory with **no** receipt is deliberately still loaded: that is the documented manual
//! fallback (drop the payload in by hand), which is how `backend install` itself gets debugged.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::backend_manifest::{BackendManifest, BackendSpec, PublishStatus};
use crate::store::{backends_dir, sha256_file, Fetcher, VerifyOutcome};

/// Receipt filename inside the backends directory. Leading dot, and not a `ggml-*` name, so
/// llama.cpp's `load_backends_from_path` never looks at it.
const RECEIPT: &str = ".knaif-backends.yaml";

/// Progress for a multi-file payload install: which file, how far through the set, and the byte
/// counts for the file currently downloading.
#[derive(Debug, Clone, Copy)]
pub struct BackendProgress<'a> {
    pub file: &'a str,
    /// 1-based position of `file` in the payload.
    pub index: usize,
    pub total_files: usize,
    pub done_bytes: u64,
    /// Server-reported total for this file, when known.
    pub total_bytes: Option<u64>,
}

pub type BackendProgressFn<'a> = dyn FnMut(BackendProgress<'_>) + 'a;

/// One row for `knaif backend list`.
#[derive(Debug, Clone)]
pub struct BackendEntry {
    pub name: String,
    pub description: Option<String>,
    pub status: PublishStatus,
    /// Whether the manifest carries a file list for the platform this binary runs on.
    pub platform_supported: bool,
    pub state: BackendState,
    pub files: Vec<String>,
    /// Sum of the manifest's declared sizes, when every file declares one.
    pub total_bytes: Option<u64>,
}

/// Whether a payload is installed here, and whether it is usable by *this* binary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendState {
    NotInstalled,
    /// Installed by this knaif release.
    Installed,
    /// Installed by a different release. The ABI-coupled lib does not match this binary, so the
    /// loader refuses the directory until `backend install` re-runs.
    Stale {
        installed_by: String,
    },
    /// A previous install did not finish. The directory may hold a mix of two payloads.
    Interrupted,
}

/// One payload's entry in the on-disk receipt.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct ReceiptEntry {
    /// The knaif release whose `backend install` wrote these files.
    knaif_version: String,
    /// `installing` until the swap completes, then `complete`. Written before the files move and
    /// rewritten after, so an interrupted install is detectable rather than silent.
    state: String,
    files: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct Receipt {
    #[serde(default)]
    installs: BTreeMap<String, ReceiptEntry>,
}

impl Receipt {
    fn load(dir: &Path) -> Self {
        std::fs::read_to_string(dir.join(RECEIPT))
            .ok()
            .and_then(|t| serde_yaml::from_str(&t).ok())
            .unwrap_or_default()
    }

    /// Write atomically (temp + rename) so a crash mid-write cannot leave a torn receipt that a
    /// later run would misread as "no receipt" and therefore load blindly.
    fn save(&self, dir: &Path) -> anyhow::Result<()> {
        std::fs::create_dir_all(dir)?;
        let text = serde_yaml::to_string(self)?;
        let tmp = dir.join(format!("{RECEIPT}.tmp"));
        std::fs::write(&tmp, text)?;
        std::fs::rename(&tmp, dir.join(RECEIPT))?;
        Ok(())
    }
}

/// What the loader must decide before scanning the backends directory.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendDirState {
    /// No receipt: empty, or a payload placed by hand. That manual route is documented and
    /// supported (it is how `backend install` is debugged), so it loads.
    Unmanaged,
    /// Every managed payload was installed by this knaif release.
    Current,
    /// At least one payload is stale or half-installed. `reason` is user-facing.
    Refuse { reason: String },
}

/// Decide whether `dir` is safe for this build to load backends from.
///
/// The check is deliberately **whole-directory**: payload files are flat in one directory (that is
/// what `load_backends_from_path` scans), so there is no way to skip one payload and keep another.
/// With a single payload today that costs nothing.
pub fn backend_dir_state(dir: &Path, knaif_version: &str) -> BackendDirState {
    let receipt = Receipt::load(dir);
    if receipt.installs.is_empty() {
        return BackendDirState::Unmanaged;
    }
    for (name, entry) in &receipt.installs {
        if entry.state != "complete" {
            return BackendDirState::Refuse {
                reason: format!(
                    "the {name} backend payload in {} was not fully installed — \
                     re-run `knaif backend install {name}`",
                    dir.display()
                ),
            };
        }
        if entry.knaif_version != knaif_version {
            return BackendDirState::Refuse {
                reason: format!(
                    "the {name} backend payload in {} was installed by knaif {} but this is knaif \
                     {knaif_version} — the backend is ABI-coupled to the binary, so it is being \
                     ignored. Run `knaif backend install {name}` to update it.",
                    dir.display(),
                    entry.knaif_version
                ),
            };
        }
    }
    BackendDirState::Current
}

pub struct BackendStore {
    dir: PathBuf,
    manifest: BackendManifest,
    platform: String,
    /// The running binary's release. Normally the manifest's `knaif_version` (the manifest ships
    /// inside the artifact, so they agree by construction); overridable for tests.
    knaif_version: String,
}

impl BackendStore {
    /// Open the store at the resolved [`backends_dir`] with the given manifest file.
    pub fn open(manifest_path: &Path) -> anyhow::Result<Self> {
        let manifest = BackendManifest::load(manifest_path)?;
        Ok(Self::with_dir(backends_dir(), manifest))
    }

    /// Construct against an explicit directory (tests, or a caller with its own resolution).
    pub fn with_dir(dir: PathBuf, manifest: BackendManifest) -> Self {
        let knaif_version = manifest
            .knaif_version
            .clone()
            .unwrap_or_else(|| env!("CARGO_PKG_VERSION").to_string());
        Self {
            dir,
            manifest,
            platform: crate::backend_manifest::current_platform(),
            knaif_version,
        }
    }

    /// Override the platform key this store resolves against (tests; also lets a caller inspect a
    /// payload for another OS).
    pub fn with_platform(mut self, platform: impl Into<String>) -> Self {
        self.platform = platform.into();
        self
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn manifest(&self) -> &BackendManifest {
        &self.manifest
    }

    pub fn platform(&self) -> &str {
        &self.platform
    }

    pub fn knaif_version(&self) -> &str {
        &self.knaif_version
    }

    fn spec(&self, name: &str) -> anyhow::Result<&BackendSpec> {
        self.manifest.get(name).ok_or_else(|| {
            let known: Vec<&str> = self.manifest.backends.keys().map(String::as_str).collect();
            anyhow::anyhow!("unknown backend {name:?}. Available: {}", known.join(", "))
        })
    }

    /// Every manifest backend with its state on this machine.
    pub fn list(&self) -> Vec<BackendEntry> {
        self.manifest
            .backends
            .iter()
            .map(|(name, spec)| {
                let platform = spec.platform(&self.platform);
                let files: Vec<String> = platform
                    .map(|p| p.files.iter().map(|f| f.name.clone()).collect())
                    .unwrap_or_default();
                let total_bytes = platform.and_then(|p| {
                    p.files
                        .iter()
                        .map(|f| f.size_bytes)
                        .sum::<Option<u64>>()
                        .filter(|_| !p.files.is_empty())
                });
                BackendEntry {
                    name: name.clone(),
                    description: spec.description.clone(),
                    status: spec.status,
                    platform_supported: platform.is_some(),
                    state: self.state(name),
                    files,
                    total_bytes,
                }
            })
            .collect()
    }

    /// Whether `name` is installed here, and whether this binary may use it.
    pub fn state(&self, name: &str) -> BackendState {
        let receipt = Receipt::load(&self.dir);
        let Some(entry) = receipt.installs.get(name) else {
            return BackendState::NotInstalled;
        };
        if entry.state != "complete" {
            return BackendState::Interrupted;
        }
        if entry.knaif_version != self.knaif_version {
            return BackendState::Stale {
                installed_by: entry.knaif_version.clone(),
            };
        }
        BackendState::Installed
    }

    /// SHA-256 every installed file of `name` against the manifest. Reports the first problem
    /// found, so a caller gets one actionable answer rather than a list.
    pub fn verify(&self, name: &str) -> anyhow::Result<VerifyOutcome> {
        let spec = self.spec(name)?;
        let Some(platform) = spec.platform(&self.platform) else {
            return Ok(VerifyOutcome::NotInstalled);
        };
        if matches!(self.state(name), BackendState::NotInstalled) {
            return Ok(VerifyOutcome::NotInstalled);
        }
        let mut checked = 0usize;
        for file in &platform.files {
            let path = self.dir.join(&file.name);
            if !path.is_file() {
                return Ok(VerifyOutcome::NotInstalled);
            }
            let Some(expected) = file.real_sha256() else {
                continue;
            };
            let actual = sha256_file(&path)?;
            if !actual.eq_ignore_ascii_case(expected) {
                return Ok(VerifyOutcome::Mismatch {
                    expected: format!("{} {expected}", file.name),
                    actual,
                });
            }
            checked += 1;
        }
        if checked == 0 {
            return Ok(VerifyOutcome::NoChecksum);
        }
        Ok(VerifyOutcome::Ok)
    }

    /// Download every file of `name`, verify all of them, then swap them into the backends
    /// directory as one step. Returns the directory the payload landed in.
    ///
    /// Refuses an unpublished entry, an unsupported platform, and any file missing a real URL or
    /// checksum — a backend is not a model, so an unchecksummed install is never allowed.
    pub fn install(&self, name: &str, fetcher: &dyn Fetcher) -> anyhow::Result<PathBuf> {
        self.install_with_progress(name, fetcher, &mut |_| {})
    }

    /// Like [`install`](Self::install) but reports per-file download progress.
    pub fn install_with_progress(
        &self,
        name: &str,
        fetcher: &dyn Fetcher,
        progress: &mut BackendProgressFn<'_>,
    ) -> anyhow::Result<PathBuf> {
        let spec = self.spec(name)?;

        if spec.status != PublishStatus::Published {
            anyhow::bail!(
                "the {name} backend payload is not published yet, so there is nothing to download. \
                 (Build it locally and copy it into {} — see docs/NATIVE.md.)",
                self.dir.display()
            );
        }
        let platform = spec.platform(&self.platform).ok_or_else(|| {
            let targets: Vec<&str> = spec.platforms.keys().map(String::as_str).collect();
            anyhow::anyhow!(
                "the {name} backend payload has no build for {} (available: {})",
                self.platform,
                targets.join(", ")
            )
        })?;
        if platform.files.is_empty() {
            anyhow::bail!("the {name} payload declares no files for {}", self.platform);
        }
        // Check the whole set up front: discovering an unpublished file after downloading 500 MB of
        // its siblings is a waste, and a partial payload must never reach the directory.
        for file in &platform.files {
            if file.real_url().is_none() {
                anyhow::bail!(
                    "the {name} payload lists {} with no download URL — the manifest says the \
                     payload is published but its assets are not",
                    file.name
                );
            }
            if file.real_sha256().is_none() {
                anyhow::bail!(
                    "the {name} payload lists {} with no sha256. A backend whose bytes are \
                     unverified can fail in ways that look like a driver bug, so this is refused \
                     rather than downloaded.",
                    file.name
                );
            }
        }

        let stage = self.dir.join(format!(".stage-{name}"));
        let _ = std::fs::remove_dir_all(&stage);
        std::fs::create_dir_all(&stage)?;

        // --- stage all + verify all -------------------------------------------------------------
        let total_files = platform.files.len();
        for (i, file) in platform.files.iter().enumerate() {
            let url = file.real_url().expect("checked above");
            let expected = file.real_sha256().expect("checked above");
            let part = stage.join(format!("{}.part", file.name));
            let staged = stage.join(&file.name);

            fetcher.fetch_to_file(url, &part, &mut |done, total| {
                progress(BackendProgress {
                    file: &file.name,
                    index: i + 1,
                    total_files,
                    done_bytes: done,
                    total_bytes: total,
                });
            })?;

            let actual = sha256_file(&part)?;
            if !actual.eq_ignore_ascii_case(expected) {
                let _ = std::fs::remove_dir_all(&stage);
                anyhow::bail!(
                    "checksum mismatch for {}: expected {expected}, got {actual}",
                    file.name
                );
            }
            std::fs::rename(&part, &staged)?;
        }

        // --- swap -------------------------------------------------------------------------------
        // Everything below is the part that must not be observed half-done. The receipt is written
        // `installing` FIRST, so a failure anywhere in here leaves a directory the loader refuses
        // rather than one it silently loads a mixed payload from.
        let new_files: Vec<String> = platform.files.iter().map(|f| f.name.clone()).collect();
        let mut receipt = Receipt::load(&self.dir);
        let old_files = receipt
            .installs
            .get(name)
            .map(|e| e.files.clone())
            .unwrap_or_default();

        receipt.installs.insert(
            name.to_string(),
            ReceiptEntry {
                knaif_version: self.knaif_version.clone(),
                state: "installing".to_string(),
                files: new_files.clone(),
            },
        );
        receipt.save(&self.dir)?;

        // Drop files the previous payload had and this one does not (e.g. a renamed redist SONAME);
        // the rest are replaced in place below.
        for old in &old_files {
            if !new_files.contains(old) {
                let _ = std::fs::remove_file(self.dir.join(old));
            }
        }
        for file in &new_files {
            let from = stage.join(file);
            let to = self.dir.join(file);
            std::fs::rename(&from, &to).map_err(|e| {
                anyhow::anyhow!(
                    "installing {file} into {}: {e}\n  \
                     (If another knaif process is running, close it and re-run — a loaded backend \
                     library cannot be replaced while it is in use.)",
                    self.dir.display()
                )
            })?;
        }

        if let Some(entry) = receipt.installs.get_mut(name) {
            entry.state = "complete".to_string();
        }
        receipt.save(&self.dir)?;
        let _ = std::fs::remove_dir_all(&stage);

        Ok(self.dir.clone())
    }

    /// Remove an installed payload's files and its receipt entry. Returns whether anything was
    /// removed. Falls back to the manifest's file list when the receipt is missing, so a payload
    /// placed by hand can still be removed by name.
    pub fn remove(&self, name: &str) -> anyhow::Result<bool> {
        let mut receipt = Receipt::load(&self.dir);
        let files = match receipt.installs.remove(name) {
            Some(entry) => entry.files,
            None => self
                .manifest
                .get(name)
                .and_then(|s| s.platform(&self.platform))
                .map(|p| p.files.iter().map(|f| f.name.clone()).collect())
                .unwrap_or_default(),
        };
        let mut removed = 0usize;
        for file in &files {
            let path = self.dir.join(file);
            if path.is_file() {
                std::fs::remove_file(&path)?;
                removed += 1;
            }
        }
        let _ = std::fs::remove_dir_all(self.dir.join(format!(".stage-{name}")));
        if receipt.installs.is_empty() {
            let _ = std::fs::remove_file(self.dir.join(RECEIPT));
        } else {
            receipt.save(&self.dir)?;
        }
        Ok(removed > 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::ProgressFn;
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    fn tmpdir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "knaif_backends_{}_{}_{}",
            tag,
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn sha(bytes: &[u8]) -> String {
        use sha2::{Digest, Sha256};
        let mut h = Sha256::new();
        h.update(bytes);
        h.finalize().iter().map(|b| format!("{b:02x}")).collect()
    }

    /// Serves a fixed body per URL, and records how many fetches happened.
    struct MapFetcher {
        bodies: HashMap<String, Vec<u8>>,
    }
    impl Fetcher for MapFetcher {
        fn fetch_to_file(
            &self,
            url: &str,
            dest: &Path,
            progress: &mut ProgressFn<'_>,
        ) -> anyhow::Result<()> {
            let body = self
                .bodies
                .get(url)
                .ok_or_else(|| anyhow::anyhow!("no body for {url}"))?;
            std::fs::create_dir_all(dest.parent().unwrap())?;
            std::fs::write(dest, body)?;
            progress(body.len() as u64, Some(body.len() as u64));
            Ok(())
        }
    }

    const LIB: &[u8] = b"ggml-cuda-bytes";
    const RT: &[u8] = b"cudart-bytes";

    fn manifest_yaml(version: &str, status: &str, lib_sha: &str, rt_sha: &str) -> String {
        format!(
            r#"
knaif_version: "{version}"
backends:
  cuda:
    description: "NVIDIA CUDA offload"
    status: {status}
    requires:
      vendor: nvidia
      min_driver: 580
    platforms:
      test-x64:
        files:
          - name: libggml-cuda.so
            tag: product
            url: "https://example.test/libggml-cuda.so"
            sha256: "{lib_sha}"
            size_bytes: 15
          - name: libcudart.so.13
            tag: redist-cuda-13.3
            url: "https://example.test/libcudart.so.13"
            sha256: "{rt_sha}"
            size_bytes: 12
"#
        )
    }

    fn store(
        dir: PathBuf,
        version: &str,
        status: &str,
        lib_sha: &str,
        rt_sha: &str,
    ) -> BackendStore {
        let m =
            BackendManifest::from_yaml(&manifest_yaml(version, status, lib_sha, rt_sha)).unwrap();
        BackendStore::with_dir(dir, m).with_platform("test-x64")
    }

    fn good_store(dir: PathBuf, version: &str) -> BackendStore {
        store(dir, version, "published", &sha(LIB), &sha(RT))
    }

    fn fetcher() -> MapFetcher {
        MapFetcher {
            bodies: HashMap::from([
                (
                    "https://example.test/libggml-cuda.so".to_string(),
                    LIB.to_vec(),
                ),
                (
                    "https://example.test/libcudart.so.13".to_string(),
                    RT.to_vec(),
                ),
            ]),
        }
    }

    #[test]
    fn install_lands_every_file_and_leaves_no_staging() {
        let dir = tmpdir("install");
        let s = good_store(dir.clone(), "1.1.0");
        assert_eq!(s.state("cuda"), BackendState::NotInstalled);

        s.install("cuda", &fetcher()).unwrap();

        assert_eq!(std::fs::read(dir.join("libggml-cuda.so")).unwrap(), LIB);
        assert_eq!(std::fs::read(dir.join("libcudart.so.13")).unwrap(), RT);
        assert_eq!(s.state("cuda"), BackendState::Installed);
        assert_eq!(s.verify("cuda").unwrap(), VerifyOutcome::Ok);
        assert!(
            !dir.join(".stage-cuda").exists(),
            "staging dir must be gone"
        );
        assert!(!dir.join("libggml-cuda.so.part").exists());
    }

    #[test]
    fn progress_reports_every_file_in_order() {
        let dir = tmpdir("progress");
        let s = good_store(dir, "1.1.0");
        let mut seen: Vec<(String, usize, usize)> = Vec::new();
        s.install_with_progress("cuda", &fetcher(), &mut |p| {
            seen.push((p.file.to_string(), p.index, p.total_files));
        })
        .unwrap();
        assert_eq!(
            seen,
            vec![
                ("libggml-cuda.so".to_string(), 1, 2),
                ("libcudart.so.13".to_string(), 2, 2),
            ]
        );
    }

    // The whole reason the swap exists: one bad file must leave the directory untouched, not
    // half-populated with the payload's other files.
    #[test]
    fn a_single_checksum_mismatch_installs_nothing() {
        let dir = tmpdir("mismatch");
        let s = store(dir.clone(), "1.1.0", "published", &sha(LIB), &sha(b"wrong"));
        let err = s.install("cuda", &fetcher()).unwrap_err();
        assert!(
            err.to_string().contains("checksum mismatch"),
            "unhelpful: {err}"
        );
        assert!(
            !dir.join("libggml-cuda.so").exists(),
            "the file that DID verify must not be left behind"
        );
        assert!(!dir.join("libcudart.so.13").exists());
        assert!(!dir.join(".stage-cuda").exists());
        assert_eq!(s.state("cuda"), BackendState::NotInstalled);
    }

    #[test]
    fn unpublished_payload_is_refused_before_any_download() {
        let dir = tmpdir("unpublished");
        let s = store(dir.clone(), "1.1.0", "unpublished", &sha(LIB), &sha(RT));
        let err = s.install("cuda", &fetcher()).unwrap_err();
        assert!(
            err.to_string().contains("not published"),
            "unhelpful: {err}"
        );
        assert!(!dir.join("libggml-cuda.so").exists());
    }

    #[test]
    fn a_file_without_a_checksum_is_refused_rather_than_installed_unverified() {
        // ModelStore tolerates a pull with no checksum; a backend must not, because unverified
        // bytes fail in ways that present as a driver bug.
        let dir = tmpdir("nosha");
        let s = store(dir, "1.1.0", "published", &sha(LIB), "TODO");
        let err = s.install("cuda", &fetcher()).unwrap_err();
        assert!(err.to_string().contains("no sha256"), "unhelpful: {err}");
    }

    #[test]
    fn unsupported_platform_names_the_ones_that_exist() {
        let dir = tmpdir("platform");
        let m =
            BackendManifest::from_yaml(&manifest_yaml("1.1.0", "published", &sha(LIB), &sha(RT)))
                .unwrap();
        let s = BackendStore::with_dir(dir, m).with_platform("plan9-x64");
        let err = s.install("cuda", &fetcher()).unwrap_err();
        assert!(err.to_string().contains("test-x64"), "unhelpful: {err}");
    }

    #[test]
    fn unknown_backend_names_the_known_ones() {
        let s = good_store(tmpdir("unknown"), "1.1.0");
        let err = s.install("rocm", &fetcher()).unwrap_err();
        assert!(err.to_string().contains("cuda"), "unhelpful: {err}");
    }

    // The requirement install-time pinning could not meet: an upgraded binary must not load the
    // payload the PREVIOUS release installed.
    #[test]
    fn a_payload_from_another_release_is_stale_and_the_dir_is_refused() {
        let dir = tmpdir("stale");
        good_store(dir.clone(), "1.1.0")
            .install("cuda", &fetcher())
            .unwrap();

        // Same directory, a binary from the next release.
        let upgraded = good_store(dir.clone(), "1.2.0");
        assert_eq!(
            upgraded.state("cuda"),
            BackendState::Stale {
                installed_by: "1.1.0".to_string()
            }
        );
        match backend_dir_state(&dir, "1.2.0") {
            BackendDirState::Refuse { reason } => {
                assert!(
                    reason.contains("1.1.0") && reason.contains("backend install"),
                    "the warning must name the version and the fix: {reason}"
                );
            }
            other => panic!("expected Refuse, got {other:?}"),
        }
        // ...and re-installing under the new binary clears it.
        upgraded.install("cuda", &fetcher()).unwrap();
        assert_eq!(upgraded.state("cuda"), BackendState::Installed);
        assert_eq!(backend_dir_state(&dir, "1.2.0"), BackendDirState::Current);
    }

    #[test]
    fn a_hand_placed_payload_has_no_receipt_and_is_still_loaded() {
        // The manual fallback stays supported — it is how `backend install` gets debugged.
        let dir = tmpdir("manual");
        std::fs::write(dir.join("libggml-cuda.so"), LIB).unwrap();
        assert_eq!(backend_dir_state(&dir, "1.1.0"), BackendDirState::Unmanaged);
    }

    #[test]
    fn an_empty_dir_is_unmanaged() {
        assert_eq!(
            backend_dir_state(&tmpdir("empty"), "1.1.0"),
            BackendDirState::Unmanaged
        );
    }

    #[test]
    fn an_interrupted_install_is_refused_not_loaded() {
        let dir = tmpdir("interrupted");
        let s = good_store(dir.clone(), "1.1.0");
        s.install("cuda", &fetcher()).unwrap();
        // Simulate a crash between the two receipt writes.
        let text = std::fs::read_to_string(dir.join(RECEIPT)).unwrap();
        std::fs::write(
            dir.join(RECEIPT),
            text.replace("state: complete", "state: installing"),
        )
        .unwrap();

        assert_eq!(s.state("cuda"), BackendState::Interrupted);
        match backend_dir_state(&dir, "1.1.0") {
            BackendDirState::Refuse { reason } => {
                assert!(
                    reason.contains("not fully installed"),
                    "unhelpful: {reason}"
                )
            }
            other => panic!("expected Refuse, got {other:?}"),
        }
    }

    #[test]
    fn remove_deletes_the_files_and_the_receipt() {
        let dir = tmpdir("remove");
        let s = good_store(dir.clone(), "1.1.0");
        s.install("cuda", &fetcher()).unwrap();

        assert!(s.remove("cuda").unwrap());
        assert!(!dir.join("libggml-cuda.so").exists());
        assert!(!dir.join("libcudart.so.13").exists());
        assert!(!dir.join(RECEIPT).exists());
        assert_eq!(s.state("cuda"), BackendState::NotInstalled);
        assert_eq!(backend_dir_state(&dir, "1.1.0"), BackendDirState::Unmanaged);
        // Removing again is a no-op, not an error.
        assert!(!s.remove("cuda").unwrap());
    }

    #[test]
    fn remove_falls_back_to_the_manifest_for_a_hand_placed_payload() {
        let dir = tmpdir("remove_manual");
        std::fs::write(dir.join("libggml-cuda.so"), LIB).unwrap();
        let s = good_store(dir.clone(), "1.1.0");
        assert!(s.remove("cuda").unwrap());
        assert!(!dir.join("libggml-cuda.so").exists());
    }

    #[test]
    fn reinstall_drops_files_the_new_payload_no_longer_has() {
        let dir = tmpdir("prune");
        good_store(dir.clone(), "1.1.0")
            .install("cuda", &fetcher())
            .unwrap();
        // A stray file from the "previous" payload, recorded in the receipt. Built through the
        // receipt type rather than by patching its text, so the test cannot silently stop
        // exercising anything when serde_yaml changes how it indents a sequence.
        std::fs::write(dir.join("libcublas.so.12"), b"old").unwrap();
        let mut receipt = Receipt::load(&dir);
        receipt
            .installs
            .get_mut("cuda")
            .unwrap()
            .files
            .push("libcublas.so.12".to_string());
        receipt.save(&dir).unwrap();

        good_store(dir.clone(), "1.1.0")
            .install("cuda", &fetcher())
            .unwrap();
        assert!(
            !dir.join("libcublas.so.12").exists(),
            "a file the new payload does not declare must not survive the reinstall"
        );
        assert!(dir.join("libcudart.so.13").exists());
    }

    #[test]
    fn list_reports_platform_support_size_and_state() {
        let dir = tmpdir("list");
        let s = good_store(dir, "1.1.0");
        let rows = s.list();
        assert_eq!(rows.len(), 1);
        let cuda = &rows[0];
        assert_eq!(cuda.name, "cuda");
        assert!(cuda.platform_supported);
        assert_eq!(cuda.status, PublishStatus::Published);
        assert_eq!(cuda.state, BackendState::NotInstalled);
        assert_eq!(cuda.total_bytes, Some(27));
        assert_eq!(cuda.files, ["libggml-cuda.so", "libcudart.so.13"]);

        s.install("cuda", &fetcher()).unwrap();
        assert_eq!(s.list()[0].state, BackendState::Installed);
    }

    #[test]
    fn verify_reports_a_tampered_file_by_name() {
        let dir = tmpdir("verify");
        let s = good_store(dir.clone(), "1.1.0");
        s.install("cuda", &fetcher()).unwrap();
        std::fs::write(dir.join("libcudart.so.13"), b"tampered").unwrap();
        match s.verify("cuda").unwrap() {
            VerifyOutcome::Mismatch { expected, .. } => {
                assert!(
                    expected.contains("libcudart.so.13"),
                    "unhelpful: {expected}"
                )
            }
            other => panic!("expected Mismatch, got {other:?}"),
        }
    }

    #[test]
    fn verify_says_not_installed_before_an_install() {
        let s = good_store(tmpdir("verify_absent"), "1.1.0");
        assert_eq!(s.verify("cuda").unwrap(), VerifyOutcome::NotInstalled);
    }
}
