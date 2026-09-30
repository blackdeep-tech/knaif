"""What is on this machine: models, builds, skills.

Three rules the selectors depend on, each of which exists because the alternative misleads:

* **Nothing resolvable is hidden, and nothing missing is silently dropped.** A model whose GGUF
  is absent is listed, greyed, with the path it looked for — otherwise "why is that arm gone?"
  has no answer.
* **A build is labelled by what it reports, not by where it sits.** `knaif backend list --json`
  is the source; a directory named `release-vulkan` holding a CUDA build is flagged, not
  believed.
* **Asking costs nothing.** The label call loads no model, so it runs on every inventory and
  there is no cache to go stale when a rebuild replaces a binary.
* **A build older than the code it compiles says so.** A binary is judged against the newest
  native source; offering a stale one silently made a fixed engine bug look unfixed.

See docs/plans/2026-09-21-skill-prompt-workbench.md (D1e, D3, T5).
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_GROUPS = ("published", "curated", "experimental", "missing")


@dataclass(frozen=True)
class ModelEntry:
    """One `backends:` stanza that names a GGUF."""

    name: str
    path: str
    resolved: bool
    group: str = "experimental"

    @property
    def label(self) -> str:
        if not self.resolved:
            return f"{self.name}  —  MISSING: {self.path}"
        return f"{self.name}  ({Path(self.path).name})"


@dataclass(frozen=True)
class BuildEntry:
    """One native build directory, described by the binary inside it."""

    path: Path
    version: str | None = None
    features: list[str] = field(default_factory=list)
    dynamic_backends: bool = False
    backends_dir: str | None = None
    platform: str | None = None
    #: The binary's modification time — when it was built, as far as the filesystem knows.
    built_at: float | None = None
    #: The newest native source changed after this build, repo-relative. None when fresh or
    #: unknown.
    newer_source: str | None = None

    @property
    def stale(self) -> bool:
        return self.newer_source is not None

    @property
    def kind(self) -> str | None:
        """The GPU backend this build actually carries, if any."""
        for feature in ("cuda", "vulkan"):
            if feature in self.features:
                return feature
        if "llama" in self.features:
            return "cpu"
        return None

    @property
    def mislabelled(self) -> bool:
        """True when the directory name claims a kind the binary does not have.

        Not fatal — the binary is still runnable and still labelled correctly. It is surfaced so
        an operator can see that `release-vulkan/` is holding something else, which is exactly
        the confusion the per-kind profiles exist to prevent.
        """
        directory = self.path.parent.name
        for feature in ("cuda", "vulkan"):
            if directory.endswith(f"-{feature}") and feature not in self.features:
                return True
        return False

    @property
    def label(self) -> str:
        where = self.path.parent.name
        if not self.features:
            text = f"{where}  —  unknown build (no `backend list --json`)"
        else:
            shown = ", ".join(self.features)
            flag = "  ⚠ directory name disagrees" if self.mislabelled else ""
            text = f"{where}  ({shown}){flag}"
        if self.built_at is not None:
            text += f"  built {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.built_at))}"
        if self.newer_source is not None:
            text += f"  ⚠ stale — {self.newer_source} changed after this build"
        return text


def group_models(
    *,
    backends: dict[str, str],
    published: set[str],
    curated: set[str],
    resolves: Callable[[str], bool],
) -> dict[str, list[ModelEntry]]:
    """Sort the eval config's model-bearing stanzas into the four dropdown groups.

    `backends` maps stanza name -> GGUF path. Stanzas with no path (`mock`, the ollama arm) are
    not models and never appear here; they are *inference* arms, which is a different row.

    Membership is decided by the stanza name OR the GGUF's stem, because a stanza keeps its
    FT-cycle key (`qwen3-4b-sft-v3-flat-q4`) while the file on disk carries the public name —
    one model, two identities, and the selector should group on either.
    """
    grouped: dict[str, list[ModelEntry]] = {name: [] for name in _GROUPS}
    for name, path in sorted(backends.items()):
        if not path:
            continue
        ok = resolves(path)
        stem = Path(path).stem
        identities = {name, stem, stem.rsplit("-q", 1)[0]}
        if not ok:
            group = "missing"
        elif identities & published:
            group = "published"
        elif identities & curated:
            group = "curated"
        else:
            group = "experimental"
        grouped[group].append(ModelEntry(name=name, path=path, resolved=ok, group=group))
    return grouped


def parse_build_label(binary: Path, reported: str) -> BuildEntry:
    """Describe a build from its own `backend list --json` output.

    An empty or unparseable report is not a failure: binaries predating that command still run,
    and hiding them would be worse than listing one as unknown.
    """
    try:
        doc: dict[str, Any] = json.loads(reported) if reported.strip() else {}
    except ValueError:
        doc = {}
    return BuildEntry(
        path=binary,
        version=doc.get("version"),
        features=list(doc.get("built_with") or []),
        dynamic_backends=bool(doc.get("dynamic_backends")),
        backends_dir=doc.get("backends_dir"),
        platform=doc.get("platform"),
    )


def scan_builds(
    root: Path | str,
    *,
    exe_name: str = "knaif",
    declared: Iterable[str | Path] = (),
) -> list[Path]:
    """Every native binary worth offering: `target/release*/` plus declared directories.

    `target/release*/` covers plain `release` and every `release-<kind>` profile, which is where
    a kind's binary and its staged llama/ggml libs live. `debug` is deliberately excluded — a
    debug llama.cpp build is not something anyone should be judging a model on.

    Declared directories come from `workbench.local.yaml`; one that has since been deleted is
    skipped rather than raising, because a machine-local config outlives what it names.
    """
    root = Path(root)
    found: list[Path] = []
    for directory in sorted((root / "target").glob("release*")):
        candidate = directory / exe_name
        if candidate.is_file():
            found.append(candidate)
    for raw in declared:
        directory = Path(raw)
        if not directory.is_absolute():
            directory = root / directory
        candidate = directory / exe_name
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    return found


#: What a native binary compiles, relative to the repo root. Skill YAML and data are read at
#: runtime, so changing them needs no rebuild and must not mark a build stale.
_NATIVE_SOURCE_GLOBS = (
    "native/**/*.rs",
    "native/**/Cargo.toml",
    "apps/**/*.rs",
    "apps/**/Cargo.toml",
    "skills/*/native/**/*.rs",
    "skills/*/native/**/Cargo.toml",
    "Cargo.toml",
    "Cargo.lock",
)


def newest_native_source(root: Path | str) -> tuple[Path, float] | None:
    """The most recently modified native source file (repo-relative) and its mtime.

    Modification time is a heuristic: a branch switch touches files without changing what a
    build would produce, so this can flag a build that is in fact current. That errs the safe
    way — a spurious rebuild costs seconds, a silently stale binary cost an afternoon.
    """
    root = Path(root)
    newest: tuple[Path, float] | None = None
    for pattern in _NATIVE_SOURCE_GLOBS:
        for path in root.glob(pattern):
            if "target" in path.relative_to(root).parts or not path.is_file():
                continue
            mtime = path.stat().st_mtime
            if newest is None or mtime > newest[1]:
                newest = (path.relative_to(root), mtime)
    return newest


def describe_build(
    binary: Path,
    *,
    timeout_s: float = 20.0,
    newest_source: tuple[Path, float] | None = None,
) -> BuildEntry:
    """Ask a binary what it is, and whether the code it compiles changed after it was built.

    Never raises — an unrunnable build is listed as unknown.
    """
    try:
        proc = subprocess.run(
            [str(binary), "backend", "list", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        entry = parse_build_label(binary, proc.stdout)
    except (OSError, subprocess.SubprocessError):
        entry = parse_build_label(binary, "")
    try:
        built_at: float | None = binary.stat().st_mtime
    except OSError:
        built_at = None
    newer = None
    if built_at is not None and newest_source is not None and newest_source[1] > built_at:
        newer = newest_source[0].as_posix()
    return dataclasses.replace(entry, built_at=built_at, newer_source=newer)


def load_local_config(root: Path | str) -> dict[str, Any]:
    """`workbench.local.yaml` — gitignored, machine-local, binaries only.

    Absent is the normal case: the scan of `target/release*/` already finds whatever was built
    here. The file exists for builds kept somewhere else.
    """
    import yaml

    path = Path(root) / "workbench.local.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def scan(root: Path | str = ".") -> dict[str, Any]:
    """Everything the selectors need, measured now rather than remembered."""
    import platform

    import yaml

    root = Path(root).resolve()
    exe_name = "knaif.exe" if platform.system() == "Windows" else "knaif"

    config = yaml.safe_load((root / "eval_backends.yaml").read_text(encoding="utf-8")) or {}
    backends = config.get("backends") or {}
    model_paths = {
        name: ((stanza or {}).get("options") or {}).get("path", "")
        for name, stanza in backends.items()
    }
    inference_arms = sorted(
        name
        for name, stanza in backends.items()
        if (stanza or {}).get("backend") in {"llama_cpp", "ollama"}
    )

    manifest = yaml.safe_load(
        (root / "contracts" / "models" / "model-manifest.yaml").read_text(encoding="utf-8")
    )
    curated_doc = yaml.safe_load((root / "models.yaml").read_text(encoding="utf-8")) or {}

    models = group_models(
        backends=model_paths,
        published=set(manifest.get("models") or {}),
        curated=set(curated_doc.get("models") or {}),
        resolves=lambda p: (root / p).is_file(),
    )

    local = load_local_config(root)
    newest = newest_native_source(root)
    builds = [
        describe_build(binary, newest_source=newest)
        for binary in scan_builds(root, exe_name=exe_name, declared=local.get("binaries") or [])
    ]

    from knaif import list_skills

    skills = {}
    for name in list_skills():
        doc = yaml.safe_load((root / "skills" / name / "skill.yaml").read_text(encoding="utf-8"))
        native = (doc.get("runtimes") or {}).get("native") or {}
        skills[name] = native.get("status", "unknown")

    return {"models": models, "builds": builds, "skills": skills, "inference": inference_arms}


def summary(inv: dict[str, Any]) -> str:
    """The inventory cell's output. States what is missing as plainly as what is present."""
    lines: list[str] = []
    models = inv["models"]
    resolved = sum(len(models[g]) for g in ("published", "curated", "experimental"))
    lines.append(
        f"models    {resolved} resolved   "
        f"{len(models['published'])} published · {len(models['curated'])} curated · "
        f"{len(models['experimental'])} experimental"
    )
    if models["missing"]:
        lines.append(f"          {len(models['missing'])} missing — listed, greyed, never dropped")

    lines.append(f"builds    {len(inv['builds'])}")
    for build in inv["builds"]:
        lines.append(f"          {build.label}")
    lines.append(f"inference {', '.join(inv['inference'][:4])}… ({len(inv['inference'])} arms)")
    lines.append("skills    " + " · ".join(f"{n} (native: {s})" for n, s in inv["skills"].items()))
    return "\n".join(lines)
