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
# Bumped 1 -> 2 on 2026-09-12 (docs/plans/2026-09-11-reject-clarify-taxonomy.md). Three
# semantics moved together: a non-zero command exit now records `error` rather than `plan`,
# so failed rows leave `avg_knaif_score`'s denominator; the `reject` slice is graded as a
# failure budget rather than a rate; and the safety corpus holds invariants only. Records
# stamped 1 are not comparable to records stamped 2 — which is the point of the stamp.
#
# Bumped 2 -> 3 on 2026-09-17 (docs/plans/2026-09-17-4b-audit-and-improvement.md). The
# executing runner now grants confirmation during dry-run command capture, so a
# preview-gated intent contributes its **whole** chain instead of stopping at the gate.
# That moves `avg_knaif_score`'s denominator exactly the way the 1 -> 2 bump did: rows that
# previously recorded `error` (their later commands were never rendered) now execute and
# re-enter it, and rows that were graded on an intermediate output are now graded on the
# final one. Measured on a fixed model with *identical* predictions on all 1,015 utterances,
# ffmpeg moved 0.937720 -> 0.938895 outcome and 0.977945 -> 0.980123 knaif; documents did
# not move. No model changed; the instrument did. Records stamped 2 are therefore not
# comparable to records stamped 3, and both skills' `eval_snapshot.json` must be re-locked
# from a policy-3 run before their acceptance bars mean anything again.
POLICY_VERSION = 3

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
