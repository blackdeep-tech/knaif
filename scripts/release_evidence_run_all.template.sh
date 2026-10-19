#!/usr/bin/env bash
# knaif <VERSION>, <PLATFORM> evidence, measured as plan docs/plans/2026-10-14-faster-release-evidence.md
# Part I says (the rules a release adopts). COPY this file at the freeze, once per platform, to
# evals/runs/<date>_r<ver>-<platform>_success/run_all.sh, fill in every <...> below, commit it, and
# only then run its first stage. The mechanics live in scripts/release_evidence.sh.
#
# Where each copy runs, from the repo root, ONE STAGE PER INVOCATION, each launched only after the
# owner has approved it with its time budget (nothing in parallel on one machine):
#   windows  the Windows box, Git Bash. Cells CUDA, Vulkan, CPU; L3 CUDA; daemon CUDA.
#   linux    WSL on the same box, from a clone on the Linux disk (never /mnt). Cells CUDA, CPU; no
#            L3; daemon on the CPU. Pull the Windows run's commits (the cold-start samples, the
#            store) into the clone first; commit the Linux run in the clone and fetch it back.
#   macos    the Mac, from a checkout outside the home folder. Cells Metal, CPU (a copy of the
#            artifact without its Metal backend, lanes CPU_LANE); L3 Metal; daemon Metal.
#
#   bash evals/runs/<this folder>/run_all.sh check    preflight only; measures nothing
#   bash evals/runs/<this folder>/run_all.sh setup    install + verify the CUDA payload (not macOS)
#   bash evals/runs/<this folder>/run_all.sh draw     the cold-start samples, once per release, in
#                                                     the first platform's run; commit them
#   bash evals/runs/<this folder>/run_all.sh l3       L3 on the subset, both models x skills (not Linux)
#   bash evals/runs/<this folder>/run_all.sh gpu      L4 subset cells on the platform's GPU backends
#   bash evals/runs/<this folder>/run_all.sh cpu      L4 subset cells, CPU
#   MODEL=4b BACKEND=cuda SKILL=ffmpeg bash evals/runs/<this folder>/run_all.sh live
#                                                     one cell live, after its cold start failed
#   bash evals/runs/<this folder>/run_all.sh daemon   plan equality on the subset
#   bash evals/runs/<this folder>/run_all.sh store    check and count what the store gained
#
# Watch a stage: bash scripts/watch_run.sh evals/runs/<this folder>
#
# ---------------------------------------------------------------------------------------------------
# GATE DECISION RULES, copied verbatim from the plan's Part I (approved by the owner 2026-10-18;
# `test_release_evidence_template.py` keeps this copy equal to the plan). Not to be changed after a
# result: a change is an amendment, recorded below with its reason, before the stage it affects runs.
#
#   **I1. What is measured.** The frozen release artifacts, built by the release scripts, per cell
#   (model × platform × backend × skill). A cell that did not run on its expected device is void.
#
#   **I2. Populations.**
#   - *Model acceptance* (Python, full corpus, S2 floors, `eval-accept`) runs when the planning
#     fingerprint changed since the locked snapshot: the model, prompt, retrieval, generation settings
#     or planner code (D3). It runs before L3 and L4, and its snapshot is re-locked in its own commit
#     with `evalsuite lock` (which records `source_run`). Otherwise the locked snapshot stands.
#   - *L4 cells* measure the committed `data/release_subset.json`; `release-subset --check` must pass
#     at the freeze commit. A skill × platform with no earlier accepted full-corpus native cell
#     measures the full corpus there once instead (D1).
#   - *Safety* runs on the binary over the whole safety corpus in every cell. It is never subsetted
#     and never reused.
#
#   **I3. The L4 bar on the subset** (D2, as `accept-native --population subset` grades it): no row
#   the accepted Python run passed may fail on the binary, and on such a row the binary's file may not
#   score lower than Python's (owner, 2026-10-19); every subset row the Python run graded must still
#   have that utterance and expected outcome in the corpus; `outcome_accuracy` and `avg_knaif_score`
#   within 0.02 of Python's **on the same rows** (the snapshot's `source_run`); coverage 1.0; safety
#   100%. The S2 slice floors do not apply, so slice waivers have nothing to waive: a regressed row
#   goes to the owner with both plans and commands, and a safety miss is never waived.
#
#   **I4. L3** on the subset rows: 0 port bugs, 0 capabilities native lacks, plan disagreement within
#   the 1.2.0 bounds, unchanged (≤ 0.0411 ffmpeg, ≤ 0.0183 documents). *Measured 2026-10-18:* 1.3.0's
#   eight L3 records (Windows and macOS, both models, both skills) had **no** disagreement on the
#   subset's rows; over the full corpus, one documents row each.
#
#   **I5. Planning (B1).** Each cell batch-plans the subset on its own backend (`native --subset
#   --batch-plan`, one model load); each row then runs through the shipped `knaif run` with its plan
#   replayed. A **cold-start sample** of 30 utterances per skill is drawn once per release from the
#   subset (`evalsuite shards --rows 30`, evenly spaced) and committed in the run folder before any
#   result. It runs live in every cell, one process per row, and each live plan must equal its batch
#   plan; rows the unsafe-phrase screen rejects before inference are excluded, as in T0. Any mismatch
#   voids that cell's batch planning, and the cell re-runs live, in full, on the subset (owner,
#   2026-10-18).
#
#   **I6. Across backends (B2).** Stage 1: none. Each cell executes its own plans; execution costs
#   0.1–0.7 s a row, so there is little to save. Stage 2: the same; execution is never reused (owner,
#   2026-10-19: no outcomes table). The cold-start rows, which run live end to end on every backend, are also B2's fresh confirmation
#   sample: one sample, not two (owner, 2026-10-18; E4's list shrinks accordingly).
#
#   **I7. Daemon plan equality (B3).** In-process vs through the daemon, byte-identical, both models,
#   both skills, over the subset's utterances, on the platforms 1.3.0 checked (Windows CUDA, macOS
#   Metal, Linux CPU). The full corpus is used when the daemon or the generation path changed since
#   the last release. A difference goes to the owner before any release claim about the daemon.
#
#   **I8. Reuse between releases (E3), Stage 2 only.** A model reply is reused when its exact prompt,
#   the model sha256, the inference fingerprint, the platform, the backend class and the run settings
#   are unchanged (`native --store`, E1 as amended; the platform added by the owner 2026-10-19, since
#   1.3.0's Linux CPU planned 3–6% of its rows unlike Windows CPU). Outcomes are never reused: every row executes in every release
#   (owner, 2026-10-19). Never reused (E4): safety, the cold-start sample, execution. Only the frozen
#   artifact writes `evals/store`, and each recorded reply is logged with the binary and run that
#   recorded it. The key does not name the compiler toolchain or build flags: the cold-start sample is
#   the only check on a compiler-level change to the arithmetic (owner, 2026-10-19).
#
#   **I9. Reporting (E2).** Each board records per row whether its plan and its outcome were measured
#   in this release or reused from `<release, cell>`. The gate prints both counts per cell, and the
#   release notes say per cell "N measured, M reused from <release>" and the population ("release
#   subset, n utterances" or "full corpus"). In Stage 1 every row is measured.

# ---------------------------------------------------------------------------------------------------
# AMENDMENTS (each with its date and reason, written before the stage it affects runs):
#   none yet
# ---------------------------------------------------------------------------------------------------

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
PLATFORM="<windows|linux|macos>"
VERSION="<VERSION>"                       # e.g. 1.4.0
FREEZE="<FREEZE COMMIT>"
# windows: dist/knaif-$VERSION-windows-x64.zip; linux: <windows-checkout>/dist/knaif-$VERSION-linux-x64.tar.gz
# (the Windows checkout, by its path inside WSL); macos: the CI run's knaif-$VERSION-macos-arm64.zip
ARTIFACT="<PATH OF THE FROZEN ARTIFACT>"
ARTIFACT_SHA256="<SHA256 OF THE FROZEN ARTIFACT>"
LANE="<LANE PREFIX>"                      # eval_backends.yaml lanes <prefix>4b and <prefix>1.7b
CPU_LANE=""                               # macOS only: lanes of the Metal-less copy, e.g. r140-mac-cpu-
COLD_START_DIR="$R"                       # the first platform's run folder; the others name it
MODELS="4b 1.7b"
SKILLS="ffmpeg documents"
GGUF_4B=models/knaif-qwen3-4b-v2-q4_k_m.gguf
SHA_4B=a9c26005e94622d63d1c6e64cb1c1b42084dcc37f6be7d69f8d5967f9c13aab7
NAME_4B=knaif-qwen3-4b-v2
GGUF_1_7B=models/knaif-qwen3-1.7b-v2-q6_k.gguf
SHA_1_7B=d59cad240f5e0f157f093868479cf92132156097394805a9cccd102e14f04ac5
NAME_1_7B=knaif-qwen3-1.7b-v2
L3_BOUND_ffmpeg=0.0411                    # Part I, I4: the 1.2.0 bounds, unchanged
L3_BOUND_documents=0.0183

case "$PLATFORM$VERSION$FREEZE$ARTIFACT$ARTIFACT_SHA256$LANE" in
  *"<"*) echo "fill in the <...> placeholders first" >&2; exit 2 ;;
esac
# shellcheck source=../../../scripts/release_evidence.sh
source scripts/release_evidence.sh
release_stage "${1:?usage: run_all.sh check|setup|draw|l3|gpu|cpu|live|daemon|store}"
