"""Merge a LoRA adapter into a bf16 HF checkpoint (Task 7, step 1).

Run in the TRAIN venv, once per base. Output feeds the SAME llama.cpp pipeline as
Task 1 (convert_hf_to_gguf.py --outtype f16, then llama-quantize Q4_K_M).

    python training/merge_to_hf.py --base Qwen/Qwen3-1.7B \
        --adapter training/adapters/qwen3-1.7b-ft \
        --out training/merged/qwen3-1.7b-ft
"""

from __future__ import annotations

import argparse
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("UNSLOTH_COMPILE_LOCATION", os.path.join(_HERE, "cache", "unsloth_compiled"))

import torch  # noqa: E402
from unsloth import FastLanguageModel  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, help="adapter dir from train_lora.py")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    # Load the saved LoRA dir directly — Unsloth resolves the base it was trained on
    # from adapter_config.json (base_model_name_or_path), so base + adapter always match.
    model, tok = FastLanguageModel.from_pretrained(
        args.adapter,
        max_seq_length=3072,
        dtype=torch.bfloat16,
        load_in_4bit=False,
    )
    # merged_16bit = base weights + adapter folded in, exported as a plain bf16 HF dir.
    model.save_pretrained_merged(args.out, tok, save_method="merged_16bit")
    print(f"merged bf16 HF -> {args.out}")


if __name__ == "__main__":
    main()
