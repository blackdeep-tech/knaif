#!/usr/bin/env python
"""Publish a knaif fine-tuned GGUF to Hugging Face and update the model manifest.

Admin-only tool (needs a write token for the `blackdeep` org). End-users never run this —
they just `knaif models pull <name>`, which fetches the public file tokenlessly.

What it does, in one command:
  1. SHA-256 + byte-size the local GGUF.
  2. Upload it to the HF repo (default `blackdeep/knaif`) under the manifest's canonical
     `file` name.
  3. Rewrite that model's manifest entry with a COMMIT-SHA-pinned resolve URL (never `main`,
     which can move under a fixed sha256 and break `verify`), the sha256, and size_bytes.
  4. Leave the manifest change as an unstaged git diff for you to review + commit.

Auth: reads `HF_TOKEN` (or `HUGGINGFACE_TOKEN`) from the environment, or from a gitignored
`.env` file at the repo root (`HF_TOKEN=hf_...`); an exported var wins over `.env`. Falls back
to a prior `hf auth login`. The token is admin-only (never needed by end-users) and is never
printed.

The manifest is the source of truth: you pass a model `--name`, and the local GGUF path is
derived from its manifest `file` under `--models-dir` (default `models/`). `--file` overrides
that for an off-convention path. `--all` publishes every manifest model whose local file exists
and isn't already hosted/current — so nothing in `models/` is ever swept up by accident.

Usage:
  export HF_TOKEN=hf_...            # or put HF_TOKEN=hf_... in a .env file, or: hf auth login

  # publish one model (file derived from the manifest):
  uv run --with huggingface_hub --with ruamel.yaml python scripts/publish_model.py --name knaif-qwen3-4b-v1

  # publish everything ready but not yet hosted:
  uv run --with huggingface_hub --with ruamel.yaml python scripts/publish_model.py --all

  # inspect without uploading or touching the manifest:
  uv run --with ruamel.yaml python scripts/publish_model.py --all --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
from pathlib import Path

DEFAULT_REPO = "blackdeep/knaif"
DEFAULT_MANIFEST = "contracts/models/model-manifest.yaml"


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, streamed so multi-GB GGUFs don't load into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_url(repo: str, oid: str, filename: str) -> str:
    """A commit-SHA-pinned HF resolve URL (stable bytes for a fixed sha256)."""
    return f"https://huggingface.co/{repo}/resolve/{oid}/{filename}"


def _yaml():
    from ruamel.yaml import YAML  # lazy: keep pure-import light for tests

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # don't wrap long URLs
    return y


def manifest_file_for(text: str, name: str) -> str:
    """The canonical artifact filename the manifest declares for *name*."""
    data = _yaml().load(text)
    models = data.get("models") or {}
    if name not in models:
        raise KeyError(f"model {name!r} not in manifest 'models' ({', '.join(models)})")
    return str(models[name]["file"])


def updated_manifest(text: str, name: str, *, url: str, sha256: str, size_bytes: int) -> str:
    """Return *text* with model *name*'s url/sha256/size_bytes replaced, comments preserved."""
    y = _yaml()
    data = y.load(text)
    models = data.get("models") or {}
    if name not in models:
        raise KeyError(f"model {name!r} not in manifest 'models' ({', '.join(models)})")
    entry = models[name]
    entry["url"] = url
    entry["sha256"] = sha256
    entry["size_bytes"] = size_bytes
    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def _is_placeholder(val: str | None) -> bool:
    """True when a manifest field is unset — None, empty, or the `TODO` sentinel (mirrors the
    Rust `is_real` check)."""
    return val is None or not str(val).strip() or str(val).strip() == "TODO"


def model_specs(text: str) -> dict[str, dict]:
    """Map each manifest model name to its {file, source, url, sha256} (source/url/sha256 may be None).

    `file` is the public/HF artifact name; `source` (optional) is the local FT-cycle filename that
    backs it when the two differ (local artifacts keep their fine-tune name; the manifest carries
    the public release name).
    """
    data = _yaml().load(text)
    models = data.get("models") or {}
    out: dict[str, dict] = {}
    for name, spec in models.items():
        out[str(name)] = {
            "file": str(spec["file"]),
            "source": None if spec.get("source") is None else str(spec.get("source")),
            "url": None if spec.get("url") is None else str(spec.get("url")),
            "sha256": None if spec.get("sha256") is None else str(spec.get("sha256")),
        }
    return out


def local_name(spec: dict) -> str:
    """The local filename that backs a manifest entry: its `source` (FT-cycle name) if set, else
    `file` (when the local file already matches the public name)."""
    return spec.get("source") or spec["file"]


def needs_publish(spec: dict, local_sha: str) -> bool:
    """Whether a model with local bytes `local_sha` should be (re)published: it has no real
    URL/checksum yet, or its bytes drifted from the recorded checksum."""
    return (
        _is_placeholder(spec.get("url"))
        or _is_placeholder(spec.get("sha256"))
        or spec["sha256"] != local_sha
    )


def _parse_dotenv(text: str) -> dict[str, str]:
    """Parse simple KEY=VALUE lines from a .env file. Skips blanks/comments, tolerates a
    leading `export `, strips surrounding quotes, and splits on the first `=` only."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def _load_dotenv() -> None:
    """Populate os.environ from a `.env` (repo root or cwd) WITHOUT overriding real env vars,
    so an explicitly exported HF_TOKEN still wins. The file is never printed or logged."""
    for cand in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if cand.is_file():
            for k, v in _parse_dotenv(cand.read_text(encoding="utf-8")).items():
                os.environ.setdefault(k, v)
            break


def _upload(file_path: Path, repo: str, path_in_repo: str) -> str:
    """Upload the file to HF and return the resulting commit SHA (oid)."""
    from huggingface_hub import HfApi  # lazy: only needed for the real upload

    _load_dotenv()  # pick up HF_TOKEN from a gitignored .env if not already exported
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    api = HfApi(token=token)  # token=None falls back to a prior `hf auth login`
    commit = api.upload_file(
        path_or_fileobj=str(file_path),
        path_in_repo=path_in_repo,
        repo_id=repo,
        repo_type="model",
    )
    return commit.oid


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--name", help="manifest model key to publish, e.g. knaif-qwen3-4b-v1")
    sel.add_argument(
        "--all",
        action="store_true",
        help="publish every manifest model with a local file that isn't already hosted/current",
    )
    ap.add_argument("--file", help="local GGUF path (default: <models-dir>/<manifest 'file'>)")
    ap.add_argument(
        "--models-dir", default="models", help="dir holding local GGUFs (default models/)"
    )
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"HF repo id (default {DEFAULT_REPO})")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST, help="path to model-manifest.yaml")
    ap.add_argument("--dry-run", action="store_true", help="hash + report, no upload, no rewrite")
    args = ap.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"error: --manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    text = manifest_path.read_text(encoding="utf-8")
    specs = model_specs(text)
    models_dir = Path(args.models_dir)

    # Resolve (name, local_path, precomputed_sha) targets — the manifest is the source of truth,
    # so filenames are derived from it, never hand-typed.
    targets: list[tuple[str, Path, str | None]] = []
    if args.all:
        if args.file:
            print("error: --file cannot be combined with --all", file=sys.stderr)
            return 2
        for name, spec in specs.items():
            lp = models_dir / local_name(spec)
            if not lp.is_file():
                print(f"  skip {name}: no local file at {lp}")
                continue
            lsha = sha256_file(lp)
            if needs_publish(spec, lsha):
                targets.append((name, lp, lsha))
            else:
                print(f"  skip {name}: already published and current")
        if not targets:
            print("Nothing to publish.")
            return 0
    else:
        if args.name not in specs:
            print(
                f"error: model {args.name!r} not in manifest ({', '.join(specs)})", file=sys.stderr
            )
            return 2
        lp = Path(args.file) if args.file else models_dir / local_name(specs[args.name])
        if not lp.is_file():
            print(f"error: local file not found: {lp}", file=sys.stderr)
            return 2
        targets.append((args.name, lp, None))

    published = False
    for name, lp, presha in targets:
        canonical = specs[name]["file"]
        if lp.name != canonical:
            print(
                f"note: local file {lp.name!r} differs from manifest file {canonical!r}; "
                f"uploading as {canonical!r} (the name `pull` will request).",
                file=sys.stderr,
            )
        sha = presha or sha256_file(lp)
        size = lp.stat().st_size

        if args.dry_run:
            print(f"[dry-run] {name}: upload {lp} as {canonical} -> {args.repo}")
            print(f"          sha256={sha} size_bytes={size}")
            continue

        print(f"Uploading {lp} -> {args.repo}/{canonical} …")
        oid = _upload(lp, args.repo, canonical)
        url = resolve_url(args.repo, oid, canonical)
        text = updated_manifest(text, name, url=url, sha256=sha, size_bytes=size)
        published = True
        print(f"  published {name}: {url}")

    if args.dry_run:
        print("(dry-run: no upload performed, manifest unchanged)")
        return 0
    if published:
        manifest_path.write_text(text, encoding="utf-8")
        names = ", ".join(n for n, _, _ in targets)
        print(
            f"\nManifest updated. Review and commit:\n"
            f"  git add {manifest_path} && git commit -m 'models: publish {names}'"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
