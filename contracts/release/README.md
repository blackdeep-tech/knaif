# `contracts/release/` — what a release supports

No-code contracts describing the **shape** of a knaif release, as opposed to any
particular one.

- **`platforms.yaml`** — the platform support matrix: which OSes are supported, the
  measured runtime floors, artifact name templates, GPU backends, and the external-tool
  and first-run-model caveats. Read by the knaif.org `/download` page; referenced (not
  restated) by [`docs/RELEASE.md`](../../docs/RELEASE.md).

## This directory holds no version numbers

`platforms.yaml` deliberately carries no versions, URLs, or checksums. Those live in
`site/data/release.json`, a snapshot of the latest **published** GitHub release produced
by `just release-data` after publication (RELEASE.md §5).

The split is the point. This file changes when a *support floor* moves — rarely, and as a
deliberate act. `release.json` changes every release. Merging them would mean deriving
download URLs from a version declared in-repo, and the version bump lands **before** the
assets exist ([RELEASE.md](../../docs/RELEASE.md) §5, steps 2→5) — so a derived URL
advertises a download that 404s for as long as the gap lasts.

## The floors are measured

`scripts/check_elf_deps.py` prints the real Linux floor from a built artifact. The binding
constraint is **libstdc++** (`GLIBCXX_3.4.30` / `CXXABI_1.3.13`), not glibc — the artifact
requires only `GLIBC_2.34`, *below* the 2.35 build base. A support table quoting a glibc
number alone is measuring the wrong thing, and is how RHEL 9 gets mis-listed as supported.

Re-measure and update this file when the build container moves; do not infer.
