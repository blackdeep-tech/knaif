# Linux release build environment for knaif.
#
# WHAT THIS IS FOR. The artifact's runtime floor is whatever glibc the build machine had, and
# nothing warns you about it. A maintainer on Arch (glibc 2.42) or Fedora 42 (2.41) who builds a
# release natively ships a binary that starts on almost nothing. This image fixes the floor at
# glibc 2.35 regardless of who cuts the release.
#
# WHAT THIS IS NOT FOR. Contributors never need it. `cargo build`, `just check`, `just test-native`
# and `installers/package.sh` all keep working natively on any distro — the floor is then simply
# your own. This image is used only for artifacts that get PUBLISHED.
#
# The image holds the ENVIRONMENT ONLY. Source arrives per run (cloned tag for a release, or a
# mounted worktree for development), so the image stays valid across releases and its layers stay
# cached.
#
#   docker build -t knaif-linux-build installers/linux/
#   just package-linux            # release: clones the tag inside the container
#   just package-linux --dev      # development: mounts the working tree
#
# ---------------------------------------------------------------------------------------------
# Reproducibility: all four inputs are pinned. Bump them deliberately, in their own commit.
# ---------------------------------------------------------------------------------------------
#   1. base image      by sha256 digest (below)
#   2. apt packages    via snapshot.ubuntu.com at a fixed timestamp — pinning the base digest
#                      alone does NOT pin apt, which would otherwise resolve to whatever the
#                      archive serves on the day you build
#   3. shaderc         exact version; LunarG's repo retains ~15 historical versions, so this pin
#                      does not rot the way a rolling apt pin would
#   4. appimagetool    tagged release + verified sha256, not the rolling `continuous` build
#
# THE COST OF PINNING, STATED PLAINLY: a fixed apt snapshot does not receive security updates.
# That is the same trade this project accepted for the app-local VC++ runtime on Windows, and it
# has the same remedy — bump deliberately and rebuild. Refresh APT_SNAPSHOT at least at every
# release, and immediately if a CVE lands in something here. Reproducibility and patching pull in
# opposite directions; this file chooses reproducibility and says so rather than pretending
# otherwise.

FROM ubuntu:22.04@sha256:0e0a0fc6d18feda9db1590da249ac93e8d5abfea8f4c3c0c849ce512b5ef8982

# glibc 2.35 — the floor this image exists to fix. Ubuntu 22.04 LTS reaches Ubuntu 22.04+,
# Debian 12+, Fedora 36+ and Mint 21. RHEL/Rocky/Alma 9 is glibc 2.34 and is knowingly excluded;
# moving to an EL9 base would mean rewriting this whole layer for dnf.
ARG APT_SNAPSHOT=20260727T000000Z
ARG SHADERC_VERSION=2025.2~rc1-1lunarg22.04-1
# Vulkan headers/loader come from LunarG, not Ubuntu — 22.04's are far too old to build against.
ARG VULKAN_SDK_VERSION=1.4.313.0~rc1-1lunarg22.04-1
ARG SPIRV_HEADERS_VERSION=1.6.1+1.4.313.0~rc1-1lunarg22.04-1
ARG GLSLANG_VERSION=15.3.0~rc1-1lunarg22.04-1
ARG RUST_VERSION=1.96.0
ARG APPIMAGETOOL_VERSION=1.9.1
ARG APPIMAGETOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# --- 1a. Bootstrap TLS before apt can reach the snapshot ---------------------------------------
#
# snapshot.ubuntu.com is HTTPS-only (plain http 301-redirects to it) and the minimal ubuntu image
# ships **no CA bundle**, so apt cannot fetch from the snapshot until ca-certificates exists —
# and ca-certificates is itself installed by apt. Ubuntu's own default sources are http and
# GPG-signed, which is precisely how apt is designed to bootstrap: integrity comes from the
# repository signature, not from TLS.
#
# The version installed here is whatever the live archive serves, so it is replaced from the
# pinned snapshot in the next layer — ca-certificates appears in BOTH package lists deliberately.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates

# --- 1b. Pin apt to a snapshot, then install the stock build dependencies ----------------------
#
# The Vulkan packages are NOT installed here — see the LunarG layer below. Ubuntu 22.04 ships
# Vulkan headers 1.3.204 (early 2022) and llama.cpp's Vulkan backend does not compile against
# them: VkPhysicalDeviceCooperativeMatrixFeaturesKHR, LayerSettingsCreateInfoEXT and
# vk::DriverId::eMesaDozen are all absent, and the build dies with dozens of "not declared in this
# scope" errors deep in ggml-vulkan.cpp. This is a real cost of choosing an older base for the
# glibc floor, and it is invisible until a full build runs.
#
# CUDA is likewise absent: the toolkit would add 3-5 GB to serve an artifact this image does not
# produce. The CUDA payload IS containerised now, in the separate image this comment used to
# anticipate — see installers/linux/Dockerfile.cuda.
#
# libclang-dev is the only piece of LLVM involved — llama-cpp-sys-2 uses bindgen, which dlopens
# libclang.so at build time.
RUN set -eux; \
    printf 'deb https://snapshot.ubuntu.com/ubuntu/%s jammy main restricted universe multiverse\n\
deb https://snapshot.ubuntu.com/ubuntu/%s jammy-updates main restricted universe multiverse\n\
deb https://snapshot.ubuntu.com/ubuntu/%s jammy-security main restricted universe multiverse\n' \
      "$APT_SNAPSHOT" "$APT_SNAPSHOT" "$APT_SNAPSHOT" > /etc/apt/sources.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      build-essential pkg-config cmake ninja-build patchelf \
      libclang-dev \
      libfuse2 \
      binutils file git curl ca-certificates gnupg python3 zstd; \
    rm -rf /var/lib/apt/lists/*
# ca-certificates is repeated from 1a on purpose: this pass installs the SNAPSHOT's version, so
# the bootstrap does not leave an unpinned package behind.
# `libfuse2` NOT `libfuse2t64` — the t64 suffix arrived with 24.04's 64-bit time_t transition and
# does not exist here. `binutils` + `file` are for the ELF dependency audit (readelf/objdump), not
# for the build itself.

# --- 2. The Vulkan build inputs, from LunarG rather than Ubuntu -------------------------------
#
# Two things Ubuntu 22.04 cannot supply:
#   - HEADERS NEW ENOUGH. Its 1.3.204 headers predate the Vulkan features llama.cpp uses (see
#     above). LunarG's 1.4.313.0 headers and loader replace them.
#   - glslc AT ALL. It is not packaged by Ubuntu, and llama.cpp compiles its Vulkan shaders with
#     it; without it cmake fails at configure with
#     "Could NOT find Vulkan (missing: ... glslc)". It comes from LunarG's `shaderc`.
#
# This is still NOT the full ~1 GB Vulkan SDK. The validation layers, gfxreconstruct, vkconfig,
# dxc and slang are all deliberately left out — they are for people *writing* Vulkan code, and
# nothing in the build or the shipped artifact touches them. Every version is pinned; LunarG
# retains historical versions, so these pins do not rot.
RUN set -eux; \
    curl -fsSL https://packages.lunarg.com/lunarg-signing-key-pub.asc \
      -o /etc/apt/trusted.gpg.d/lunarg.asc; \
    echo "deb https://packages.lunarg.com/vulkan jammy main" > /etc/apt/sources.list.d/lunarg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      "vulkan-headers=${VULKAN_SDK_VERSION}" \
      "libvulkan-dev=${VULKAN_SDK_VERSION}" \
      "libvulkan1=${VULKAN_SDK_VERSION}" \
      "spirv-headers=${SPIRV_HEADERS_VERSION}" \
      "glslang-tools=${GLSLANG_VERSION}" \
      "shaderc=${SHADERC_VERSION}"; \
    rm -rf /var/lib/apt/lists/*; \
    glslc --version; \
    grep -m1 VK_HEADER_VERSION_COMPLETE /usr/include/vulkan/vulkan_core.h

# --- 3. appimagetool --------------------------------------------------------------------------
#
# A tagged release with a verified checksum, rather than the rolling `continuous` tag which would
# make two builds of this Dockerfile silently different. It is itself an AppImage;
# installers/linux/build-appimage.sh invokes it with --appimage-extract-and-run, so no FUSE device
# or container privileges are required.
#
# DO NOT BE ALARMED that the installed binary reports "continuous build ... 2025-12-04": upstream
# tagged 1.9.1 on 2025-11-18 and then RE-UPLOADED the asset onto that tag on 2025-12-04, so the
# release carries a continuous build's version string. This is exactly why the pin is a checksum
# and not just a tag — a tag alone would have silently handed us different bytes. If upstream
# replaces the asset again, sha256sum fails the build loudly instead of drifting, which is the
# behaviour we want; bump APPIMAGETOOL_SHA256 deliberately when that happens.
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/appimagetool \
      "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"; \
    echo "${APPIMAGETOOL_SHA256}  /usr/local/bin/appimagetool" | sha256sum -c -; \
    chmod +x /usr/local/bin/appimagetool

# --- 4. Rust, at the version the workspace pins -----------------------------------------------
#
# rust-toolchain.toml pins 1.96.0, so installing anything else would just make cargo download
# 1.96.0 on first use — baking it in keeps the toolchain in the layer instead of in every run.
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo PATH=/usr/local/cargo/bin:$PATH
# `--component` takes ONE value; repeating the flag is the only way to pass several. Passing
# `--component rustfmt clippy` makes rustup-init reject `clippy` as a stray positional.
# rustfmt and clippy are installed because rust-toolchain.toml lists them: without them rustup
# would fetch them on first use inside the container, turning an offline build into a network one.
RUN set -eux; \
    curl -fsSL https://sh.rustup.rs | sh -s -- \
      -y --no-modify-path --profile minimal \
      --default-toolchain "${RUST_VERSION}" \
      --component rustfmt --component clippy; \
    chmod -R a+w "$CARGO_HOME" "$RUSTUP_HOME"; \
    rustc --version; cargo --version

# Cargo's registry and the build tree are mounted as volumes by the just recipe so llama.cpp is
# not recompiled on every run; both are world-writable above so the container can run as the
# invoking UID without root-owning the host cache.

WORKDIR /src
