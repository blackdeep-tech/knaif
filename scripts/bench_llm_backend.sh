#!/usr/bin/env bash
# Benchmark a single llama.cpp backend (CUDA / Vulkan / CPU) with llama-bench.
#
# Usage:
#   LLAMA_BENCH=/path/to/llama-bench scripts/bench_llm_backend.sh <label> [model.gguf ...]
#
# Example (WSL, CUDA build produced by this repo's setup) — current promoted lanes:
#   LLAMA_BENCH=~/tools/llama.cpp/build-cuda/bin/llama-bench \
#     scripts/bench_llm_backend.sh cuda models/knaif-qwen3-4b-v1-q4_k_m.gguf models/knaif-qwen3-1.7b-v1-q6_k.gguf
#
# Prints prompt-processing (pp) and token-generation (tg) throughput per model.
# Results append to bench_results.csv (label,model,test,tok_per_s).
set -euo pipefail

LABEL="${1:?first arg = backend label, e.g. cuda|vulkan|cpu}"
shift || true
MODELS=("$@")
if [ "${#MODELS[@]}" -eq 0 ]; then
  MODELS=(models/knaif-qwen3-4b-v1-q4_k_m.gguf)   # default: current 4B desktop lane (models.yaml knaif-qwen3-4b-v1)
fi

: "${LLAMA_BENCH:?set LLAMA_BENCH to a llama-bench binary}"
[ -x "$LLAMA_BENCH" ] || { echo "not executable: $LLAMA_BENCH" >&2; exit 1; }

NGL="${NGL:-99}"      # GPU layers (99 = all). Set NGL=0 to force CPU.
PROMPT="${PROMPT:-512}"
GEN="${GEN:-128}"
REPS="${REPS:-3}"
OUT="${OUT:-bench_results.csv}"

echo "backend=$LABEL  ngl=$NGL  pp=$PROMPT  tg=$GEN  reps=$REPS"
echo "bin=$LLAMA_BENCH"
echo

[ -f "$OUT" ] || echo "label,model,test,tok_per_s" > "$OUT"

for m in "${MODELS[@]}"; do
  [ -f "$m" ] || { echo "SKIP missing model: $m" >&2; continue; }
  echo "=== $m ==="
  # -o csv gives machine-readable rows; also show the human table on stderr.
  "$LLAMA_BENCH" -m "$m" -p "$PROMPT" -n "$GEN" -ngl "$NGL" -r "$REPS" 2>&1 | tee /dev/stderr \
    | awk -F'|' -v L="$LABEL" -v M="$(basename "$m")" '
        /pp'"$PROMPT"' *\|/ {split($(NF-1),a,"±"); gsub(/ /,"",a[1]); print L","M",pp,"a[1]}
        /tg'"$GEN"' *\|/    {split($(NF-1),a,"±"); gsub(/ /,"",a[1]); print L","M",tg,"a[1]}
      ' >> "$OUT" || true
  echo
done

echo "appended to $OUT"
