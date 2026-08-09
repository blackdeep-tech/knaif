# Chain probe — can native fail to produce a multi-step plan?

P2c follow-up for [docs/plans/2026-08-08-native-python-planning-parity.md](../../../docs/plans/2026-08-08-native-python-planning-parity.md).

The plan was opened on the observation that the native runtime "would not produce a multi-step plan
at all", reported against a **real model, `plan`, ffmpeg** — the same configuration measured in the
2026-08-08 baseline, where 39 of 41 chain-tagged utterances chained correctly. If the symptom is
real, the trigger is not the runtime or the model but something else, and the most likely candidate
was **phrasing**: the eval corpus is curated, and a person typing by hand is not.

## Method

39 hand-written multi-step requests deliberately unlike corpus phrasing: casual and conversational,
bare imperative, four-step chains (longer than anything in the corpus), connectors the corpus never
uses (`&`, `+`, `;`, "followed by", "after that"), polite/verbose framing, pronoun-heavy chains
("cut it down and then compress **it**"), reversed clause order ("make it 720p **after** cutting
it"), four languages, uppercase, and typos.

## Result — the symptom does not reproduce

| lane | multi-step | notes |
|---|---|---|
| native, `knaif-qwen3-4b-v1` | **39/39** | every probe chained |
| python, `knaif-qwen3-4b-v1` | **39/39** | identical outcome |
| native, `knaif-qwen3-1.7b-v1` | **38/39** | the one miss is a validation error, not a refusal to chain |

Model auto-selection was also checked: `plan` with no `--model` picks the 4B (the only installed
model) and chains correctly, ruling out the "auto-selected a weaker model" explanation.

Even the 1.7B chains. The single 1.7B miss is `convert clip.mov to mp4, scale to 480p, mute it, and
compress` failing validation on an undeclared `convert_video` argument — the same schema-coverage
bug that accounts for all five failures in the full-corpus baseline, not a chaining failure.

## What is ruled out, and what is left

Ruled out as explanations for the original observation: the runtime, the model size, model
auto-selection, corpus-specific phrasing, connector style, chain length up to four steps, language,
and casing. Combined with the 847-utterance baseline, native chaining is robust across every
dimension probed.

Not ruled out, because they cannot be tested from here: an older binary predating a fix, a build
without the `llama` feature (the mock backend replays a canned response and never chains), or a
different skill. **The symptom is recorded as unreproducible**, with this file as the evidence.

That does not weaken the parity work — the prompt divergence R1/R2 pin is measured and real. It
removes the *urgency* framing: Workstream Q is a correctness port, not a fix for a quality
emergency.
