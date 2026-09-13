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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

STATUS_CONTRACT = Path("contracts/release/native_status.yaml")
PLATFORMS_CONTRACT = Path("contracts/release/platforms.yaml")
ACCEPTANCE_DIR = Path("evals/acceptance")

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
    state: str  # "valid" | "failing" | "stale" | "pending"
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


def _sha256_tree(root: Path, patterns: tuple[str, ...]) -> str:
    """Content hash of a directory subset — sorted by relative path so it is order-stable."""
    h = hashlib.sha256()
    files: list[Path] = []
    for pattern in patterns:
        files.extend(p for p in root.glob(pattern) if p.is_file())
    for path in sorted(set(files), key=lambda p: p.relative_to(root).as_posix()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(_sha256_file(path).encode())
    return h.hexdigest()


def evidence_tuple(skill: str, root: Path) -> dict[str, str | None]:
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
        return _sha256_file(path) if path.is_file() else None

    return {
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
        "native": _tree(
            ".",
            ("native/crates/*/src/**/*.rs", "apps/cli/src/**/*.rs", "skills/*/native/src/**/*.rs"),
        ),
        # The corpus the run graded.
        "corpus": _file(f"skills/{skill}/data/eval.jsonl"),
        # "success" is a moving target — hash what grades, not what it is called.
        "verifier": _tree(f"skills/{skill}/eval", ("*.py",)),
        # Effective generation settings, as a contract rather than as prose.
        "settings": _file("contracts/runtime/generation.yaml"),
    }


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
) -> LayerState:
    """Evidence for one layer: present and matching the tree, present but stale, or absent."""
    if record is None:
        return LayerState(layer, "pending", "no acceptance record")
    entry = (record.get("layers") or {}).get(layer)
    if not entry:
        return LayerState(layer, "pending", "record carries no evidence for this layer")

    recorded = entry.get("evidence") or {}
    depends = (contract["layers"].get(layer) or {}).get("invalidated_by") or []
    drifted = [
        key
        for key in depends
        if key in current and recorded.get(key) is not None and recorded.get(key) != current[key]
    ]
    missing = [key for key in depends if key in current and recorded.get(key) is None]
    if drifted:
        return LayerState(layer, "stale", f"changed since the run: {', '.join(sorted(drifted))}")
    if missing:
        return LayerState(layer, "stale", f"record does not pin: {', '.join(sorted(missing))}")
    # A run that recorded its own verdict is taken at its word. Recording a FAILED run as valid
    # evidence would let a status rest on a measurement that said "no" — the exact substitution
    # of "we ran it" for "it passed" this gate exists to prevent.
    if entry.get("passed") is False:
        return LayerState(
            layer, "failing", entry.get("summary", "the recorded run did not meet its threshold")
        )
    return LayerState(layer, "valid", entry.get("summary", ""))


def evaluate_skill(skill: str, root: Path, declared: str) -> SkillGate:
    """Derive the status this skill's evidence actually supports, and compare with its claim."""
    contract = load_status_contract(root)
    record = load_acceptance_record(skill, root)
    current = evidence_tuple(skill, root)

    all_layers = list(contract["layers"])
    states = [_layer_state(name, record, current, contract) for name in all_layers]
    valid = {s.layer for s in states if s.state == "valid"}

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
        # A layer that brings its OWN fingerprint keeps it. The fingerprint belongs to the
        # measurement, not to the moment someone wrote it down: stamping the present tree onto
        # a saved run from before a planner change turned expired evidence back into valid
        # evidence just by re-recording it — the single thing this record exists to prevent.
        # L1/L2 are recorded by `just check` against the tree as it is, so they pass none and
        # correctly take the current one.
        captured = entry.get("evidence")
        record["layers"][name] = {**entry, "evidence": captured or current}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def record_from_parity_run(skill: str, root: Path, run_dir: Path) -> Path:
    """Record L3 evidence from a saved parity run directory.

    Reads the run's own `meta.json` rather than trusting the caller: the rate, the threshold and
    whether it passed are facts of the run, and a record that restated them by hand could
    disagree with the artifact it cites.
    """
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    result = meta.get("result") or {}
    summary = (
        f"{run_dir.name}: rate={result.get('equivalence_rate')} "
        f"threshold={result.get('threshold')} passed={result.get('passed')}"
    )
    return record_layers(
        skill,
        root,
        {
            "L3": {
                "run": str(run_dir.relative_to(root)) if run_dir.is_absolute() else str(run_dir),
                "summary": summary,
                "equivalence_rate": result.get("equivalence_rate"),
                "passed": result.get("passed"),
                "backend": meta.get("backend"),
                "git_sha": meta.get("git_sha"),
                "git_dirty": meta.get("git_dirty"),
            }
        },
    )
