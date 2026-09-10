"""The outcome vocabulary shared by both runtimes' records.

One distinction here is not cosmetic. A runtime can decline a request for two
opposite reasons:

* ``reject`` — it understood the request and refused. The safety model working.
* ``not_implemented`` — the capability is not built. A coverage gap.

Recorded under the same label they are indistinguishable, and **coverage cannot be
computed at all**: native's *"this request needs 3 steps"* and a correct destructive
refusal both read as ``reject``. Coverage is what keeps L4's exclusion of unattempted
rows from ``avg_knaif_score`` honest, so it has to be measurable rather than inferred.

The native runtime marks these with a ``not_implemented:`` line prefix (see
``NOT_IMPLEMENTED_PREFIX`` and ``apps/cli/src/main.rs``); Python has no unbuilt
capabilities and so never emits one, but reads and scores them.

See ``docs/plans/2026-09-10-skill-quality-lifecycle.md`` (L4d).
"""

from __future__ import annotations

#: The version of the **acceptance policy**: the thresholds, denominators and scoring
#: semantics in force when a record was written. Every scoreboard is stamped with it and
#: every `acceptance.yaml` declares the one it was written against, so a later tightening
#: cannot leave old records looking compliant with rules they were never measured under.
#: Bump this whenever any of those semantics change — and re-lock the baselines in the
#: same commit, because a number graded under new rules is not comparable to one graded
#: under old ones.
POLICY_VERSION = 1

#: Line prefix the native runtime uses for a capability it has not built.
#: Must stay in sync with `NOT_IMPLEMENTED_PREFIX` in `apps/cli/src/main.rs`.
NOT_IMPLEMENTED_PREFIX = "not_implemented:"

#: Every outcome a runtime can record for one utterance.
OUTCOMES = (
    "plan",
    "clarify",
    "reject",
    "not_implemented",
    "parse_error",
    "error",
)

#: Outcomes where the runtime produced something gradeable.
ATTEMPTED = ("plan",)

#: Outcomes where the runtime declined *because the capability does not exist*.
#: These are coverage misses: a failure for outcome accuracy, and excluded from the
#: quality average — which is only honest while coverage is reported alongside it.
UNATTEMPTED = ("not_implemented",)

#: Outcomes that are a deliberate answer to the request rather than a gap.
REFUSALS = ("clarify", "reject")


def is_capability_gap(outcome: str) -> bool:
    """True when the runtime declined because it cannot do this, not because it won't."""
    return outcome in UNATTEMPTED


def coverage(outcomes: list[str]) -> float:
    """Fraction of utterances the runtime actually attempted.

    A deliberate refusal counts as attempted — the runtime did its job. Only an
    unbuilt capability reduces coverage.
    """
    if not outcomes:
        return 0.0
    return sum(1 for o in outcomes if not is_capability_gap(o)) / len(outcomes)
