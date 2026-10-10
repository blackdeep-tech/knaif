# Media runtime: shipping ffmpeg, and a mobile media skill

**Status:** Draft · **Created:** 2026-10-03 · **Completed:** —
**Owner:** ffmpeg skill / packaging · **Ref:** [code-signing](2026-07-27-code-signing.md) ·
[TODO.md](../TODO.md) (*Media runtime and mobile*)
**Release:** —

> **Status note:** parked 2026-10-03 by the owner ("we will get back to it"). This records a design
> discussion, not an approved plan: nothing here is decided except where marked **Owner**. Do not
> implement until the owner reopens it. Two threads: (A) how desktop users get a working, signed
> ffmpeg; (B) how media editing reaches iOS/Android.

**Goal:** Decide how knaif ships ffmpeg on desktop (signing, licensing, monorepo layout) and how a
separate mobile media skill reuses the ffmpeg skill's deterministic engine.

## Context

- Signing knaif's own binaries (Azure Artifact Signing, 1.2.1) fixed SmartScreen / Smart App Control
  for knaif. ffmpeg is still a third-party install (`winget install Gyan.FFmpeg` etc., see
  `skills/ffmpeg/SPEC.md`), so the ffmpeg skill still depends on a binary we neither ship nor sign.
- The owner proposed packaging the ffmpeg skill as a separately licensed, dynamically loaded library
  (ffmpeg + the skill's steps, maybe a LoRA later), downloadable or embedded in the installer.

## Thread A — desktop: a signed "ffmpeg runtime pack"

### Findings

- **LGPL is not incompatible with Apache-2.0; how ffmpeg is used decides.** Both runtimes run ffmpeg
  as a separate process (`skills/ffmpeg/native/src/exec.rs`, `skills/ffmpeg/python/_deps.py`). Shipping
  an ffmpeg executable next to knaif is aggregation; knaif stays Apache-2.0. This is the existing
  dependency-license rule: subprocess is fine, in-process linking of copyleft is the hard line.
- **The skill needs a GPL ffmpeg, not an LGPL one.** Its commands use `libx264` (71 places) and
  `libx265` (12), both GPL, plus `-crf`. An LGPL-only build has neither, so it would break most commands.
- **A dynamic-library plugin is the wrong tool for this problem:**
  - linking a GPL ffmpeg in-process makes the plugin a GPL combined work;
  - it means rewriting the skill from rendering command lines to calling the libav* API, losing the
    command-parity contracts and eval baselines;
  - Rust has no stable ABI, and the native `Step`/`Intent` traits do not exist yet (audit F11);
  - macOS library validation loads only dylibs signed by our own Team ID.
  If skill plugins are wanted for other reasons, prefer out-of-process plugins (JSON over stdio).

### Recommendation (not decided)

- [ ] **A0 — Smart App Control experiment first.** In the Windows Sandbox (which enforces Smart App
  Control), install `Gyan.FFmpeg` via winget and let signed knaif run it. If cloud reputation lets it
  through, the pack is a convenience, not a blocker.
- [ ] **A1 — Build the pack.** `ffmpeg`/`ffprobe` from pinned source, GPL build reduced to the
  codecs and filters the skill uses (~20–40 MB rather than 100+), signed with the Blackdeep
  certificate.
- [ ] **A2 — Publish separately.** Its own release asset and tag (e.g. `ffmpeg-runtime-7.x-1`) on the
  same repo, with the matching source tarballs and build script (GPL source obligation), license
  texts and a NOTICE entry.
- [ ] **A3 — Installer.** The knaif installer stays Apache-only and downloads the pack when the
  ffmpeg component is ticked. Bundling is also legal (aggregation) but downloading keeps the
  installer clean.
- [ ] **A4 — Binary resolution.** Bundled pack first, then `PATH`, in both runtimes. PyPI users keep
  their own ffmpeg. Probe `-encoders` at startup, since bundled and system builds differ.
- [ ] **A5 — macOS / Linux.** macOS: sign + notarize the pack like knaif, or `depends_on "ffmpeg"` in the
  brew tap (Homebrew's build has x264). Linux: `.deb` `Recommends: ffmpeg`; the AppImage/tarball may
  bundle a static pack. Fedora/RHEL `ffmpeg-free` lacks libx264/libx265, so the skill likely fails
  there today; the pack fixes it.

**Monorepo:** no separate repo and no separate license for the skill's code (it is all ours and stays
Apache-2.0). Only the ffmpeg binaries are GPL. Put the build scripts under something like
`third_party/ffmpeg-runtime/` with their own README and license notes; fetch the ffmpeg source in CI
instead of vendoring it. A separate repo pays off only to keep GPL source tarballs off the main repo
or to give the pack a fully independent release cadence. Forks inherit only the Apache code and can
ship the pack, use a system ffmpeg, or drop the skill.

### Risks

- **Patents:** distributing H.264/H.265/AAC encoders makes us the distributor (mainly a US concern).
  A question for a lawyer, not a licensing one.
- **Security updates:** ffmpeg parses untrusted media; every ffmpeg CVE fix means a new pack release.
- **Reverses a decision:** the 2026-07-07 installer decision says third-party tools are never bundled
  and come from their own installers. This deliberately reverses it for ffmpeg — owner's call.
- Antivirus false positives on ffmpeg builds; signing helps but does not guarantee.
- **LGPL-only alternative (later):** swap `libx264` for `h264_mf`/nvenc/qsv/VideoToolbox. Lower
  quality, no `-crf`, commands differ per platform, every eval re-accepted.

## Thread B — mobile (iOS / Android)

### Findings

- The pack does not carry over. iOS forbids launching other programs; Android allows it only for
  binaries packaged as `lib*.so`, which is fragile. ffmpeg-kit (the usual wrapper) was retired in 2025.
- In-process ffmpeg on mobile means linking: a GPL build makes the app GPL (and the App Store's terms
  are widely considered incompatible with GPL); an LGPL build as a dynamic framework / `.so` is
  possible but has no libx264 and relies on hardware encoders without `-crf`.
- Use the platform APIs instead: **AVFoundation** (iOS) and **Media3 Transformer** (Android). Their
  H.264/HEVC/AAC encoders are licensed by Apple/Google, which also removes the patent question.

### Owner decision (2026-10-03): a separate mobile skill, not the ffmpeg skill

The differences are too big for one skill:
- utterances differ (gallery / share-sheet selection, no file paths);
- capabilities differ both ways (mobile lacks MKV/MP3/reverse; adds photo library, Live Photos,
  HDR, sharing);
- the app's own storage is the only sandbox;
- users run the 1.7B there, so the bar and scores differ.

### What to share anyway

Most of the ffmpeg skill's effort is not ffmpeg-specific. Extract it into a shared Rust crate (e.g.
`knaif-media-core`) used by both skills, and keep ffmpeg command rendering in the desktop skill:
- time parsing ("the last 5 seconds", "last frame") and trim normalization;
- resize math (fit, aspect, crop/pad) and the platform profiles (WhatsApp/Instagram limits);
- vocabulary and argument coercion, and the clarify rules.

The engine already has the seam: intent → `Recipe` (`skills/ffmpeg/native/src/engine.rs`) →
`build_flags` / `render_command`. The `Recipe` still leaks ffmpeg in `video.encoder`, `crf`, `preset`
and `pixel_format`; those become neutral terms (`codec: h264`, `quality: visually_good`) mapped by
each executor.

**Corpus:** derived, not shared. Seed the mobile `eval.jsonl` from the ffmpeg one (paths → selection
references; drop rows the phone cannot do; add mobile-only rows). The ffmpeg prompt already uses
`selected_videos` / `current_file`. Measured on 2026-10-03 against `skills/ffmpeg/data/eval.jsonl`
(328 rows):
- 106 clarify/reject rows are plan-level and carry over as they are;
- 175 of 222 plan rows (mp4, H.264/HEVC/AAC, jpg) have no obvious platform gap;
- ~47 rows need what the phones lack: MP3 output 14, MKV/WebM 16, `reverse_video` 11, VP9/AV1
  encoding 5, GIF/Opus/FLAC 6;
- 60 rows grade ffmpeg command text (`filters`, `flags`, `encoder`) and must become output
  properties (duration, dimensions, codecs). That also makes desktop grading more honest.

**Tool names:** keep a name such as `trim_video` identical where the meaning is identical (the
training rows of both skills land in one combined dataset); never reuse a name with a different
argument shape.

### Open questions for the owner

1. **Lifecycle.** AGENTS.md requires Python acceptance before any native port, but Python cannot call
   AVFoundation/Media3. Choose: a Python executor that imitates the phone (desktop ffmpeg restricted to
   the phone's capabilities) for the fast loops plus on-device acceptance, or a written exception
   making the device run the reference. AGENTS.md and `check-gate` must say which.
2. **Training.** Both skills' rows in one dataset for the 1.7B ("trim with a path" vs "trim the selected
   item"). Measure in the first cycle that includes mobile rows; the desktop snapshot is the gate.
3. A mobile runtime does not exist yet: Rust core + llama.cpp on device, a model that fits (the 1.7B),
   and Swift/Kotlin executors behind a callback (UniFFI).

### Suggested order

- [ ] **B1 — Extract `knaif-media-core`** from the ffmpeg skill and make the `Recipe` backend-neutral.
  Desktop-only, no behaviour change, guarded by existing tests and parity checks. Worth doing even if
  mobile slips.
- [ ] **B2 — Convert the 60 command-text criteria** to output properties and tag every row with what it
  needs (`mp3`, `reverse`, `mkv`, …). Also desktop-useful.
- [ ] **B3 — Write the mobile skill's SPEC:** tool surface, selection model, sandbox rules, per-platform
  capability list, and the answer to open question 1.
- [ ] **B4 — Derive the first mobile corpus**, then add mobile-only rows.
- [ ] **B5 — Prototype one executor** (iOS first: AVFoundation compositions cover trim, concat, speed,
  volume) against the in-scope rows, with the bar written before the first run.
