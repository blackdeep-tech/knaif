//! How a native skill says "I have not built this".
//!
//! A runtime can decline a request for two opposite reasons, and they must not share a label:
//!
//! * `reject:` — it understood the request and declined. The safety model working.
//! * `not_implemented:` — the capability is not built. A coverage gap.
//!
//! Recorded identically, **coverage cannot be computed at all**, and the acceptance rule that
//! excludes unattempted rows from the quality average stops being honest — it is conditioned on
//! coverage being gated independently (docs/plans/2026-09-10-skill-quality-lifecycle.md, L4d).
//!
//! This lives in the shared crate rather than in the CLI because the skills are where the gap
//! actually is. The first L4 run (2026-09-11) found `documents` using the marker and `ffmpeg`
//! bailing with a bare error, so the run reported **coverage 1.0000 when it was 0.9705** — a
//! defect that existed precisely because the two consumers could not reach one definition.

/// Line prefix marking output produced because a capability is not built.
///
/// Must stay in sync with `NOT_IMPLEMENTED_PREFIX` in `knaif.evalsuite.outcomes`; a contract
/// test pins the two equal.
pub const NOT_IMPLEMENTED_PREFIX: &str = "not_implemented:";

/// Build a `not_implemented:` message for a capability this runtime does not have.
pub fn not_implemented_message(reason: &str) -> String {
    format!("{NOT_IMPLEMENTED_PREFIX} {reason}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_message_carries_the_marker_the_harness_looks_for() {
        let msg = not_implemented_message("the ffmpeg intent \"reverse_video\" is not built");
        assert!(msg.starts_with(NOT_IMPLEMENTED_PREFIX));
        assert!(msg.contains("reverse_video"));
    }

    #[test]
    fn the_marker_is_not_a_reject() {
        // A capability gap and a declined request are opposite facts about the product; a
        // harness that cannot tell them apart cannot compute coverage.
        assert!(!NOT_IMPLEMENTED_PREFIX.starts_with("reject"));
    }
}
