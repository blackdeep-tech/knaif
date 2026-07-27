# Portable Builds — floor-pinned release environments for Linux and Windows

**Status:** Done · **Created:** 2026-07-27 · **Completed:** 2026-07-27
**Owner:** packaging · **Ref:** [`installers/package.sh`](../../installers/package.sh) · [`installers/linux/build-appimage.sh`](../../installers/linux/build-appimage.sh) · [`installers/smoke.sh`](../../installers/smoke.sh) · [`docs/RELEASE.md`](../RELEASE.md)

> **Why this plan exists now.** 1.0.2 will be the project's **first native release** — no GitHub
> Release has ever existed and no native artifact has ever been downloadable (established in
> [windows-installer-polish](2026-07-25-windows-installer-polish.md), *Release framing*). Everything
> shipped so far ran on a machine that also built it. That is exactly the condition under which a
> **runtime floor** goes unnoticed, and a first release is the worst time to discover one.
>
> **Both OSes turn out to have a real defect, and both share the same structural blind spot:
> the check runs where the bug cannot happen.** `installers/smoke.sh` executes the artifact on the
> build box, which by definition has every build-time dependency installed. It is a good test of
> *staging* and useless as a test of *portability*.

**Goal:** Publish artifacts whose runtime floor is **chosen and documented** rather than inherited
from whichever machine happened to build them — and make the release build environment a file in the
repository instead of the state of one box.

---

## Decision log

**2026-07-27 — the release container is a *release* tool, not a *development* tool.** `cargo build`,
`just check`, `just test-native` and running the CLI stay **native, on whatever distro a contributor
has**; Docker appears nowhere in the contribution path. `installers/package.sh` keeps working
natively and unchanged, documented as *"the floor is your distro's glibc."* Only **cutting a
published release** uses the container. A contributor never touches it; a release-cutter runs one
command.

**2026-07-27 — Docker, not a second WSL distro.** The alternative considered was installing Ubuntu
22.04 as another WSL2 distro on the Windows box and building there. Rejected on three grounds:

- **Portability is the whole point, and WSL is the Windows-only option.** A future maintainer on a
  native Linux box can run a Dockerfile — containers are *cheaper* on Linux than anywhere else, no
  VM, no Docker Desktop, `podman` or `docker.io` from the package manager. Nobody can run someone
  else's WSL distro. Choosing WSL means choosing "releases only happen on one Windows machine."
- **It reproduces the snowflake problem the release doc already warns about.**
  [`RELEASE.md`](../RELEASE.md) §2 records that a cached `llama-cpp-sys-2` build hides missing `-dev`
  packages, so a box can package successfully while being broken. A fresh container structurally
  cannot have that failure; a long-lived hand-built distro is precisely where it hides.
- **A Dockerfile *is* the dependency list**, in executable form. Writing it immediately surfaced two
  errors in RELEASE.md's prose list (L5 below) that had been invisible for months — and *building*
  it surfaced a third that no amount of reading would have (L6: the floor base's Vulkan headers are
  too old to compile against). A prose list cannot be wrong in a way that stops the build; an
  executable one cannot be wrong in a way that doesn't.

**2026-07-27 — the floor is a property of the artifact, not of the builder's OS.** This is why the
container is not a Windows workaround. A maintainer on Arch (glibc 2.42) or Fedora 42 (2.41) who
builds a release natively ships a binary that starts on almost nothing, and **nothing warns them**.
The container exists to make that outcome impossible regardless of who cuts the release.

**2026-07-27 — ship the VC++ runtime app-local; do NOT chain the redistributable installer.**
Windows artifacts carry `VCRUNTIME140` / `VCRUNTIME140_1` / `MSVCP140` / `VCOMP140` in `bin\` beside
the exe. The alternative — having `setup.exe` run `vc_redist.x64.exe` — was rejected:

- **The portable zip cannot chain anything.** Its whole promise is *unpack and run*, and there is no
  installer to hook. App-local is therefore required **regardless**, so chaining would add a second
  mechanism without retiring the first.
- **With app-local DLLs present, a machine-wide redist is never loaded.** Windows resolves from the
  executable's own directory first — the same rule that makes `llama.dll` and the `ggml-*` backends
  work. Chaining would install something the artifact structurally cannot use.
- **The redist installer requires admin; this installer deliberately does not.**
  `PrivilegesRequired=lowest`, per-user, `%LOCALAPPDATA%`. Chaining either raises UAC mid-install —
  breaking a documented product property — or hard-fails for a user who cannot elevate.
- **The cost is 1.06 MB on a 31 MB artifact (3.4 %)**: `msvcp140` 628 KB, `vcomp140` 208 KB,
  `vcruntime140` 174 KB, `vcruntime140_1` 49 KB.
- **Also rejected: static CRT.** See P1 — allocations cross the `knaif.exe` ↔ `llama.dll` boundary
  and a static CRT gives each module its own heap, trading a link error for a rare crash.

> **The accepted cost, stated plainly: app-local copies get no security servicing.** A machine-wide
> redistributable receives CRT fixes through Windows Update; ours do not, and the only remedy is to
> rebuild and re-release. Judged acceptable because knaif is a local CLI rather than a network-facing
> service, so the CRT surface exposed to untrusted input is small. **Revisit if** a CRT CVE is
> reachable from knaif's input handling, or if knaif ever grows a listening/daemon mode — the second
> of which is already contemplated as post-v1 work. This is a decision with an expiry condition, not
> a permanent one.

**2026-07-27 — do NOT bundle `libstdc++.so.6`; RHEL/Rocky/Alma 9 is documented as unsupported.**
Taken *after* the ELF audit, which is what the earlier "audit first, then decide" deferral was for.
The audit changed the shape of the question twice over:

- **glibc was never the binding constraint.** The artifact requires `GLIBC_2.34` — *below* the
  2.35 floor image — while `libstdc++` requires **`GLIBCXX_3.4.30`** and **`CXXABI_1.3.13`**. So
  RHEL 9 (glibc 2.34, GLIBCXX 3.4.29) misses by **one libstdc++ version and nothing else**, and
  bundling would genuinely bring the whole enterprise family in.
- **It would still be the wrong trade.** Our `libstdc++` is GCC 12's; a modern host's Mesa/Vulkan
  driver is commonly built against GCC 13/14 (`GLIBCXX_3.4.32`+). Because `libggml-vulkan.so`
  **dlopens that driver**, forcing our older copy ahead of the system one via `$ORIGIN` can break
  GPU support on the newest and most common desktops. That trades a real regression for
  Ubuntu/Fedora users against reach for a distro nobody has yet asked for.

**If enterprise reach is ever wanted, the right fix is a second artifact built on an `almalinux:9`
base**, so the binary actually targets that toolchain instead of grafting a newer `libstdc++` onto
a glibc-2.35 build. No driver risk, at the cost of a second Dockerfile and a fifth published
artifact. Revisit on demand, not speculatively.

**2026-07-27 — this release is 1.0.2 (patch), not 1.1.0.** It publishes the first-ever Linux
artifacts and the project's first GitHub Release, which is arguably MINOR under semver — but **no
runtime code changed**. Everything here is packaging correctness plus artifacts that should always
have shipped, so the patch number is the honest description. The release notes carry the burden of
explaining that a patch adds a platform.

**Deliberately not bound to the CI plan.** When
[post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md) lands `release.yml`, this
image is what its Linux job runs. That is an *adoption* of this plan, not a dependency of it — a
hand-cut release from the release container is a complete outcome, and 1.0.2 needs to ship before CI
exists.

---

## Findings

Every row below was verified on 2026-07-27, not inferred. The *Verified* column says how.

| # | Symptom | Root cause | Verified | Severity |
|---|---|---|---|---|
| **W1** | On a clean Windows box `knaif.exe` **fails at process start** with a missing-DLL error and prints nothing | **All 13 staged binaries** depend on the VC++ Redistributable, which is **not part of Windows**, and **none of it is staged** — `bin\` holds only the ggml/llama libs. Four distinct DLLs: **`VCRUNTIME140.dll`** (everything), **`MSVCP140.dll`** + **`VCRUNTIME140_1.dll`** (the C++ libs), and **`VCOMP140.dll`** — the **OpenMP** runtime, pulled in by `ggml-base.dll` and all nine `ggml-cpu-*` variants | `scripts/check_pe_imports.py` over the staged 1.0.2 tree: **41 undeclared imports across 13 binaries**; `package.sh` greps clean for `VCRUNTIME`/`MSVCP`/`redist` | **P0 — breaks the artifact entirely** |
| **W2** | Nothing catches W1 | `smoke.sh` runs the exe on the **build box**, which has the redist because it has Visual Studio. The failure is unreachable there | Structural; this box carries `vcruntime140.dll` 14.51 plus the `*d.dll` debug variants that only ship with VS | P1 — the reason W1 survived |
| **L1** | The Linux artifact will not start on Ubuntu 22.04 LTS, Debian 12, or RHEL/Rocky/Alma 9 | Built on Ubuntu 24.04 → **glibc 2.39 floor**. Those distros carry 2.35 / 2.36 / 2.34 | `ldd --version` in the WSL distro; `ubuntu:22.04` container reports 2.35 | **P0 for a first release** |
| **L2** | The AppImage ships **without `NOTICE`** — an Apache-2.0 §4(d) violation | [`build-appimage.sh:49`](../../installers/linux/build-appimage.sh#L49) copies `LICENSE` only. F11 was fixed in `package.sh`, so the staged tree and the tarball are compliant, but the AppImage is assembled from that tree by a **second script that was never updated** | Script read | **P0 — licence compliance** |
| **L3** | Nothing catches L2 | `smoke.sh` accepts `.zip`, `.tar.gz` or a staged dir — **not `.AppImage`** ([`smoke.sh:17`](../../installers/smoke.sh#L17)). The LICENSE+NOTICE assertion cannot run against the one artifact that has the bug | Script read | P1 — the reason L2 survived |
| **L4** | The AppImage's icon is a **1×1 transparent PNG** | A placeholder from before `media/knaif.ico` existed ([`build-appimage.sh:72`](../../installers/linux/build-appimage.sh#L72)). W3 gave Windows a real mark; Linux was never revisited | Script read | P2 |
| **L5** | RELEASE.md's Linux dependency list is **wrong for any distro older than 24.04**, in three ways | (a) `glslc` is **not packaged by Ubuntu at all**, and llama.cpp compiles its Vulkan shaders with it — cmake fails at configure with *"Could NOT find Vulkan (missing: … glslc)"*; (b) `libfuse2t64` is **`libfuse2`** before 24.04's 64-bit `time_t` rename; (c) **22.04's Vulkan headers (1.3.204, early 2022) are too old to build against** — see L6 | `apt-cache policy` for all 11 packages inside `ubuntu:22.04`; then a full build | P1 |
| **L6** | The Vulkan backend **does not compile** on the floor base, and nothing short of a full build reveals it | `libvulkan-dev` on Ubuntu 22.04 is **1.3.204**. `ggml-vulkan.cpp` needs `VkPhysicalDeviceCooperativeMatrixFeaturesKHR`, `VkPhysicalDevicePipelineRobustnessFeaturesEXT`, `vk::LayerSettingsCreateInfoEXT` and `vk::DriverId::eMesaDozen`, none of which exist in those headers. Fixed by taking `vulkan-headers` / `libvulkan-dev` / `spirv-headers` / `glslang-tools` from **LunarG 1.4.313.0** instead of Ubuntu | Container build: dozens of *"was not declared in this scope"* errors in `ggml-vulkan.cpp`, then `ninja: build stopped` | **P0 for the container — it blocked the Linux artifact entirely** |

| **C1** | `just package-linux --kind=cpu` after a `vulkan` build panics: `hard_link … AlreadyExists` | Every feature set gets its own `llama-cpp-sys-2` out dir but they all **hard-link into the same `target/release/`**. Its build.rs does `if !dst.exists() { hard_link(..).unwrap() }`, and `exists()` **follows symlinks** — a stale SONAME symlink left by the other kind reads as absent while still occupying the name. One shared cache volume therefore made kind-switching fail | Owner ran `--kind=cpu` against a warm cache | P1 — blocked a documented flag |
| **C2** | A `--kind=cpu` run built an AppImage **from the leftover `vulkan` staging tree**, smoke-tested it, and printed `PASS` | The staged-dir glob `knaif-*-linux-x64` cannot match `…-linux-x64-cpu` (package.sh's SUFFIX rule), so it silently selected the unsuffixed vulkan tree. Every check then passed — on the wrong artifact | Same run: `dist/` held a cpu tarball beside a vulkan AppImage | **P0 — a mislabelled artifact that reports success.** Same shape as the stale `dist/staging` that once produced a "knaif contributors" installer |
| **C3** | A **clean** Vulkan build is OOM-killed: `c++: fatal error: Killed signal terminated program cc1plus` | llama.cpp's generated shader TUs need 1–2 GB each in `cc1plus`; cargo defaults `NUM_JOBS` to the CPU count and the cmake crate passes it through as `--parallel 16`, against a **7.5 GB** Docker VM | Clean-volume rebuild after C1's fix. **An earlier "clean build works" claim in this plan was wrong** — that run had a partially warm cache | **P0 for releases — it only bites a from-scratch build**, i.e. exactly what cutting a release does |

> **W1 and L2 are the same failure mode wearing different clothes**, and W2/L3 say why neither was
> caught. In both cases a compliance-or-startup dependency is added in one place and a *second*
> packaging path silently doesn't get it, while the verification step runs in the one environment
> where the defect is invisible. **Every workstream below that fixes a defect also moves its check
> somewhere the defect can actually occur.** That is the through-line of this plan.

> **C1–C3 were found by the owner running `--kind=cpu`, after this plan was already marked done —
> and they are the sharpest evidence for the plan's own thesis.** Each one passed every check I had
> written. C2 is the worst: it produced a mislabelled artifact and reported `PASS`, because the
> checks verified *an* artifact rather than *the* artifact. C3 is the most dangerous in practice,
> because it is invisible until a from-scratch build — so the machine works for weeks and fails on
> release day. Fixes: one cache volume **per kind** (structural, not documented-around), a staged
> path derived from `$KIND` with the AppImage built only for the release kind, and build
> parallelism capped by **memory** rather than CPU count.

---

## Workstreams

Ordered by what blocks the release. P1 and P2 are independent of the container work and could ship
without it; P3 is what makes the Linux artifact worth publishing at all.

### - [x] P1 — Ship the Windows C runtime *(W1, W2 — shipped 2026-07-27)*

> **Reopened and re-closed the same day after an external audit (2026-07-27).** Three real defects
> in the first cut, all now fixed and re-verified:
> - **The guard was never mandatory.** `package.sh` *claimed* in a comment that the checker "is run
>   below" and did not run it at all; it executed only via `smoke.sh`, which is per-artifact, manual,
>   and **passed silently when python was absent**. A guard that opts itself out on the machine
>   lacking the tooling is worth nothing — that is the exact shape of the bug it exists to catch. It
>   is now a **required packaging step** that hard-fails without python.
> - **The allowlist could pass a fatal dependency.** Loadable backends were matched by the `ggml-`
>   prefix, which also matches **`ggml-base.dll` — a core lib loaded at process start**. A GPU-driver
>   import there would have passed while making the CLI unlaunchable. Now matched against an explicit
>   `CORE_LIBS` set mirroring `package.sh`'s own staging split.
> - **CUDA runtime libs were allowlisted as driver-provided**, inverting the check: `package.sh`
>   *stages* `cudart64_*`/`cublas64_*` from `$CUDA_PATH`, so an artifact with a **missing** CUDA
>   payload would have passed. Only `nvcuda.dll` (the actual driver) remains allowlisted.
>
> Also hardened: CRT discovery now prefers **`VCToolsRedistDir`** from the active developer
> environment — Microsoft's documented method, and the only one that guarantees the runtime matches
> the *compiler* rather than merely existing on the disk. The filesystem scan is a fallback that
> announces itself, searches per-arch, and sorts versions with `sort -V` (lexically, `14.9` sorts
> after `14.51` and `2022` after `18` — both the wrong answer).

- [x] **Stage the four VC++ runtime DLLs beside the exe** in `package.sh`'s Windows branch:
      `VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, `MSVCP140.dll` and **`VCOMP140.dll`** into `bin\`.
      App-local deployment is the shape this artifact already uses — `dynamic-backends` puts every
      llama/ggml lib beside `knaif.exe` and Windows resolves from the exe's own directory first, so
      the runtime is the one thing left out of an otherwise self-contained tree.
  - **`VCOMP140.dll` lives in a different redist folder** (`Microsoft.VC143.OpenMP`, not
    `…CRT`), which is exactly why a spot-check of three binaries missed it and the full scan did
    not. It is the OpenMP runtime that llama.cpp's CPU backends are compiled against — **omitting it
    breaks every `ggml-cpu-*` variant**, i.e. inference itself, not just startup.
  - **Locate the redist directory, never hard-code it.** This box has **three** versions installed
    (`14.38.33130`/VC143, `14.44.35112`/VC143, `14.51.36231`/VC145) and the path moves with every VS
    update. **Take the newest available** — the VC14 runtime is backward compatible across these, so
    a redistributable at least as new as the compiler is the supported configuration. Fail loudly if
    none is found rather than silently producing a broken artifact, which is the failure this whole
    workstream exists to end.
  - **Redistribution grant — CONFIRMED 2026-07-27, and it adds no obligation to the artifact.**
    The `Redist.txt` shipped with VS 18 is a **three-line stub** pointing at
    `https://aka.ms/vs/18/redistribution`; the authoritative Distributable List is
    [online](https://learn.microsoft.com/visualstudio/releases/vs18/redistribution). Its *Visual C++
    Runtime Files* section grants, verbatim: *"you may copy and distribute with your program any of
    the files within the following folder and its subfolders … `[VisualStudioFolder]\VC\redist`"*,
    subject to not modifying them. Both source directories sit inside that tree
    (`…\VC\Redist\MSVC\<ver>\x64\Microsoft.VC145.CRT` and `…\Microsoft.VC145.OpenMP`), so all four
    DLLs are covered. Three things worth having written down:
    - **The only carve-out is `debug_nonredist`** (and `onecore\debug_nonredist`), which is where
      `Microsoft.VC[version].DebugOpenMP` lives. `package.sh` takes the **release** OpenMP from
      `Microsoft.VC145.OpenMP`, so it is on the right side of the one boundary that matters — but a
      future glob loosened to `Microsoft.VC*` **must not** be allowed to reach a debug folder.
    - **No attribution or licence-inclusion requirement.** Unlike the bundled CUDA libs — where
      `package.sh` *hard-fails* without `NVIDIA-CUDA-EULA.txt` — the VC++ grant asks only that the
      files be unmodified. **Nothing new goes into the artifact's `licenses/` dir**, and this bullet
      is the record of why that absence is deliberate rather than an oversight.
    - **The grant is conditional on being a licensed VS user.** The page states distribution *"is
      limited to licensed Visual Studio users and is subject to its license terms."* Builds come
      from **VS Community**, whose terms cover open-source projects and small organisations. That is
      a condition on **who may cut a release**, not on the artifact — and it is another reason the
      pinned-container work in P3 matters: it keeps the Linux build reproducible by anyone, while
      the Windows build stays tied to a properly licensed toolchain.
  - **Rejected: static CRT** (`-Ctarget-feature=+crt-static` plus
    `-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded`). Allocations cross the `knaif.exe` ↔ `llama.dll`
    boundary, and a statically linked CRT gives each module **its own heap** — freeing across the
    boundary is then undefined behaviour that appears as a rare crash rather than a link error.
    Not worth it to avoid shipping three DLLs.
  - **Rejected: chaining the redistributable installer.** It would fix `setup.exe` and leave the
    **portable zip** — the artifact whose entire promise is "unpack and run" — still broken.
- [x] **Assert the artifact has no undeclared runtime dependency — statically.** This is the primary
      guard, not the clean room. Parse the **PE import table** of every binary in `bin\` and require
      each imported DLL to be either (a) **staged beside it** or (b) on an explicit **clean-Windows
      baseline** allowlist. `VCRUNTIME140.dll` / `MSVCP140.dll` / `VCRUNTIME140_1.dll` are **not** on
      that baseline; the UCRT forwarders (`api-ms-win-crt-*`) **are**, since they ship with Windows
      10+. Properties this has that a VM run does not:
  - **It fails on the build box.** W1 survived because every check ran where the redist exists; a
    static check is machine-independent, so the one environment that hid the bug now reports it.
  - **The allowlist is the documentation.** "What a clean Windows provides" stops being tribal
    knowledge and becomes a reviewable list — and adding an entry to it is the moment someone has to
    justify a new runtime dependency.
  - **It generalises.** A new loadable backend, a new crate pulling in a DLL, or a CUDA payload all
    get checked for free.
  - **No tooling to install.** `objdump` and `strings` are absent from this box's Git Bash, but
      Python is present, and a PE import directory is a short `struct` parse — the same hand-rolled
      approach as `test_installer_iss.py`. Do **not** add a `pefile` dependency for this.
- [x] **Prove it catches W1 before trusting it**, the way W6's lint was mutation-tested: run it
      against the **current, unfixed** staged tree and require it to fail naming `VCRUNTIME140.dll`,
      then against the fixed tree and require it to pass.
- [x] **Confirmed in a clean room** *(Windows Sandbox, 2026-07-27)*. Driven headlessly from a
      `.wsb` config: artifact mapped read-only, a writable folder for results, `LogonCommand`
      running the probe and shutting the VM down. **Same image, before and after the fix:**

      | | exit code | stdout |
      |---|---|---|
      | before | `-1073741515` (`0xC0000135` STATUS_DLL_NOT_FOUND) | *(empty)* |
      | after | `0` | `documents … / ffmpeg …` |

      The image reported `VCRUNTIME140.dll ABSENT`, `MSVCP140.dll ABSENT`, `VCOMP140.dll ABSENT`
      **in both runs** — so the artifact was fixed by shipping them, not by the environment
      changing. It also reported **`ucrtbase.dll PRESENT`**, which independently confirms the
      allowlist split: the UCRT is an OS component, so accepting `api-ms-win-crt-*` while rejecting
      `VCRUNTIME140` was correct rather than a guess. Windows 11 24H2 (10.0.26100.8875).

### - [x] P2 — Finish F11 on Linux *(L2, L3, L4 — shipped 2026-07-27)*

> **Verified on Linux against a synthetic staged tree** (WSL, 2026-07-27) — a real Linux build
> needs P3's container, but none of these three defects depend on a real binary, so waiting for it
> would have been theatre:
> - staged tree **without** `NOTICE` → `build-appimage.sh` **exits 1** naming the file; **with** it,
>   exit 0. `NOTICE` confirmed present inside the built AppImage.
> - `smoke.sh` opens the `.AppImage` and reaches **check 6** — the LICENSE+NOTICE assertion that
>   previously had no way to see this artifact at all.
> - the extracted icon measures **256×256**, not the 1×1 transparent placeholder.
>
> **Line endings are load-bearing here.** `installers/smoke.sh` briefly carried CRLF in the working
> tree, and Linux refused to run it at all: the kernel reads the shebang literally, so a trailing
> carriage return becomes part of the interpreter name and `env` reports it cannot find an
> interpreter whose name ends in a stray CR. `.gitattributes` already declares `text eol=lf` for
> these scripts and git stores LF, so a fresh clone — including one made **inside** the release
> container, as P3 specifies — is correct. The hazard is confined to editing on Windows, and it is
> one more reason the container clones the tag rather than mounting the Windows checkout.

- [x] **Copy `NOTICE` into the AppDir** in `build-appimage.sh`, beside the existing `LICENSE` copy.
      One line. It is an Apache-2.0 §4(d) obligation and the last place F11 still survives.
- [x] **Teach `smoke.sh` to accept an `.AppImage`.** Unpack with `--appimage-extract` (no FUSE
      needed, which matters in a container), then run the existing checks against the extracted
      `squashfs-root/usr/` tree. **Without this the fix above has no regression guard** — and L2
      exists precisely because there wasn't one.
- [x] **Give the AppImage the real icon.** `media/knaif.ico` is generated from `media/logo-square.png`
      by `scripts/gen_icon.py`; the AppImage wants a PNG, so render one from the same source rather
      than adding a third copy of the mark. Keep the generator as the single path — the icon is a
      *generated* asset, like the licence reports.

### - [x] P3 — The Linux release container *(L1, L5, L6 — shipped 2026-07-27)*

> **Owner decision 2026-07-27: fully pinned**, over the cheaper "floor-pinned" option that would
> have left the `ubuntu:22.04` tag, a live LunarG repo and appimagetool's rolling `continuous`
> build floating. Every input is now fixed:
>
> | Input | Pin |
> |---|---|
> | base image | `ubuntu:22.04@sha256:0e0a0fc6d18f…5ef8982` |
> | apt packages | `snapshot.ubuntu.com/ubuntu/20260727T000000Z` |
> | Vulkan headers / loader | LunarG `1.4.313.0~rc1-1lunarg22.04-1` |
> | spirv-headers / glslang / shaderc | LunarG, exact versions |
> | appimagetool | `1.9.1`, `sha256:ed4ce84f…cb384eb0` |
> | Rust | `1.96.0`, matching `rust-toolchain.toml` |
>
> **Three things pinning forced that a floating config never hits**, all found by building it:
>
> - **A TLS bootstrap step.** `snapshot.ubuntu.com` is HTTPS-only and the minimal `ubuntu:22.04`
>   image ships **no CA bundle** — so apt cannot reach the snapshot until `ca-certificates` exists,
>   which apt installs. The image now installs it from Ubuntu's default http sources first, then
>   re-installs it from the snapshot so nothing unpinned survives. Integrity is unaffected: apt
>   verifies by repository signature, not by TLS.
> - **A checksum, not just a tag, for appimagetool.** It self-reports as a *continuous build dated
>   2025-12-04* despite tag `1.9.1` being published **2025-11-18** — the GitHub API confirms the
>   asset was **re-uploaded onto the existing tag**. A tag-only pin would have silently handed us
>   different bytes; the checksum makes it deterministic, and a future substitution now fails the
>   build loudly instead of drifting.
> - **The security trade, written into the Dockerfile.** A fixed apt snapshot receives no security
>   updates. Same shape as the app-local VC++ runtime decision, same remedy: bump deliberately, at
>   least every release and immediately on a relevant CVE. Reproducibility and patching pull in
>   opposite directions and the file says which one it chose.

- [x] **`installers/linux/Dockerfile`** on **`ubuntu:22.04`** (**glibc 2.35**, verified), installing
      the eleven build packages plus `git`/`curl`/`ca-certificates`, the Rust toolchain via rustup,
      and `appimagetool`. Two corrections that prose missed and a container caught:
  - **`glslc` is not packaged on 22.04.** Add **LunarG's jammy apt repo** and install `shaderc`,
    which provides `/usr/bin/glslc` — verified working end to end in a container
    (`shaderc 2025.2~rc1-1lunarg22.04-1`).
  - **`libfuse2t64` is `libfuse2`** before 24.04.
  - No FUSE device or extra container privileges are needed: `build-appimage.sh` already invokes
    `appimagetool --appimage-extract-and-run` ([line 81](../../installers/linux/build-appimage.sh#L81)).
- [x] **A `just` recipe** that builds the tarball and the AppImage inside it. Two constraints:
  - **Never bind-mount the Windows checkout.** `/mnt/c` is 9p **without the `metadata` option**, so
    every file reads `-rwxrwxrwx` and those modes would be baked into the tarball and the AppDir.
    For a release build, `git clone --depth 1 --branch <tag>` **inside** the container: the tree is
    then provably the tag with no local dirt — which is the direct answer to the stale
    `dist/staging` that produced a *"knaif contributors"* installer earlier in this cycle.
  - **Mount a cache volume** for `~/.cargo` and `target/`, or every run recompiles llama.cpp.
- [x] **Prove the floor — both directions, with runnable commands.** An earlier draft of this bullet
      said to test "in a container *older* than the build image" and then named `ubuntu:22.04`, which
      **is** the build image; and it mounted `<artifact>` at `/x` then ran `/x/bin/knaif`, a path that
      only exists for an already-extracted directory, not for a tarball or an AppImage. Both
      corrected. A floor claim needs a **pass at the floor and a fail below it** — otherwise the
      number is decoration:

      ```bash
      # extract once on the host
      tar xzf dist/knaif-<ver>-linux-x64.tar.gz -C /tmp/floor
      ./dist/knaif-<ver>-linux-x86_64.AppImage --appimage-extract   # -> squashfs-root/

      # MUST PASS at the claimed floor (glibc 2.35)
      docker run --rm -v /tmp/floor/knaif-<ver>-linux-x64:/x:ro ubuntu:22.04 /x/bin/knaif skills list
      docker run --rm -v "$PWD/squashfs-root:/x:ro" ubuntu:22.04 /x/usr/bin/knaif skills list

      # MUST FAIL below it — this is what proves the floor is real and not merely untested
      docker run --rm -v /tmp/floor/knaif-<ver>-linux-x64:/x:ro ubuntu:20.04 /x/bin/knaif skills list
      ```

      The AppImage is tested **extracted**, because running it as a single file needs FUSE the
      container does not have. That checks the payload's floor, not the AppRun wrapper — run the
      `.AppImage` itself on a real desktop at least once.
- [x] **Audit the full ELF requirement set, not just glibc.** A glibc version is **not** sufficient
      to claim a distro works. llama.cpp is C++, so the artifact also carries versioned `GLIBCXX_*`
      and `CXXABI_*` requirements from `libstdc++`, plus whatever `DT_NEEDED` names must be resolved
      by the host. A build on 22.04 links GCC 11's `libstdc++`, and a distro can satisfy glibc 2.35
      while lagging on `GLIBCXX`. **Write the Linux counterpart of `scripts/check_pe_imports.py`** —
      audit `DT_NEEDED` against what is staged plus a documented baseline, and report the maximum
      required `GLIBC_` / `GLIBCXX_` / `CXXABI_` symbol version:
      `readelf -dV`, or `objdump -T | grep -o 'GLIBCXX_[0-9.]*' | sort -V | tail -1`. Only then is
      the compatibility table below a claim rather than a guess. **Decide too whether to bundle
      `libstdc++.so.6`** — it removes the axis entirely at the cost of ~2 MB, exactly as the VC++
      runtime does on Windows, and the symmetry is not an accident.
- [x] **Decide and record the supported floor**, *after* the audit above rather than from the glibc
      number alone. glibc 2.35 reaches Ubuntu 22.04+, Debian 12+, Fedora 36+ and Mint 21;
      **RHEL/Rocky/Alma 9 is 2.34 and still misses** — accept that or go older and say so. Whatever
      is chosen goes in `site/docs/index.md` and the release body as a stated requirement, not left
      for a user to discover.

### - [x] P3b — Choose a Windows floor *(shipped 2026-07-27)*

P1 made the Windows artifact self-contained; it did **not** pick a minimum Windows version. The
plan's goal says "chosen and documented" for both platforms, and only Linux has a number. Left
alone, the floor is whatever the allowlist happens to imply.

- [x] **State a minimum Windows version and put it beside the Linux floor.** The two constraints
      that set it: the artifact imports the **UCRT** (`api-ms-win-crt-*`), an OS component **from
      Windows 10 onward**, and Microsoft supports the v14 runtime on **Windows 10 and 11**. So
      **Windows 10 x64** is the defensible claim. Record it in `site/docs/index.md`, the release
      body, and `installers/windows/README.md`.
- [x] **Note what has actually been tested, which is not the floor.** The clean-room run was
      Windows 11 24H2 — the *top* of the range. Testing the floor needs a Windows 10 image, and
      until that exists the claim rests on Microsoft's support statement rather than on a run of
      ours. Say so rather than implying both ends were exercised.
- [~] **Considered and not done: tying the `api-ms-win-*` allowlist to a specific Windows release.**
      `check_pe_imports.py` accepts every API-set contract regardless of which release introduced
      it, so a future dependency on a newer set would raise the real floor silently. **Decided
      against for now**, on two grounds: the artifact's current API-set imports are the long-standing
      UCRT ones, which are exactly what "Windows 10 or later" already means; and encoding a
      per-contract introduction table would be a large, hand-maintained mapping whose staleness
      would itself become a source of false results — the failure mode the allowlist exists to
      avoid. **Revisit if** the Windows floor is ever raised deliberately, or if a dependency lands
      that plausibly needs a newer API set — at which point the mapping only needs to cover the
      contracts actually imported, not all of them.

### - [x] P4 — Make portability testable, not incidental *(shipped 2026-07-27)*

P1's static import check and P2's `smoke.sh` work make the *artifact* self-describing. Execution on a
foreign machine is still the thing that proves it, and that gap is what produced W1 and L1.

> **The two layers are not redundant, and the order matters.** The static check runs every build,
> catches the defect on the machine that caused it, and needs no VM — so it is what actually holds
> the line. The clean-room run is periodic, human, and validates the *baseline the static check
> assumes*. Neither substitutes for the other: a wrong allowlist passes the static check forever,
> and a clean-room run nobody performs catches nothing. **Windows Sandbox needs a reboot as of
> 2026-07-27**, which is precisely why the static check is P1's primary guard.

- [x] **Add a clean-room step to `RELEASE.md` §4** covering both OSes: the Linux artifact executed in
      a floor-glibc container, the Windows artifact executed in Windows Sandbox. Both are free, both
      take minutes, and **neither can pass on the build box by accident** — which is the only
      property that matters here.
- [x] **State the rule the findings table earned**, so the next packaging path inherits it: *a
      verification step that runs on the build box tests staging, never portability.* Any new
      artifact shape (a second Linux format, macOS, a container image) needs its own clean-room run
      before it is published.

### - [x] P5 — Docs *(shipped 2026-07-27)*

- [x] **[`docs/RELEASE.md`](../RELEASE.md)** — split §2 into **native build** (any distro, floor is
      yours, fine for local artifacts) and **pinned release build** (the container, the only shape
      that gets published). Correct the dependency list per L5.
- [x] **[`installers/linux/README.md`](../../installers/linux/README.md)** — the AppImage's `NOTICE`,
      icon, and the container path.
- [x] **[`site/docs/index.md`](../../site/docs/index.md)** — two claims are currently false and both
      predate this plan: line ~396 says *"Python package | not on PyPI yet"* (it has been on PyPI
      since 1.0.0) and line ~405 says *"Download from Releases"*, linking to a page that is empty.
      Both must be true before the first native release, and the Linux floor from P3 belongs in the
      platform table.

---

## Definition of done

`just check` green; the Windows zip runs in Windows Sandbox and the Linux tarball and AppImage run in
a floor-glibc container, **all three on a machine with no build tooling**; `smoke.sh` accepts an
`.AppImage` and asserts `LICENSE` + `NOTICE` + the staged CRT DLLs; the Linux release artifacts are
built from a Dockerfile in this repository rather than from anyone's machine; and `RELEASE.md`
describes both the native and the pinned path with a dependency list that is correct for the pinned
one.

---

## Out of scope

- **macOS.** `installers/macos/` is a README with no installer behind it, so there is no artifact to
  make portable. When one exists it needs its own floor decision (the deployment target) and its own
  clean-room run — P4's rule is written to cover it in advance.
- **CI.** See the decision log: this image is what a future `release.yml` Linux job runs, but a
  hand-cut release from the container is a complete outcome and 1.0.2 cannot wait for CI.
- **CUDA.** Still an opt-in payload dropped into `~/.knaif/backends`; no CUDA artifact is published
  at v1 and nothing here changes that.
- **A static-musl CPU floor build.** Recorded in [`NATIVE.md`](../NATIVE.md#L431) as a possible
  fast-follow. It would drop the glibc question entirely for CPU-only users, but Vulkan and CUDA need
  glibc plus the vendor driver regardless, so it cannot replace the pinned container — only add a
  fourth artifact. Revisit if the floor decision in P3 proves too narrow in practice.
