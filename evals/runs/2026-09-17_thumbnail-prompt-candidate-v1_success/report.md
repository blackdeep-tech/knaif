# Candidate thumbnail prompt diagnostic

The same in-memory symbolic-position clarification used for the incumbent leaves the
candidate at 18/20 correct frames, with 20/20 executed plan outcomes. Both failures
use the supported `last` token and expose the renderer's approximate final-frame seek.
All 20 wrong-adjacent-frame negative controls are rejected. Cases and fixture bytes
match the original-prompt and incumbent runs.

No repository prompt change was made. The candidate remains rejected for full-corpus
documents regressions; targeted thumbnail success does not override that decision.
See the [paired report](../2026-09-17_sft-v5-symbolic-pair-v1_success/report.md).
