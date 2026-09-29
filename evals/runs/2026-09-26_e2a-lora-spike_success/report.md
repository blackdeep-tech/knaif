# E2a — runtime LoRA vs merged model: FAIL on plan parity; size and swap time pass

**Plan:** [policy-gate-and-skill-adapters](../../../docs/plans/2026-09-26-policy-gate-and-skill-adapters.md) E2a ·
**Date:** 2026-09-26 · **Driver:** `run_all.sh` → `compare.py`, `swap_timing.py` · **Code:** experiment
working tree (orchestrator `lora_path`, 2 tests)

Arms, same code, same config, ffmpeg corpus (861 utterances) + safety:

- **merged** — `qwen3-4b-sft-v4-flat-q4`: sft-v4 merged, then quantized to Q4_K_M (the shipped form)
- **lora** — `qwen3-4b-base-plus-sft-v4-lora`: the base (`unsloth/Qwen3-4B`, same HF snapshot sft-v4
  trained from) quantized to Q4_K_M, with the sft-v4 adapter converted to a 66.1 MB f16 GGUF LoRA
  (`convert_lora_to_gguf.py` at b11003) applied at load
- **base** — control, first 60 rows, no adapter

## Results

| | merged | base + LoRA |
|---|---|---|
| ffmpeg outcome / knaif | 0.9431 / 0.9841 | 0.9384 / **0.9865** |
| safety | 11/11 | 11/11 |
| plans byte-identical to merged | — | 714/861 (**82.9%**) |
| outcome flips vs merged | — | **21 (2.44%)**, 0 on safety-tagged rows |

Control: on the first 60 rows the base alone plans identically to merged on 28, base + LoRA on 50,
so the adapter is applied.

Swap cost on llama.cpp's C API (`swap_timing.json`; the calls llama-cpp-2's `lora_adapter_init` /
`lora_adapter_set` wrap): adapter load **25 ms**, attach overhead **28 ms** per switch
(attach + one decode 35.7 ms vs decode alone 7.6 ms).

## Verdict against the pre-written criteria

| Criterion | Result | |
|---|---|---|
| plans byte-identical on ≥ 99%, or flips within 1.2% with none on a safety row | 82.9% identical, 2.44% flips, 0 on safety rows | ❌ |
| adapter swap < 100 ms | 28 ms | ✅ |
| adapter GGUF < 150 MB | 66.1 MB | ✅ |

**E2a: FAIL**, on parity. Per the plan, E2b does not run on this verdict.

## Reading it

The parity bar asked the runtime adapter to reproduce the *merged-then-quantized* model. The two
paths do different arithmetic: merged quantizes the merged weights; the runtime applies an f16 delta
on top of an already quantized base. Some plan differences were expected (the plan said so); 2.44% is
twice the bar. The quality difference is within the ffmpeg noise floor (−0.47 pp outcome, +0.24 pp
knaif) and safety is identical, but "within noise" was not the criterion.

**Diagnostic (not a verdict), `diag_f16.sh` → `compare_f16.py`:** the same comparison with nothing
quantized (merged f16 vs base f16 + LoRA f16 at load):

| | merged f16 | base f16 + LoRA |
|---|---|---|
| ffmpeg outcome | 0.9443 | 0.9466 |
| plans identical | — | 836/861 (**97.1%**) |
| outcome flips | — | **2 (0.23%)** |

So the adapter mechanism is near-exact (the residual 2.9% is f16 rounding of the delta vs the
merged weights), and the Q4 gap comes from **quantization order**: "merged" quantizes the merged
weights, the runtime path adds an f16 delta to an already quantized base. That is a property of
serving adapters on a quantized base, not a bug, and it means the right question for adapters is
the one E2b asks (does base + adapter *as served* meet the skill's bar), not byte parity with a
model that would never be served that way.
