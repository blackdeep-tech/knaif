# Incumbent thumbnail prompt diagnostic

One in-memory FFmpeg prompt change documents supported `first`, `middle`, and `last`
positions alongside explicit timestamps. Repository prompt files are unchanged.

On the frozen v4 frame-content probe, correct frames improve from 6/20 to 11/20.
Cases 02, 03, 05, 09 and 11 become correct; no formerly correct frame is lost.
Case 08 worsens from an incorrect 2.5-second image to an execution error after the
model guesses 25 seconds for a 12-second source. Both `last` cases still fail the
exact-frame check because the renderer subtracts 0.1s from duration.

Outcome accuracy is 14/20; conditional artifact average is 11/14. Neither substitutes
for the 11/20 end-to-end correct-frame count. All 20 wrong-adjacent-frame negative
controls are rejected. Cases and fixture bytes match the original-prompt runs.

Not adopted: this is a narrow diagnostic with a worse failure mode on one row and no
full-corpus regression evaluation. The [paired experiment report](../2026-09-17_sft-v5-symbolic-pair-v1_success/report.md)
records the stopping decision. `prompt_override.json` preserves the exact change and
header hashes; `score.json` preserves every plan, error and frame score.
