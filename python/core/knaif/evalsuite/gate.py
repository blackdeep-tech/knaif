"""G1/G2: the gate — a skill may not claim more than its evidence supports.

`runtimes.native.status` used to be a word anyone could type. This turns it into a claim that
something checks: `supported` requires an L4 acceptance record, `parity` requires an L3 run, and
either is invalid once the tree has moved underneath it.

Three design points, each of which exists because the obvious alternative is wrong:

* **Status is derived from valid evidence, never decremented.** A change to the Python core or
  the shared contracts invalidates L3/L4 *and* L1/L2 — so demoting `supported` to `parity` would
  assert a second claim whose evidence had just expired too. A skill whose evidence is all stale
  is `in-progress`, whatever it used to be.
* **The evidence tuple includes the shared things, because those are what get forgotten.**
  `planner.py`, `prompt.py` and `registry.py` change plans for every skill without touching any
  skill bundle; a `models.yaml` edit changes results with every other hash unchanged; and
  "success" is a moving target, so the verifier's *implementation* is hashed, not its name.
* **A released artifact's record is kept, marked as applying to that artifact.** It stops being
  a claim about `main` without becoming a lie about what shipped — which is what release support
  needs in order to answer "what was true for 1.1.0?".

See `contracts/release/native_status.yaml` for the status definitions and
`docs/plans/2026-09-10-skill-quality-lifecycle.md` (G1, G2).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .matrix import CELL_LAYERS, load_matrix, required_cells
from .outcomes import POLICY_VERSION
from .redact import redact_local_paths

STATUS_CONTRACT = Path("contracts/release/native_status.yaml")
PLATFORMS_CONTRACT = Path("contracts/release/platforms.yaml")
ACCEPTANCE_DIR = Path("evals/acceptance")
MODEL_MANIFEST = Path("contracts/models/model-manifest.yaml")

#: Evidence a measurement brings for itself; see `native_status.yaml`. `policy` and `model` are
#: also derivable from the tree (the code's POLICY_VERSION, the manifest hash of the skill's
#: `recommended_model`); `native_binary` only when a binary is handed to the gate.
RUN_SCOPED = ("model", "native_binary", "policy")

#: Ordered weakest → strongest, so a derived status is a max over satisfied ones.
STATUS_ORDER = ("in-progress", "parity", "supported")


@dataclass
class LayerState:
    """One layer's evidence and why it is in that state.

    `failing` is a separate state from `pending` on purpose: "we never measured it" and "we
    measured it and it did not pass" are different facts, and only the first is fixed by
    running something. Both are equally unable to support a status claim.
    """

    layer: str
    state: str  # "valid" | "excepted" | "failing" | "stale" | "pending"
    detail: str = ""


@dataclass
class SkillGate:
    skill: str
    declared: str
    derived: str
    layers: list[LayerState] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _without_native_status_claim(text: str) -> str:
    """`skill.yaml` as canonical JSON of its parsed content, `runtimes.native.status` masked.

    That value is the claim the evidence justifies (`in-progress` / `parity` / `supported`);
    nothing reads it at run time. Hashed, flipping it to `supported` after R5c staled every L3/L4
    record that justified the flip, so no skill could reach the one release-eligible status.

    Masked in the parsed document, at exactly that path, so flow style, anchors and quoting cannot
    widen it (a line-based mask blanked a whole `native: {status: …, crate: …}` mapping; Codex
    audit, 2026-09-28). Hashing the parsed content also means a comment-only edit no longer stales
    evidence, which it never should have. Unparseable YAML is hashed as text.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    native = (doc.get("runtimes") or {}).get("native") if isinstance(doc, dict) else None
    if isinstance(native, dict) and "status" in native:
        doc = {**doc, "runtimes": {**doc["runtimes"], "native": {**native, "status": "<claim>"}}}
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str)


def _sha256_source(path: Path) -> str:
    """Hash of a source file as git stores it: CRLF folded to LF. A Windows checkout
    (`core.autocrlf=true`) holds CRLF where git and Linux CI hold LF, and a freshly written file
    is LF until git next touches it, so raw bytes made one commit fingerprint differently per
    machine and evidence recorded on one read as stale on another. Binaries use `_sha256_file`.
    A skill's `skill.yaml` is hashed without its native status claim (see above)."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    if path.name == "skill.yaml":
        data = _without_native_status_claim(data.decode("utf-8")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _sha256_tree(root: Path, patterns: tuple[str, ...]) -> str:
    """Content hash of a directory subset — sorted by relative path so it is order-stable."""
    h = hashlib.sha256()
    files: list[Path] = []
    for pattern in patterns:
        files.extend(p for p in root.glob(pattern) if p.is_file())
    for path in sorted(set(files), key=lambda p: p.relative_to(root).as_posix()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(_sha256_source(path).encode())
    return h.hexdigest()


def evidence_tuple(
    skill: str, root: Path, native_binary: Path | None = None
) -> dict[str, str | None]:
    """The fingerprint an acceptance record is bound to.

    Every member answers "could this change what a run would produce?". The four shared ones —
    `python_core`, `contracts`, `verifier`, `settings` — are here precisely because they are
    easy to forget: they are not per-skill, so nothing about editing a skill bundle reminds you
    they exist.
    """

    def _tree(rel: str, patterns: tuple[str, ...]) -> str | None:
        path = root / rel
        return _sha256_tree(path, patterns) if path.is_dir() else None

    def _file(rel: str) -> str | None:
        path = root / rel
        return _sha256_source(path) if path.is_file() else None

    tuple_: dict[str, str | None] = {
        # The skill's own declarative contract + handlers.
        "bundle": _tree(
            f"skills/{skill}",
            ("*.yaml", "python/**/*.py", "native/src/**/*.rs"),
        ),
        # Shared contracts both runtimes read.
        "contracts": _tree("contracts", ("**/*.yaml", "**/*.json")),
        # Shared planning AND execution code: changes plans for every skill, touching no
        # bundle. `agent.py` is here because the pipeline it owns — tool dispatch, expansion,
        # the clarify gate — decides what a run produces just as surely as the planner does;
        # leaving it out meant the gate could not see the runtime being rewritten.
        "python_core": _tree(
            "python/core/knaif", ("planner.py", "prompt.py", "registry.py", "agent.py")
        ),
        # What turns a run into a number. A scoring or outcome-policy change makes two
        # scoreboards incomparable even when nothing about the runtime moved — the same
        # reason `POLICY_VERSION` exists, expressed as a fingerprint rather than as a stamp.
        "grading": _tree(
            "python/core/knaif/evalsuite", ("scoring.py", "outcomes.py", "acceptance.py")
        ),
        # L3 and L4 are claims about the SHIPPED BINARY. Its sources were not fingerprinted at
        # all, so the native runtime could be rewritten under a record still reading "valid".
        "native": _tree(".", NATIVE_PATTERNS),
        # The corpus the run graded.
        "corpus": _file(f"skills/{skill}/data/eval.jsonl"),
        # The safety corpus L4's verdict covers whole. A row added after the run was never
        # measured, so without this the record would still read valid.
        "safety_corpus": _file(f"skills/{skill}/data/safety_test.jsonl"),
        # "success" is a moving target — hash what grades, not what it is called.
        "verifier": _tree(f"skills/{skill}/eval", ("*.py",)),
        # Effective generation settings, as a contract rather than as prose.
        "settings": _file("contracts/runtime/generation.yaml"),
        # The rules that turn outcomes into a score. A record graded under another version is
        # not comparable, whatever else held still.
        "policy": str(POLICY_VERSION),
    }
    # The GGUF the skill ships with, by the hash the manifest publishes. Absent (not None)
    # when there is nothing to compare against — no recommended model, or `sha256: TODO`
    # before upload — because a None here would read as "changed" against every record.
    model = _recommended_model_sha(skill, root)
    if model:
        tuple_["model"] = model
    # The built binary is not in the tree. Only a gate handed the artifact can check it.
    if native_binary is not None:
        tuple_["native_binary"] = _sha256_file(native_binary)
    return tuple_


def _recommended_model_sha(skill: str, root: Path) -> str | None:
    """sha256 the model manifest publishes for this skill's `recommended_model`, if any."""
    skill_yaml = root / "skills" / skill / "skill.yaml"
    if not skill_yaml.is_file():
        return None
    name = (yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}).get("recommended_model")
    return _manifest_model_sha(name, root)


def _manifest_model_sha(name: Any, root: Path) -> str | None:
    """sha256 the model manifest publishes for model *name*, if it publishes a real one."""
    manifest = root / MODEL_MANIFEST
    if not (isinstance(name, str) and manifest.is_file()):
        return None
    models = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("models") or {}
    sha = str((models.get(name) or {}).get("sha256") or "")
    return sha if len(sha) == 64 and all(c in "0123456789abcdef" for c in sha.lower()) else None


def _binary_os(head: bytes) -> str | None:
    """The `platforms.yaml` id an executable belongs to, from its header: the format AND the
    architecture, so an arm64 build is never taken for x64 (Codex, 2026-09-29). None if unknown."""
    if head[:2] == b"MZ" and len(head) >= 0x40:
        pe = int.from_bytes(head[0x3C:0x40], "little")
        if head[pe : pe + 4] == b"PE\x00\x00":
            machine = int.from_bytes(head[pe + 4 : pe + 6], "little")
            return "windows-x64" if machine == 0x8664 else None
        return None
    if head[:4] == b"\x7fELF" and len(head) >= 20:
        # 64-bit, little-endian, e_machine x86-64
        if head[4] == 2 and head[5] == 1 and int.from_bytes(head[18:20], "little") == 0x3E:
            return "linux-x64"
        return None
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):  # Mach-O 64 / universal
        return "macos"
    return None


def binaries_by_os(paths: list[Path]) -> dict[str, str]:
    """{os id: sha256} for the packaged binaries under test, one per OS, recognised by header."""
    out: dict[str, str] = {}
    for path in paths:
        os_id = _binary_os(Path(path).read_bytes()[:4096])
        if os_id is None:
            raise ValueError(f"{path}: not a Windows x64, Linux x64 or macOS executable")
        if os_id in out:
            raise ValueError(f"two binaries for {os_id}: the gate checks one artifact per OS")
        out[os_id] = _sha256_file(Path(path))
    return out


def _current_for_cell(
    cell: str,
    current: dict[str, str | None],
    root: Path,
    binaries: dict[str, str] | None = None,
    recorded_binary: Any = None,
    equivalences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The fingerprint a cell is judged against: the tree's, with `model` being the GGUF the
    cell NAMES (a cell key starts with the model's manifest name), not the skill's recommended
    one. Judged against the recommended model, every other model's cells read "stale: model"
    once recorded, and a cell measured on the wrong GGUF read valid (R5c T7, 2026-09-28).

    With *binaries* ({os: sha256}, several packaged artifacts), `native_binary` is the binary of
    the OS the cell names (absent if none was given: "not checked here"). A cell keyed by model
    alone (L3) is judged against whichever given binary it recorded, else it has drifted."""
    cell_current = {k: v for k, v in current.items() if k != "model"}
    sha = _manifest_model_sha(cell.split("|", 1)[0], root)
    if sha:
        cell_current["model"] = sha
    if binaries is not None:
        cell_current.pop("native_binary", None)
        parts = cell.split("|")
        if len(parts) == 3:
            if parts[1] in binaries:
                cell_current["native_binary"] = binaries[parts[1]]
        elif binaries:
            cell_current["native_binary"] = _pick_binary(binaries, recorded_binary, equivalences)
    return cell_current


#: Rebuilds the owner accepted as equivalent to a measured build (`evalsuite equivalence`).
EQUIVALENCES = ACCEPTANCE_DIR / "equivalences.json"

#: The native source tree the `native` fingerprint covers (see `evidence_tuple`).
NATIVE_PATTERNS = (
    "native/crates/*/src/**/*.rs",
    "apps/cli/src/**/*.rs",
    "skills/*/native/src/**/*.rs",
)


def _mapping(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("from")) and bool(value.get("to"))


def _valid_equivalence(entry: Any) -> bool:
    """Only the shapes `evalsuite equivalence` writes may carry anything: an id, a `native` source
    mapping, and at least one binary mapping. A text fix maps `native` and nothing else under
    `fingerprints`; a `sampled` entry (a code change, verified by a pre-registered sample run it
    names) may also map `bundle`, per skill, and — since 1.2.1 — `python_core` and `contracts`
    (the run must then also check the Python runtime, which `load_equivalences` re-verifies). A
    hand-added `grading`/`model` mapping, or any of these on a text fix, is ignored (Codex,
    2026-09-29)."""
    if not isinstance(entry, dict) or not entry.get("id"):
        return False
    fps = entry.get("fingerprints")
    bins = entry.get("binaries")
    if not isinstance(fps, dict) or not _mapping(fps.get("native")):
        return False
    if entry.get("kind") == "sampled":
        if not entry.get("sample_run") or not set(fps) <= SAMPLED_CARRIES:
            return False
        if not all(_mapping(fps[k]) for k in ("python_core", "contracts") if k in fps):
            return False
        bundle = fps.get("bundle", {})
        if not isinstance(bundle, dict) or not all(_mapping(m) for m in bundle.values()):
            return False
    elif set(fps) != {"native"}:
        return False
    if not isinstance(bins, dict) or not bins:
        return False
    return all(_mapping(p) for p in bins.values())


def load_equivalences(root: Path) -> list[dict[str, Any]]:
    """The honoured entries. A `sampled` entry is honoured only while the run it names is on disk
    and still shows every OS it maps and every measured skill equivalent — the gate re-checks the
    evidence instead of trusting a hand-editable field (Codex, 2026-09-29)."""
    path = root / EQUIVALENCES
    if not path.is_file():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    honoured = []
    for entry in doc.get("equivalences") or []:
        if not _valid_equivalence(entry):
            continue
        if entry.get("kind") == "sampled" and sampled_entry_problems(root, entry):
            continue
        honoured.append(entry)
    return honoured


#: What a `sampled` equivalence may map. `python_core` and `contracts` only with a Python stage in
#: its run (`_needs_python_stage`), and `contracts` only for a backend-manifest change
#: (`contracts_change_allowed`, checked when the entry is recorded).
SAMPLED_CARRIES = frozenset({"native", "bundle", "python_core", "contracts"})


def _needs_python_stage(entry: dict[str, Any]) -> bool:
    """A mapping beyond the native binary's own sources needs the run to have checked Python too:
    the native sample says nothing about the Python lane."""
    return bool({"python_core", "contracts"} & set(entry.get("fingerprints") or {}))


#: `(base, patterns)` of the two shared fingerprints a sampled entry may map; see `evidence_tuple`.
SHARED_SPECS = {
    "python_core": ("python/core/knaif", ("planner.py", "prompt.py", "registry.py", "agent.py")),
    "contracts": ("contracts", ("**/*.yaml", "**/*.json")),
}


def _git_names(root: Path, a: str, b: str, specs: list[str]) -> list[str] | None:
    """Files that differ between commits *a* and *b* under the git pathspecs, or None when git
    cannot say (a shallow clone, an unknown commit): the caller must then honour nothing."""
    import subprocess

    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", a, b, "--", *specs],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return out.stdout.split() if out.returncode == 0 else None


def _git_show(root: Path, commit: str, name: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "show", f"{commit}:{name}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def sampled_entry_problems(root: Path, entry: dict[str, Any]) -> list[str]:
    """Why a `sampled` entry must not be honoured, or [] — re-derived from git and the run on
    disk at every read, never from the entry's own `changed_files` (Codex, 2026-10-02).

    - every mapped fingerprint is what `from_commit` and `to_commit` actually hold;
    - a bundle moved only through `skill.yaml` `dependencies`, the skill's native sources, or its
      Python modules; `contracts` only under `contracts/backends/`;
    - the run holds a `python` stage whenever Python code or a contract is carried, and that stage
      recorded running on exactly the Python tree the entry maps to (`python_tree.json`)."""
    fps = entry.get("fingerprints") or {}
    a, b = entry.get("from_commit"), entry.get("to_commit")
    if not a or not b:
        return ["names no from_commit/to_commit"]
    problems: list[str] = []
    python_bundle = False
    try:
        for skill, mapping in (fps.get("bundle") or {}).items():
            base = f"skills/{skill}"
            names = _git_names(root, a, b, [f":(glob){base}/{p}" for p in BUNDLE_PATTERNS])
            if names is None:
                return [f"git cannot diff {a}..{b}"]
            for name in names:
                rel = name[len(base) + 1 :]
                if rel == "skill.yaml":
                    if not bundle_change_allowed(
                        _git_show(root, a, name), _git_show(root, b, name)
                    ):
                        problems.append(f"{name}: changed outside `dependencies`")
                elif rel.startswith("python/") and rel.endswith(".py"):
                    python_bundle = True
                elif not rel.startswith("native/src/"):
                    problems.append(f"{name}: not a file a sample may carry")
            for commit, want in ((a, mapping["from"]), (b, mapping["to"])):
                if tree_at_commit(root, commit, BUNDLE_PATTERNS, base=base) != want:
                    problems.append(f"{skill} bundle at {commit} is not {want[:12]}")
        for key, (base, patterns) in SHARED_SPECS.items():
            if key not in fps:
                continue
            for commit, want in ((a, fps[key]["from"]), (b, fps[key]["to"])):
                if tree_at_commit(root, commit, patterns, base=base) != want:
                    problems.append(f"{key} at {commit} is not {want[:12]}")
        if "contracts" in fps:
            base, patterns = SHARED_SPECS["contracts"]
            names = _git_names(root, a, b, [f":(glob){base}/{p}" for p in patterns])
            if names is None or not contracts_change_allowed(names):
                problems.append(f"contracts changed outside contracts/backends/: {names}")
    except Exception as exc:  # noqa: BLE001 - any git failure means: honour nothing
        return [f"cannot verify against git: {exc}"]
    python = _needs_python_stage(entry) or python_bundle
    run = root / str(entry.get("sample_run") or "")
    problems += sample_run_problems(
        run, set(entry.get("binaries") or {}), _measured_skills(root), python=python
    )
    if python:
        problems += _python_tree_problems(run, fps)
    return problems


def _python_tree_problems(run: Path, fps: dict[str, Any]) -> list[str]:
    """The `python` stage's own record of the tree it ran on must be the tree the entry carries
    results TO; otherwise an old passing stage could vouch for a later Python change."""
    path = run / "python_tree.json"
    if not path.is_file():
        return [f"{path}: missing (the python stage records the tree it ran on)"]
    tree = json.loads(path.read_text(encoding="utf-8"))
    problems = [
        f"python stage ran on {key} {str(tree.get(key))[:12]}, entry maps to {fps[key]['to'][:12]}"
        for key in SHARED_SPECS
        if key in fps and tree.get(key) != fps[key]["to"]
    ]
    for skill, mapping in (fps.get("bundle") or {}).items():
        if (tree.get("bundle") or {}).get(skill) != mapping["to"]:
            problems.append(f"python stage ran on another {skill} bundle")
    return problems


def contracts_change_allowed(changed: list[str]) -> bool:
    """True when every changed contract file is under `contracts/backends/`: the backend payload
    manifest, read only by `knaif backend` and the loader's receipt check — never by planning,
    validation, prompts or grading. Anything else in `contracts/` is a change a sample cannot
    vouch for."""
    return bool(changed) and all(name.startswith("contracts/backends/") for name in changed)


def sampled_bundle_file_allowed(rel: str, python_stage: bool) -> bool:
    """Which files under `skills/<skill>/` (besides `skill.yaml`, see `bundle_change_allowed`) a
    sampled equivalence may carry: the skill's native sources, and — when the run also checked the
    Python runtime — its Python modules. Never a prompt, tool or profile YAML."""
    if rel.startswith("native/src/"):
        return True
    return python_stage and rel.startswith("python/") and rel.endswith(".py")


def _carrying_entry(
    recorded: dict[str, Any],
    current: dict[str, Any],
    entries: list[dict[str, Any]],
    skill: str | None = None,
) -> dict[str, Any] | None:
    """The one equivalence that maps this record's measured build to the current one: its source
    mapping takes the recorded `native` to the current, (when a binary is checked) its binary
    mapping takes the recorded binary to the given one, and — if this skill's `bundle` moved too —
    its `bundle` mapping for this skill takes the recorded bundle to the current. All from the SAME
    entry, so two entries cannot certify a combination neither vouches for (Codex, 2026-09-29)."""
    rec_native, cur_native = recorded.get("native"), current.get("native")
    if rec_native is None or rec_native == cur_native:
        return None
    check_binary = "native_binary" in current and recorded.get("native_binary") is not None
    rec_bundle, cur_bundle = recorded.get("bundle"), current.get("bundle")
    bundle_moved = rec_bundle is not None and rec_bundle != cur_bundle
    for entry in entries:
        native = entry["fingerprints"]["native"]
        if native["from"] != rec_native or native["to"] != cur_native:
            continue
        if check_binary and not any(
            p["from"] == recorded.get("native_binary") and p["to"] == current.get("native_binary")
            for p in entry["binaries"].values()
        ):
            continue
        if bundle_moved:
            mapped = (entry["fingerprints"].get("bundle") or {}).get(skill or "")
            if not (mapped and mapped["from"] == rec_bundle and mapped["to"] == cur_bundle):
                continue
        # Python core and contracts, when they moved, from the SAME entry and exact values too.
        if not all(
            _maps_exactly(entry, key, recorded.get(key), current.get(key))
            for key in ("python_core", "contracts")
        ):
            continue
        return entry
    return None


def _maps_exactly(entry: dict[str, Any], key: str, rec: Any, cur: Any) -> bool:
    """True when `key` did not move, or the entry maps exactly its recorded value to the current."""
    if rec is None or rec == cur:
        return True
    mapped = entry["fingerprints"].get(key)
    return bool(mapped) and mapped["from"] == rec and mapped["to"] == cur


def _equivalence_label(entry: dict[str, Any]) -> str:
    """How the gate names a carrying entry: its id, and "(sampled)" when a sample run, not a
    text-only diff, is what vouches for it."""
    return f"{entry['id']} (sampled)" if entry.get("kind") == "sampled" else str(entry["id"])


def rust_text_spans(source: str) -> list[tuple[int, int]]:
    """[start, end) spans of string literals and comments in Rust source: a small lexer covering
    line and (nested) block comments, "..." with escapes, raw strings r#"..."#, byte strings and
    char literals. Used to prove a replacement touches only text, never code."""
    spans: list[tuple[int, int]] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if source.startswith("//", i):
            j = source.find("\n", i)
            j = n if j == -1 else j
            spans.append((i, j))
            i = j
        elif source.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if source.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif source.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            spans.append((i, j))
            i = j
        elif (c in "rb" and re.match(r"b?r#*\"", source[i:])) and (
            i == 0 or not (source[i - 1].isalnum() or source[i - 1] == "_")
        ):
            m = re.match(r"b?r(#*)\"", source[i:])
            assert m is not None
            close = '"' + m.group(1)
            j = source.find(close, i + m.end())
            j = n if j == -1 else j + len(close)
            spans.append((i, j))
            i = j
        elif c == '"' or (c == "b" and source.startswith('b"', i)):
            j = i + (2 if c == "b" else 1)
            while j < n and source[j] != '"':
                j += 2 if source[j] == "\\" else 1
            spans.append((i, j + 1))
            i = j + 1
        elif c == "'":
            m = re.match(r"'(\\.|\\u\{[0-9a-fA-F]+\}|[^\\'])'", source[i:])
            i += m.end() if m else 1  # a char literal, or a lifetime / label tick
        else:
            i += 1
    return spans


def text_only_change(old: str, new: str, replacements: list[tuple[str, str]]) -> bool:
    """True when *new* differs from *old* only by the declared replacements, applied to some of
    their occurrences (others may stay, e.g. as test data), at least one was applied, and every
    applied one lies inside a string literal or comment of *old*: a comparison or an identifier in
    code can never pass as a text fix. Walks both texts side by side, so any other difference,
    however small, fails."""
    spans = rust_text_spans(old)
    i = j = applied = 0
    while i < len(old) or j < len(new):
        for a, b in replacements:
            if old.startswith(a, i) and new.startswith(b, j) and a != b:
                if not any(s <= i and i + len(a) <= e for s, e in spans):
                    return False
                i, j, applied = i + len(a), j + len(b), applied + 1
                break
        else:
            if i < len(old) and j < len(new) and old[i] == new[j]:
                i, j = i + 1, j + 1
            else:
                return False
    return applied > 0


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    for part in re.split(r"(\*\*/|\*)", pattern):
        out += "(?:.*/)?" if part == "**/" else "[^/]*" if part == "*" else re.escape(part)
    return re.compile(out + r"\Z")


def tree_at_commit(root: Path, commit: str, patterns: tuple[str, ...], base: str = "") -> str:
    """`_sha256_tree(root / base, patterns)` computed from *commit*'s tree instead of the
    checkout, so a measured fingerprint can be tied to the commit it came from. Paths are taken
    relative to *base* and each blob is hashed as `_sha256_source` hashes the file (CRLF folded,
    `skill.yaml`'s native status claim masked), so the two agree byte for byte."""
    import subprocess

    names = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    prefix = base.strip("/") + "/" if base else ""
    regexes = [_glob_regex(p) for p in patterns]
    h = hashlib.sha256()
    rel_names = sorted(n[len(prefix) :] for n in names if n.startswith(prefix))
    for rel in (n for n in rel_names if any(r.match(n) for r in regexes)):
        blob = subprocess.run(
            ["git", "show", f"{commit}:{prefix}{rel}"], cwd=root, capture_output=True, check=True
        ).stdout.replace(b"\r\n", b"\n")
        if rel.rsplit("/", 1)[-1] == "skill.yaml":
            blob = _without_native_status_claim(blob.decode("utf-8")).encode("utf-8")
        h.update(rel.encode())
        h.update(hashlib.sha256(blob).hexdigest().encode())
    return h.hexdigest()


#: The files the `bundle` fingerprint covers, relative to `skills/<skill>` (see `evidence_tuple`).
BUNDLE_PATTERNS = ("*.yaml", "python/**/*.py", "native/src/**/*.rs")


def bundle_change_allowed(old: str, new: str) -> bool:
    """True when two `skill.yaml` texts differ only under `dependencies` (the external tools the
    skill runs and where to find them; nothing the prompt, planning or validation reads) or in the
    masked native status claim. Anything else — a prompt, a tool, a model — is not a change a
    sampled equivalence may carry."""
    try:
        before, after = yaml.safe_load(old), yaml.safe_load(new)
    except yaml.YAMLError:
        return False
    if not (isinstance(before, dict) and isinstance(after, dict)):
        return False

    def rest(doc: dict[str, Any]) -> str:
        doc = {k: v for k, v in doc.items() if k != "dependencies"}
        return _without_native_status_claim(yaml.safe_dump(doc, sort_keys=True))

    return rest(before) == rest(after)


#: `run.sh` stage names in a sample run, and the `platforms.yaml` id each one ran on.
SAMPLE_STAGES = {"win": "windows-x64", "linux": "linux-x64"}


def sample_run_problems(
    run_dir: Path, os_ids: set[str], skills: set[str], *, python: bool = False
) -> list[str]:
    """Why a sample run cannot vouch for an equivalence, or [] when it can.

    With `python`, the run must also hold a `python` stage: a `== python <skill>` verdict for every
    skill and one START/DONE, the way each OS's stage does.

    Every (OS, skill) pair must have a `VERDICT: equivalent on the sample` block in
    `verdicts.txt`; no line may report a failure; and `COMPLETE` must show each OS's stage started
    once and ended `DONE`, nothing else — two endings under one start is two runs overlapping in
    one folder (RC3, 2026-09-29), not a clean run."""
    problems: list[str] = []
    verdicts = run_dir / "verdicts.txt"
    complete = run_dir / "COMPLETE"
    if not verdicts.is_file() or not complete.is_file():
        return [f"{run_dir}: no verdicts.txt / COMPLETE"]
    found: dict[tuple[str, str], str] = {}
    current: tuple[str, str] | None = None
    for raw in verdicts.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("FAILED"):
            problems.append(f"a failure is recorded: {line}")
        head = re.match(r"^== (\S+) (\S+)$", line)
        if head:
            current = (SAMPLE_STAGES.get(head.group(1), head.group(1)), head.group(2))
        elif current and line.startswith("VERDICT:"):
            # A second block for one pair is two runs' verdicts in one file: a later good one
            # must not hide an earlier bad one (Codex, 2026-09-29).
            found[current] = "duplicate verdict blocks" if current in found else line
            current = None
    stages = sorted(os_ids) + (["python"] if python else [])
    for os_id in stages:
        for skill in sorted(skills):
            verdict = found.get((os_id, skill))
            if verdict != "VERDICT: equivalent on the sample":
                problems.append(f"{os_id} {skill}: {verdict or 'no verdict'}")
    events: dict[str, list[str]] = {}
    for line in complete.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("START", "DONE"):
            events.setdefault(SAMPLE_STAGES.get(parts[1], parts[1]), []).append(parts[0])
        elif line.startswith("FINISHED WITH FAILURES"):
            problems.append(f"a stage finished with failures: {line.strip()}")
    for os_id in stages:
        if events.get(os_id) != ["START", "DONE"]:
            problems.append(f"{os_id}: stage log is {events.get(os_id)}, not one START then DONE")
    return problems


def artifact_binary(artifact: Path) -> tuple[str | None, str]:
    """(platform id, sha256) of the knaif executable inside a release zip or tarball — so an
    equivalence maps the binary that was actually in the artifact a sample run tested."""
    import tarfile
    import zipfile

    if zipfile.is_zipfile(artifact):
        with zipfile.ZipFile(artifact) as z:
            names = [n for n in z.namelist() if re.search(r"(^|/)bin/knaif(\.exe)?$", n)]
            if len(names) != 1:
                raise ValueError(f"{artifact}: expected one bin/knaif executable, found {names}")
            data = z.read(names[0])
    else:
        with tarfile.open(artifact, "r:*") as t:
            members = [
                m for m in t.getmembers() if m.isfile() and re.search(r"(^|/)bin/knaif$", m.name)
            ]
            if len(members) != 1:
                raise ValueError(f"{artifact}: expected one bin/knaif executable")
            fh = t.extractfile(members[0])
            assert fh is not None
            data = fh.read()
    return _binary_os(data[:4096]), hashlib.sha256(data).hexdigest()


def run_preregistered(root: Path, run: str) -> bool:
    """True when the run's rules (`run.sh`) were committed strictly before its results
    (`verdicts.txt`): the commit that added the rules is an ancestor of, and not the same as, the
    one that added the verdicts. Rules written after the results are not a pre-registration."""
    import subprocess

    def added(name: str) -> str | None:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%H", "--", f"{run}/{name}"],
            cwd=root,
            capture_output=True,
            text=True,
        ).stdout.split()
        return out[-1] if out else None

    rules, results = added("run.sh"), added("verdicts.txt")
    if not rules or not results or rules == results:
        return False
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", rules, results], cwd=root, capture_output=True
        ).returncode
        == 0
    )


def _measured_skills(root: Path) -> set[str]:
    """Skills whose acceptance record holds L3/L4 cells."""
    skills = set()
    for path in (root / ACCEPTANCE_DIR).glob("*.json"):
        if path.name == EQUIVALENCES.name:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        layers = record.get("layers") if isinstance(record, dict) else None
        if any(((layers or {}).get(layer) or {}).get("cells") for layer in CELL_LAYERS):
            skills.add(str(record.get("skill") or path.stem))
    return skills


def record_equivalence(root: Path, entry: dict[str, Any]) -> Path:
    """Append *entry* to the equivalence record (ids are unique)."""
    if not _valid_equivalence(entry):
        raise ValueError("an equivalence maps `native` and at least one binary, nothing else")
    path = root / EQUIVALENCES
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    entries = list(raw.get("equivalences") or [])
    if any(e.get("id") == entry.get("id") for e in entries):
        raise ValueError(f"equivalence {entry.get('id')!r} is already recorded")
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = redact_local_paths({"equivalences": entries}, root=root)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _pick_binary(
    binaries: dict[str, str], recorded: Any, equivalences: list[dict[str, Any]] | None = None
) -> str:
    """For a record that names no OS: the given binary it recorded, else the given rebuild an
    equivalence maps it to (so the answer does not depend on argument order), else one it did not
    record (drift)."""
    given = list(binaries.values())
    if recorded in given:
        return str(recorded)
    for entry in equivalences or []:
        for pair in entry["binaries"].values():
            if pair["from"] == recorded and pair["to"] in given:
                return str(pair["to"])
    return given[0]


def _current_for_flat(
    layer: str,
    record: dict[str, Any] | None,
    current: dict[str, str | None],
    binaries: dict[str, str] | None,
    equivalences: list[dict[str, Any]] | None = None,
) -> dict[str, str | None]:
    """A flat (pre-matrix) layer, given several binaries: judged against the one it recorded,
    exactly as a model-keyed cell is. Without this, a list of binaries dropped the check for
    flat records altogether (Codex, 2026-09-29)."""
    if not binaries:
        return current
    entry = ((record or {}).get("layers") or {}).get(layer) or {}
    recorded = (entry.get("evidence") or {}).get("native_binary")
    return {**current, "native_binary": _pick_binary(binaries, recorded, equivalences)}


def load_status_contract(root: Path) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load((root / STATUS_CONTRACT).read_text(encoding="utf-8"))
    return doc


def _requirements(contract: dict[str, Any], status: str) -> list[str]:
    return list((contract["statuses"].get(status) or {}).get("requires") or [])


def load_acceptance_record(skill: str, root: Path) -> dict[str, Any] | None:
    path = root / ACCEPTANCE_DIR / f"{skill}.json"
    if not path.is_file():
        return None
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return record


def _layer_state(
    layer: str,
    record: dict[str, Any] | None,
    current: dict[str, str | None],
    contract: dict[str, Any],
    cell: str | None = None,
    equivalences: list[dict[str, Any]] | None = None,
) -> LayerState:
    """Evidence for one layer: present and matching the tree, present but stale, or absent.

    *cell* is the matrix cell being judged (L3/L4). A cell is a measurement with a verdict, so
    it must carry a literal true/false: anything else read as a pass before (Codex, 2026-09-28).
    """
    if record is None:
        return LayerState(layer, "pending", "no acceptance record")
    entry = (record.get("layers") or {}).get(layer)
    if not entry:
        return LayerState(layer, "pending", "record carries no evidence for this layer")

    recorded = entry.get("evidence") or {}
    depends = (contract["layers"].get(layer) or {}).get("invalidated_by") or []
    # Results measured on an earlier build carry over only through ONE equivalence that maps
    # both the measured source and the measured binary to what is here now.
    via = _carrying_entry(recorded, current, equivalences or [], (record or {}).get("skill"))
    carried: list[str] = [_equivalence_label(via)] if via else []
    carried_keys = (
        (
            "native",
            "native_binary",
            "bundle",
            *(k for k in ("python_core", "contracts") if k in via["fingerprints"]),
        )
        if via
        else ()
    )
    drifted = [
        key
        for key in depends
        if key in current
        and recorded.get(key) is not None
        and recorded.get(key) != current[key]
        and key not in carried_keys
    ]
    # Source carried over, binary still the measured one: that binary was not built from this
    # source. The rebuild the equivalence names is what ships with it.
    if (
        not via
        and "native_binary" in current
        and recorded.get("native_binary") == current.get("native_binary")
        and any(
            e["fingerprints"]["native"]["from"] == recorded.get("native")
            and e["fingerprints"]["native"]["to"] == current.get("native")
            for e in (equivalences or [])
        )
    ):
        drifted = [k for k in drifted if k != "native"]
        drifted.append("native_binary (the measured build, not the equivalent rebuild)")
    missing = [key for key in depends if key in current and recorded.get(key) is None]
    if drifted:
        return LayerState(layer, "stale", f"changed since the run: {', '.join(sorted(drifted))}")
    if missing:
        return LayerState(layer, "stale", f"record does not pin: {', '.join(sorted(missing))}")
    # A run-scoped key the record pins but this gate had nothing to compare with. It cannot make
    # the layer stale, and it must not vanish either: that silence is the bug this closes.
    unchecked = [
        key
        for key in depends
        if key in RUN_SCOPED and key not in current and recorded.get(key) is not None
    ]
    note = f" [not checked here: {', '.join(unchecked)}]" if unchecked else ""
    if carried:
        note += f" [equivalent: {', '.join(sorted(set(carried)))}]"
    # A run that recorded its own verdict is taken at its word. Recording a FAILED run as valid
    # evidence would let a status rest on a measurement that said "no" — the exact substitution
    # of "we ran it" for "it passed" this gate exists to prevent.
    if cell is not None and not isinstance(entry.get("passed"), bool):
        return LayerState(
            layer, "failing", f"record carries no true/false verdict ({entry.get('passed')!r})"
        )
    if entry.get("passed") is False:
        waiver = _waiver_that_holds(entry, cell)
        if waiver is not None:
            return LayerState(
                layer,
                "excepted",
                f"{entry.get('summary')} — owner exception {waiver.get('date')}: "
                f"{waiver.get('reason')}{note}",
            )
        return LayerState(
            layer,
            "failing",
            entry.get("summary", "the recorded run did not meet its threshold") + note,
        )
    return LayerState(layer, "valid", entry.get("summary", "") + note)


#: When cells disagree, the layer reads as its worst cell. A failure outranks staleness, which
#: outranks absence: each is a stronger statement about why the claim cannot stand. An owner's
#: exception ranks just below a clean pass: it supports the claim, and it is always printed.
_SEVERITY = ("failing", "stale", "pending", "excepted", "valid")

#: The only thresholds an owner may waive: quality (an aggregate or a capability slice). Safety
#: is never lowered, and an identity violation (wrong model, verifier, policy, coverage) means the
#: run did not measure the thing at all, so there is nothing to accept.
WAIVABLE_KINDS = frozenset({"aggregate", "slice"})


_UNMET_HEADER = re.compile(r"NOT ACCEPTED - (\d+) of \d+ thresholds unmet:")


def _unmet_kinds(summary: str) -> list[str] | None:
    """The kind of every unmet threshold a recorded verdict names (`[slice] ...`), or None when
    the summary does not account for all of them: the header's count must match the kinds
    listed, and every bracket must be a well-formed kind. A summary that hides a miss (a
    mangled `[safety ]`, a dropped line) must not be waivable (Codex, 2026-09-28)."""
    header = _UNMET_HEADER.match(summary)
    kinds = re.findall(r"\[(\w+)\] ", summary)
    if header is None or not kinds:
        return None
    if int(header.group(1)) != len(kinds) or summary.count("[") != len(kinds):
        return None
    return kinds


def _waivable(summary: str) -> bool:
    kinds = _unmet_kinds(summary)
    return kinds is not None and set(kinds) <= WAIVABLE_KINDS


def _waiver_that_holds(entry: dict[str, Any], cell: str | None) -> dict[str, Any] | None:
    """The cell's owner exception, if it still applies: given for THIS cell and THIS
    measurement (its verdict quoted verbatim, its run and evidence the ones recorded, so a
    re-run or a copy onto another cell is not covered), with a reason, over quality thresholds
    only. Checked here too, not only when written, since the record is a file."""
    waiver = entry.get("owner_exception")
    summary = str(entry.get("summary") or "")
    if not isinstance(waiver, dict) or not _waivable(summary):
        return None
    bound = (
        waiver.get("waives") == summary
        and waiver.get("cell") == cell
        and waiver.get("run") == entry.get("run")
        and waiver.get("evidence") == entry.get("evidence")
    )
    if not bound or not str(waiver.get("reason") or "").strip():
        return None
    return waiver


def waive_cell(skill: str, root: Path, layer: str, cell: str, *, reason: str, date: str) -> Path:
    """Record the owner's decision to ship a cell that failed a quality threshold.

    The verdict stays as measured (`passed: false`, its summary); the waiver sits beside it and
    quotes the verdict it answers. Re-recording the cell replaces the whole entry, so a new
    verdict needs a new decision. Refuses anything the gate would not honour.
    """
    record = load_acceptance_record(skill, root)
    cells = (((record or {}).get("layers") or {}).get(layer) or {}).get("cells") or {}
    entry = cells.get(cell)
    if not isinstance(entry, dict) or entry.get("passed") is not False:
        raise ValueError(f"{skill} {layer} {cell}: only a recorded failing verdict can be waived")
    summary = str(entry.get("summary") or "")
    if not _waivable(summary):
        raise ValueError(
            f"{skill} {layer} {cell}: {summary!r} is not waivable; only "
            f"{sorted(WAIVABLE_KINDS)} thresholds are, and the verdict must name them"
        )
    if not reason.strip():
        raise ValueError("a waiver needs the reason for the decision")
    entry["owner_exception"] = {
        "date": date,
        "reason": reason.strip(),
        "waives": summary,
        "cell": cell,
        "run": entry.get("run"),
        "evidence": entry.get("evidence"),
    }
    path = root / ACCEPTANCE_DIR / f"{skill}.json"
    record = redact_local_paths(record, root=root)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _cells_state(
    layer: str,
    record: dict[str, Any] | None,
    current: dict[str, str | None],
    contract: dict[str, Any],
    cells: list[str],
    root: Path,
    binaries: dict[str, str] | None = None,
    equivalences: list[dict[str, Any]] | None = None,
) -> LayerState:
    """A cell-keyed layer (the acceptance matrix): valid only when EVERY required cell is.

    Each cell is judged exactly as a flat layer is — same staleness, same failing-vs-pending
    distinction — so the matrix adds coverage without adding a second set of rules.
    """
    if not cells:
        return LayerState(layer, "pending", "the acceptance matrix requires no cell for this layer")
    entry = ((record or {}).get("layers") or {}).get(layer)
    stored = entry.get("cells") if isinstance(entry, dict) else None
    per_cell = {
        cell: _layer_state(
            layer,
            # `skill` travels with the cell: a sampled equivalence maps `bundle` per skill.
            {"skill": (record or {}).get("skill"), "layers": {layer: (stored or {}).get(cell)}},
            _current_for_cell(
                cell,
                current,
                root,
                binaries,
                (((stored or {}).get(cell) or {}).get("evidence") or {}).get("native_binary"),
                equivalences,
            ),
            contract,
            cell,
            equivalences,
        )
        for cell in cells
    }
    worst = min((s.state for s in per_cell.values()), key=_SEVERITY.index)
    # A composed cell (evalsuite.compose) rests on reused evidence; it counts, and says so.
    composed = [c for c in cells if ((stored or {}).get(c) or {}).get("composed")]
    note = f" [composed, not a full run: {', '.join(composed)}]" if composed else ""
    # Results carried over to an equivalent rebuild are named in every state.
    carried = sorted(
        {m for st in per_cell.values() for m in re.findall(r"\[equivalent: ([^\]]+)\]", st.detail)}
    )
    if carried:
        note += f" [equivalent: {', '.join(carried)}]"
    if worst == "valid":
        # A cell that is valid only because nothing was there to compare must still say so.
        for c, st in per_cell.items():
            what = re.search(r"\[not checked here: ([^\]]+)\]", st.detail)
            if what:
                note += f" [not checked here: {what.group(1)} for {c}]"
        return LayerState(layer, "valid", f"{len(cells)} cell(s) valid{note}")
    groups = []
    for state in _SEVERITY[:-1]:
        hit = [(cell, st) for cell, st in per_cell.items() if st.state == state]
        if not hit:
            continue
        if state == "pending":  # "no evidence" says nothing per cell; the names are the news
            groups.append(f"pending: {', '.join(cell for cell, _ in hit)}")
        else:
            groups.append(f"{state}: " + ", ".join(f"{c} ({st.detail})" for c, st in hit))
    bad = "; ".join(groups)
    if stored is None and entry:
        bad = "record is not keyed by cell (pre-matrix); " + bad
    return LayerState(layer, worst, bad + note)


def evaluate_skill(
    skill: str,
    root: Path,
    declared: str,
    native_binary: Path | list[Path] | None = None,
) -> SkillGate:
    """Derive the status this skill's evidence actually supports, and compare with its claim.

    Pass *native_binary* (the packaged artifact under test) to check the binary a record
    measured; without it the gate says it did not.
    """
    contract = load_status_contract(root)
    record = load_acceptance_record(skill, root)
    # Several packaged artifacts (one per OS) are matched to the cells of their own OS; a single
    # path keeps the original meaning: that binary, for every record.
    binaries = binaries_by_os(native_binary) if isinstance(native_binary, list) else None
    single = native_binary if not isinstance(native_binary, list) else None
    current = evidence_tuple(skill, root, single)
    equivalences = load_equivalences(root)

    all_layers = list(contract["layers"])
    matrix = load_matrix(root)
    states = [
        (
            _cells_state(
                name,
                record,
                current,
                contract,
                required_cells(matrix, name),
                root,
                binaries,
                equivalences,
            )
            if matrix is not None and name in CELL_LAYERS
            else _layer_state(
                name,
                record,
                _current_for_flat(name, record, current, binaries, equivalences),
                contract,
                equivalences=equivalences,
            )
        )
        for name in all_layers
    ]
    valid = {s.layer for s in states if s.state in ("valid", "excepted")}

    derived = "in-progress"
    for status in STATUS_ORDER:
        if set(_requirements(contract, status)) <= valid:
            derived = status

    gate = SkillGate(skill=skill, declared=declared, derived=derived, layers=states)

    if declared not in contract["statuses"]:
        gate.problems.append(
            f"{skill}: unknown native status {declared!r}; "
            f"expected one of {', '.join(STATUS_ORDER)}"
        )
        return gate

    if STATUS_ORDER.index(declared) > STATUS_ORDER.index(derived):
        missing = [s for s in states if s.layer in _requirements(contract, declared)]
        why = "; ".join(
            f"{s.layer} {s.state}" + (f" ({s.detail})" if s.detail else "") for s in missing
        )
        gate.problems.append(
            f"{skill}: skill.yaml claims {declared!r} but the evidence supports {derived!r} — {why}"
        )
    return gate


def check_platform_coverage(root: Path) -> list[str]:
    """A platform may not be `supported` unless the recorded parity coverage includes it.

    The tripwire for a known-incoming change, not a theoretical safeguard: macOS support is in
    flight, and this converts "remember to extend parity coverage" from a note someone must find
    into a failure they cannot miss.
    """
    platforms = yaml.safe_load((root / PLATFORMS_CONTRACT).read_text(encoding="utf-8"))
    aliases = load_status_contract(root).get("platform_coverage_aliases") or {}
    covered = recorded_platform_coverage(root)
    problems = []
    for entry in platforms.get("platforms") or []:
        if entry.get("status") != "supported":
            continue
        pid = entry.get("id", "?")
        os_key = pid.split("-")[0]
        accepted = set(aliases.get(os_key) or [os_key])
        if not (accepted & covered):
            problems.append(
                f"platforms.yaml marks {pid!r} supported, but no parity contract records "
                f"coverage for it (looked for {sorted(accepted)}, found {sorted(covered)}). "
                "Either exercise the layers there and record it in the contracts' `_coverage` "
                "block, or do not claim the platform."
            )
    return problems


def recorded_platform_coverage(root: Path) -> set[str]:
    """OS keys the parity contracts claim to have been exercised on (CI or local)."""
    covered: set[str] = set()
    for path in sorted((root / "contracts" / "parity").glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for os_key, how in (doc.get("_coverage") or {}).items():
            if how and how != "unexercised":
                covered.add(os_key)
    return covered


def record_layers(
    skill: str,
    root: Path,
    layers: dict[str, dict[str, Any]],
) -> Path:
    """Merge evidence for `layers` into the skill's acceptance record, stamping the tuple.

    **Evidence is written by whatever verified it, never typed by hand.** `just check-contracts`
    calls this for L1/L2 after the tests pass; a parity run supplies L3; a lane run supplies L4.
    A record entry therefore means "this actually ran against this tree", and the stamped tuple
    is what lets a later check notice the tree has moved.
    """
    path = root / ACCEPTANCE_DIR / f"{skill}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = load_acceptance_record(skill, root) or {"skill": skill, "layers": {}}
    current = evidence_tuple(skill, root)
    for name, entry in layers.items():
        # A cell-keyed entry (the acceptance matrix) lands in its own cell, so recording one
        # model/OS/backend never overwrites another's verdict.
        if entry.get("cell"):
            cell = entry["cell"]
            layer = record["layers"].get(name)
            if not isinstance(layer, dict) or "cells" not in layer:
                layer = {"cells": {}}
            body = {k: v for k, v in entry.items() if k != "cell"}
            layer["cells"][cell] = {**body, "evidence": entry.get("evidence") or current}
            record["layers"][name] = layer
            continue
        # A layer that brings its OWN fingerprint keeps it. The fingerprint belongs to the
        # measurement, not to the moment someone wrote it down: stamping the present tree onto
        # a saved run from before a planner change turned expired evidence back into valid
        # evidence just by re-recording it — the single thing this record exists to prevent.
        # L1/L2 are recorded by `just check` against the tree as it is, so they pass none and
        # correctly take the current one.
        captured = entry.get("evidence")
        record["layers"][name] = {**entry, "evidence": captured or current}
    # Committed and public: no checkout or home paths (AGENTS.md, Public Output Hygiene).
    record = redact_local_paths(record, root=root)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _model_name(meta: dict[str, Any], root: Path) -> str:
    """The public model a parity run measured: the manifest key whose `file` is its GGUF."""
    path = (meta.get("model") or {}).get("path") or ""
    filename = Path(path).name
    manifest = root / MODEL_MANIFEST
    if filename and manifest.is_file():
        models = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("models") or {}
        for key, entry in models.items():
            if (entry or {}).get("file") == filename:
                return str(key)
    return Path(filename).stem if filename else "unknown-model"


def record_from_parity_run(skill: str, root: Path, run_dir: Path) -> Path:
    """Record L3 evidence from a saved parity run directory.

    Reads the run's own `meta.json` rather than trusting the caller: the verdict, the bound and
    whether it passed are facts of the run, and a record that restated them by hand could
    disagree with the artifact it cites.
    """
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    result = meta.get("result") or {}
    # L3's bar (native_status.yaml `thresholds.L3`): zero port bugs, zero capability gaps,
    # plan disagreement within the run's pre-written bound. A run judged by the retired
    # equivalence-rate threshold never counted port bugs, so its `passed` cannot stand for it.
    judged = "port_bugs" in result
    passed = bool(result.get("passed")) if judged else False
    if judged:
        summary = (
            f"{run_dir.name}: port_bugs={result.get('port_bugs')} "
            f"not_implemented={result.get('native_not_implemented')} "
            f"plan_disagreement={result.get('plan_disagreement_rate')} "
            f"<= {result.get('max_plan_disagreement')} passed={passed}"
        )
    else:
        summary = (
            f"{run_dir.name}: judged by the retired equivalence-rate bar "
            f"(rate={result.get('equivalence_rate')}); re-run under the port-bug bar"
        )
    # The run's own model and binary, which only its meta knows (see `RUN_SCOPED`).
    evidence = {
        **evidence_tuple(skill, root),
        "model": (meta.get("model") or {}).get("sha256"),
        "native_binary": (meta.get("binary") or {}).get("sha256"),
    }
    return record_layers(
        skill,
        root,
        {
            "L3": {
                "cell": _model_name(meta, root),
                "evidence": evidence,
                "run": str(run_dir.relative_to(root)) if run_dir.is_absolute() else str(run_dir),
                "summary": summary,
                "equivalence_rate": result.get("equivalence_rate"),
                "port_bugs": result.get("port_bugs"),
                "native_not_implemented": result.get("native_not_implemented"),
                "plan_disagreement_rate": result.get("plan_disagreement_rate"),
                "max_plan_disagreement": result.get("max_plan_disagreement"),
                "passed": passed,
                "backend": meta.get("backend"),
                "git_sha": meta.get("git_sha"),
                "git_dirty": meta.get("git_dirty"),
            }
        },
    )


RELEASES_DIR = ACCEPTANCE_DIR / "releases"


def write_release_record(
    root: Path,
    version: str,
    skills: list[str] | None = None,
    native_binary: Path | list[Path] | None = None,
) -> Path:
    """Keep what was true for *version*: the acceptance records and the gate's verdict at the tag.

    The live records under `evals/acceptance/` go stale on `main` as soon as the tree moves, as
    they should; this copy is the answer to "what was true for 1.2.0?" (this module's docstring,
    and release plan R2/R7). Written once — an existing release is never overwritten — and only
    under the release the acceptance matrix names, since filing one release's evidence under
    another's number would be a false statement about that release. The one exception is a patch
    of that release whose results an honoured equivalence named `<patch>-…` carried over (1.2.1):
    its record says so in `carried_from` and `equivalences`.
    """
    from .matrix import load_matrix

    matrix = load_matrix(root)
    carried: list[str] = []
    if matrix is not None and matrix["current_release"] != version:
        # A patch keeps its minor's matrix: bumping the contract moves the `contracts`
        # fingerprint and stales every record. It may be recorded under its own number only
        # when an honoured equivalence named for it carried the results over.
        if _is_patch_of(version, matrix["current_release"]):
            carried = [
                e["id"] for e in load_equivalences(root) if str(e["id"]).startswith(f"{version}-")
            ]
        if not carried:
            raise ValueError(
                f"the acceptance matrix is for {matrix['current_release']}, not {version}; "
                "record the release the evidence was gathered for"
            )
    out = root / RELEASES_DIR / version
    if out.exists():
        raise FileExistsError(f"{out} already exists; a release record is written once")

    live = root / ACCEPTANCE_DIR
    names = skills if skills is not None else sorted(p.stem for p in live.glob("*.json"))
    out.mkdir(parents=True)
    # The equivalences the verdict leans on travel with it, or the frozen record would name an
    # entry whose hashes and reason live only in a file that keeps changing (Codex, 2026-09-29).
    if (root / EQUIVALENCES).is_file():
        (out / EQUIVALENCES.name).write_bytes((root / EQUIVALENCES).read_bytes())
    verdicts: dict[str, Any] = {}
    for skill in names:
        record = live / f"{skill}.json"
        if record.is_file():
            (out / record.name).write_bytes(record.read_bytes())
        declared = _declared_status(skill, root)
        # The verdict frozen at the tag is judged against the packaged binaries, like `gate`
        # itself; without them a different build could be frozen as valid (Codex, 2026-09-29).
        gate = evaluate_skill(skill, root, declared or "in-progress", native_binary=native_binary)
        verdicts[skill] = {
            "declared": declared,
            "derived": gate.derived,
            "layers": {s.layer: {"state": s.state, "detail": s.detail} for s in gate.layers},
        }
    matrix_release = matrix["current_release"] if carried and matrix else version
    release = (matrix or {}).get("releases", {}).get(matrix_release) if matrix else None
    doc: dict[str, Any] = {"version": version, "skills": verdicts, "matrix": release}
    if carried:
        doc["carried_from"] = matrix_release
        doc["equivalences"] = carried
    (out / "release.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out


def _is_patch_of(version: str, base: str) -> bool:
    """`1.2.1` is a patch of `1.2.0`: same major and minor, a later patch, no suffix."""
    parts = [p.split(".") for p in (version, base)]
    if not all(len(p) == 3 and all(x.isdigit() for x in p) for p in parts):
        return False
    (vmaj, vmin, vpat), (bmaj, bmin, bpat) = parts
    return (vmaj, vmin) == (bmaj, bmin) and int(vpat) > int(bpat)


def _declared_status(skill: str, root: Path) -> str | None:
    path = root / "skills" / skill / "skill.yaml"
    if not path.is_file():
        return None
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    native = ((doc.get("runtimes") or {}).get("native")) or {}
    status = native.get("status")
    return str(status) if status else None
