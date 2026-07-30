//! NVIDIA GPU detection, and the decision of whether to offer the CUDA payload.
//!
//! Lives here rather than in `knaif-llm` for two reasons: it needs the backend manifest (which
//! declares the driver floor and the measured-inadequate architectures), and it must work on a
//! machine where **CUDA is not installed** — which is the whole point of a nudge. A `knaif-llm`
//! device list cannot answer it: `LlamaBackendDevice` carries name/description/backend/memory/type
//! and no compute capability, and the `compute capability 8.6` line people remember is a CUDA-
//! backend init log that by definition does not exist before the payload is installed.
//!
//! ## Why the offer has two strengths
//!
//! CUDA is a **correctness** requirement on some NVIDIA hardware and an **optimisation** on the
//! rest. On Blackwell (sm_120) the Vulkan fallback generates at CPU speed — measured 2026-07-14 on
//! knaif's real workload, ~5.7 tok/s against a CPU's ~5.9 — so the payload is what makes the product
//! work, and the offer says so. On Ampere the same message would be scaremongering: CUDA is faster
//! and worth having, but Vulkan is perfectly usable.
//!
//! **The architecture list is data, not code.** The defect is in a llama.cpp/driver code path and
//! may be fixed upstream, at which point a baked-in "Blackwell is broken" list would start lying in
//! the other direction. It lives in `contracts/backends/backend-manifest.yaml` under
//! `nudge.vulkan_inadequate_compute_caps`, sourced from `docs/PERFORMANCE.md` §2, so re-measuring
//! when the llama.cpp pin moves is one edit in a data file.

use std::process::Command;

use crate::backend_manifest::BackendSpec;
use crate::backend_store::{BackendState, BackendStore};

/// One NVIDIA GPU as `nvidia-smi` reports it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NvidiaGpu {
    pub name: String,
    /// Full driver version, e.g. `610.74`.
    pub driver_version: String,
    /// Compute capability, e.g. `8.6` (Ampere) or `12.0` (Blackwell).
    pub compute_cap: String,
}

impl NvidiaGpu {
    /// Driver major version (`610.74` -> `610`), which is what the R580 floor is expressed in.
    pub fn driver_major(&self) -> Option<u32> {
        self.driver_version.split('.').next()?.trim().parse().ok()
    }
}

/// Probe for NVIDIA GPUs via `nvidia-smi`.
///
/// One call returns both fields the gate needs, so the compute-capability half costs nothing beyond
/// the driver check that was required anyway. An absent `nvidia-smi`, a non-zero exit, or
/// unparseable output all mean "no NVIDIA GPU worth offering for" — an empty vec, never an error.
/// This runs on every machine, including AMD and Intel ones, so it must be silent and cheap when it
/// finds nothing.
pub fn probe_nvidia() -> Vec<NvidiaGpu> {
    let output = Command::new("nvidia-smi")
        .args([
            "--query-gpu=name,driver_version,compute_cap",
            "--format=csv,noheader",
        ])
        .output();
    let Ok(output) = output else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    parse_nvidia_smi(&String::from_utf8_lossy(&output.stdout))
}

/// Parse `nvidia-smi --format=csv,noheader` rows. Split out so the format is testable without a GPU.
fn parse_nvidia_smi(text: &str) -> Vec<NvidiaGpu> {
    text.lines()
        .filter_map(|line| {
            let mut parts = line.split(',').map(str::trim);
            let name = parts.next()?;
            let driver_version = parts.next()?;
            let compute_cap = parts.next()?;
            if name.is_empty() || driver_version.is_empty() {
                return None;
            }
            Some(NvidiaGpu {
                name: name.to_string(),
                driver_version: driver_version.to_string(),
                compute_cap: compute_cap.to_string(),
            })
        })
        .collect()
}

/// Whether — and how strongly — to offer the CUDA payload on this machine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CudaOffer {
    /// No NVIDIA GPU, or no `nvidia-smi`. Say nothing at all: the default artifact already serves
    /// this machine, and an unsolicited GPU message on an AMD laptop is noise.
    NotApplicable,
    /// NVIDIA hardware, but the driver predates the CUDA floor. Offering the payload here would
    /// hand the user 668 MB that cannot load; tell them what to update instead.
    DriverTooOld {
        gpu: String,
        have: String,
        need: u32,
    },
    /// The Vulkan fallback has been measured to run at CPU speed on this architecture, so the
    /// payload is what makes the product usable rather than merely faster.
    Recommended { gpu: String },
    /// CUDA is faster here, and optional. Deliberately quotes **no number**: the "~3%" this used to
    /// claim was the generation column, and knaif's workload is prompt-decode-dominated. No
    /// replacement figure is quotable until `PERFORMANCE.md` §2 is reconciled.
    Optional { gpu: String },
    /// Already installed and usable by this build — nothing to say.
    AlreadyInstalled,
    /// Installed, but by a different release, or a previous install did not finish. The loader is
    /// ignoring it, so the user has paid for a payload they are not getting.
    NeedsReinstall { reason: String },
}

/// Decide what to tell the user about CUDA, given the manifest, the payload's state, and the probe.
///
/// Split from [`probe_nvidia`] so every branch is testable without NVIDIA hardware — which matters,
/// because the branches that are hardest to reach in the field (old driver, stale payload) are
/// exactly the ones whose message has to be right.
pub fn cuda_offer(store: &BackendStore, gpus: &[NvidiaGpu]) -> CudaOffer {
    let Some(spec) = store.manifest().get("cuda") else {
        return CudaOffer::NotApplicable;
    };

    match store.state("cuda") {
        BackendState::Installed => return CudaOffer::AlreadyInstalled,
        BackendState::Stale { installed_by } => {
            return CudaOffer::NeedsReinstall {
                reason: format!(
                    "the installed CUDA backend was built for knaif {installed_by}, so it is being \
                     ignored"
                ),
            }
        }
        BackendState::Interrupted => {
            return CudaOffer::NeedsReinstall {
                reason: "the CUDA backend install did not finish, so it is being ignored"
                    .to_string(),
            }
        }
        BackendState::NotInstalled => {}
    }

    // No payload for this OS/arch means there is nothing to offer, however good the GPU is.
    if spec.platform(store.platform()).is_none() {
        return CudaOffer::NotApplicable;
    }

    let Some(gpu) = gpus.first() else {
        return CudaOffer::NotApplicable;
    };

    // Driver floor. `nvcuda.dll` / libcuda.so presence alone is insufficient: an older driver loads
    // the library and then fails, which reaches the user as "CUDA didn't work".
    if let Some(need) = spec.requires.min_driver {
        match gpu.driver_major() {
            Some(have) if have < need => {
                return CudaOffer::DriverTooOld {
                    gpu: gpu.name.clone(),
                    have: gpu.driver_version.clone(),
                    need,
                }
            }
            // An unparseable driver version is treated as adequate rather than blocking the offer:
            // refusing on a parse failure would silently withhold the payload from a machine that
            // can use it, which is the worse error of the two.
            _ => {}
        }
    }

    if vulkan_is_inadequate(spec, &gpu.compute_cap) {
        CudaOffer::Recommended {
            gpu: gpu.name.clone(),
        }
    } else {
        CudaOffer::Optional {
            gpu: gpu.name.clone(),
        }
    }
}

/// Has the Vulkan fallback been *measured* inadequate on this compute capability?
fn vulkan_is_inadequate(spec: &BackendSpec, compute_cap: &str) -> bool {
    spec.nudge
        .vulkan_inadequate_compute_caps
        .iter()
        .any(|cc| cc == compute_cap)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend_manifest::BackendManifest;
    use std::path::PathBuf;

    const MANIFEST: &str = r#"
knaif_version: "1.1.0"
backends:
  cuda:
    status: published
    requires:
      vendor: nvidia
      min_driver: 580
    nudge:
      vulkan_inadequate_compute_caps: ["12.0"]
    platforms:
      test-x64:
        files:
          - name: libggml-cuda.so
            url: "https://example.test/x"
            sha256: "aa"
"#;

    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

    /// A store over a directory unique to this call. Tests run in parallel threads of one process,
    /// so a dir keyed only on the pid would have them overwriting each other's receipts — which
    /// shows up as an unrelated test's install state leaking into this one's offer.
    fn store() -> BackendStore {
        let dir = std::env::temp_dir().join(format!(
            "knaif_nudge_{}_{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        BackendStore::with_dir(dir, BackendManifest::from_yaml(MANIFEST).unwrap())
            .with_platform("test-x64")
    }

    /// Write the receipt a completed `backend install` would have left, so the offer logic can be
    /// exercised against an "installed" payload without downloading anything.
    fn write_receipt(dir: &std::path::Path, version: &str, state: &str) {
        std::fs::write(
            dir.join(".knaif-backends.yaml"),
            format!(
                "installs:\n  cuda:\n    knaif_version: {version}\n    state: {state}\n    \
                 files:\n    - libggml-cuda.so\n"
            ),
        )
        .unwrap();
    }

    fn gpu(name: &str, driver: &str, cc: &str) -> NvidiaGpu {
        NvidiaGpu {
            name: name.to_string(),
            driver_version: driver.to_string(),
            compute_cap: cc.to_string(),
        }
    }

    // The exact line the bench box produces, so a format change in nvidia-smi shows up here.
    #[test]
    fn parses_the_real_nvidia_smi_line() {
        let parsed = parse_nvidia_smi("NVIDIA GeForce RTX 3070 Laptop GPU, 610.74, 8.6\n");
        assert_eq!(
            parsed,
            vec![gpu("NVIDIA GeForce RTX 3070 Laptop GPU", "610.74", "8.6")]
        );
        assert_eq!(parsed[0].driver_major(), Some(610));
    }

    #[test]
    fn parses_multiple_gpus_and_ignores_junk_lines() {
        let parsed = parse_nvidia_smi(
            "NVIDIA GeForce RTX 5080, 590.00, 12.0\n\nnot-a-row\nNVIDIA A100, 580.1, 8.0\n",
        );
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed[1].name, "NVIDIA A100");
    }

    #[test]
    fn no_nvidia_gpu_says_nothing() {
        // An unsolicited GPU message on an AMD laptop is noise, not help.
        assert_eq!(cuda_offer(&store(), &[]), CudaOffer::NotApplicable);
    }

    #[test]
    fn blackwell_gets_the_strong_offer() {
        // Measured: Vulkan generates at ~CPU speed on sm_120, so the payload is what makes the
        // product work rather than what makes it faster.
        let offer = cuda_offer(
            &store(),
            &[gpu("NVIDIA GeForce RTX 5080", "610.74", "12.0")],
        );
        assert_eq!(
            offer,
            CudaOffer::Recommended {
                gpu: "NVIDIA GeForce RTX 5080".to_string()
            }
        );
    }

    #[test]
    fn ampere_gets_the_optional_offer() {
        let offer = cuda_offer(&store(), &[gpu("NVIDIA GeForce RTX 3070", "610.74", "8.6")]);
        assert_eq!(
            offer,
            CudaOffer::Optional {
                gpu: "NVIDIA GeForce RTX 3070".to_string()
            }
        );
    }

    #[test]
    fn an_old_driver_gets_an_update_hint_not_an_offer() {
        // The payload would download and then fail to load, which reads as "CUDA didn't work".
        let offer = cuda_offer(
            &store(),
            &[gpu("NVIDIA GeForce RTX 3070", "550.120", "8.6")],
        );
        assert_eq!(
            offer,
            CudaOffer::DriverTooOld {
                gpu: "NVIDIA GeForce RTX 3070".to_string(),
                have: "550.120".to_string(),
                need: 580,
            }
        );
    }

    #[test]
    fn a_driver_exactly_at_the_floor_is_offered() {
        assert!(matches!(
            cuda_offer(&store(), &[gpu("NVIDIA A100", "580.0", "8.0")]),
            CudaOffer::Optional { .. }
        ));
    }

    #[test]
    fn an_unparseable_driver_version_does_not_withhold_the_offer() {
        // Refusing on a parse failure silently denies the payload to a machine that can use it,
        // which is the worse of the two errors.
        assert!(matches!(
            cuda_offer(&store(), &[gpu("NVIDIA Whatever", "unknown", "8.6")]),
            CudaOffer::Optional { .. }
        ));
    }

    #[test]
    fn an_installed_payload_is_not_re_offered() {
        let s = store();
        write_receipt(s.dir(), "1.1.0", "complete");
        assert_eq!(
            cuda_offer(&s, &[gpu("NVIDIA GeForce RTX 5080", "610.74", "12.0")]),
            CudaOffer::AlreadyInstalled
        );
    }

    #[test]
    fn a_stale_payload_is_reported_rather_than_silently_ignored() {
        // The user paid ~668 MB for a backend the loader is now skipping. Saying nothing would
        // leave them believing CUDA is active.
        let s = store();
        write_receipt(s.dir(), "1.0.9", "complete");
        match cuda_offer(&s, &[gpu("NVIDIA GeForce RTX 5080", "610.74", "12.0")]) {
            CudaOffer::NeedsReinstall { reason } => assert!(reason.contains("1.0.9"), "{reason}"),
            other => panic!("expected NeedsReinstall, got {other:?}"),
        }
    }

    #[test]
    fn a_platform_with_no_payload_offers_nothing() {
        let s = BackendStore::with_dir(
            PathBuf::from("."),
            BackendManifest::from_yaml(MANIFEST).unwrap(),
        )
        .with_platform("plan9-x64");
        assert_eq!(
            cuda_offer(&s, &[gpu("NVIDIA GeForce RTX 5080", "610.74", "12.0")]),
            CudaOffer::NotApplicable
        );
    }

    // The architecture split must be data-driven: the defect is in a llama.cpp/driver code path and
    // may be fixed upstream, at which point a baked-in list would lie in the other direction.
    #[test]
    fn the_inadequate_arch_list_comes_from_the_manifest_not_the_code() {
        let empty = MANIFEST.replace(r#"["12.0"]"#, "[]");
        let dir = store().dir().to_path_buf();
        let s = BackendStore::with_dir(dir, BackendManifest::from_yaml(&empty).unwrap())
            .with_platform("test-x64");
        assert!(
            matches!(
                cuda_offer(&s, &[gpu("NVIDIA GeForce RTX 5080", "610.74", "12.0")]),
                CudaOffer::Optional { .. }
            ),
            "clearing the manifest list must downgrade the offer, with no code change"
        );
    }
}
