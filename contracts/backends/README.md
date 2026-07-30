# `contracts/backends/` — language-neutral loadable-backend contract

Home of the **no-code, cross-language backend manifest** (`backend-manifest.yaml`): the opt-in GPU
backend payloads `knaif backend install` fetches into the directory the runtime scans
(`~/.knaif/backends`, or `$KNAIF_BACKENDS_DIR`). Records per-platform file lists with a per-file
SHA-256 and source release tag, the driver floor the first-run nudge gates on, and the knaif release
the payload is ABI-bound to. Read by the shared `knaif-models` `BackendStore`.

It is a **bill of materials, not a catalog**, and deliberately stricter than
[`contracts/models/`](../models/): a model manifest may be forgiving about versions, a backend
manifest may not. A `ggml` lib whose ABI does not match the binary loading it is undefined behaviour
that presents to a user as a driver bug. The file header explains the five schema differences that
follow from that.

Backends are **native-only** — the Python runtime does not manage them, so the Python side carries a
release-readiness guard (`python/core/tests/test_backend_manifest_release_ready.py`) rather than a
second reader.

`contracts/` holds **no-code, language-neutral contracts only** — skill bundles live at root
`skills/`, not here.

See [`docs/NATIVE.md`](../../docs/NATIVE.md) §5.3 (loadable backends) and
[`docs/RELEASE.md`](../../docs/RELEASE.md) (publishing the payload assets).
