"""bf16 LoRA fine-tune on the union chat dataset (Task 6).

Run in the TRAIN venv (Unsloth). Same hyperparameters across both base sizes so
the size-vs-gain comparison is clean — pass only --base and --out.

    # 1.7B
    python training/train_lora.py --base Qwen/Qwen3-1.7B \
        --out training/adapters/qwen3-1.7b-ft
    # 4B
    python training/train_lora.py --base Qwen/Qwen3-4B \
        --out training/adapters/qwen3-4b-ft

Consumes training/union_chat.jsonl (produced by build_dataset.py in the core venv).
Trains on the assistant completion only (prompt tokens masked).
"""

from __future__ import annotations

import argparse
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
# Pin Unsloth's compiled cache under training/ (same convention as phase0_smoke.py).
os.environ.setdefault("UNSLOTH_COMPILE_LOCATION", os.path.join(_HERE, "cache", "unsloth_compiled"))

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402
from unsloth import FastLanguageModel  # noqa: E402
from unsloth.chat_templates import train_on_responses_only  # noqa: E402

# ── shared hyperparameters (identical for 1.7B and 4B) ──
MAX_SEQ = 3072  # union prompts (header+tools+examples) reach ~2.1k tokens; headroom so
# the assistant plan is never truncated off the right end.
LORA_R = 16
LORA_ALPHA = 16
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
EPOCHS = 3
LR = 2e-4
BATCH = 1
GRAD_ACCUM = 8  # effective batch 8
WARMUP_RATIO = 0.05
SEED = 3407


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="HF base repo, e.g. Qwen/Qwen3-1.7B")
    ap.add_argument("--out", required=True, help="adapter output dir")
    ap.add_argument("--data", default=os.path.join(_HERE, "union_chat.jsonl"))
    ap.add_argument("--rank", type=int, default=LORA_R)
    ap.add_argument("--alpha", type=int, default=None, help="lora_alpha (default: = rank)")
    ap.add_argument("--epochs", type=float, default=EPOCHS)
    ap.add_argument("--lr", type=float, default=LR)
    args = ap.parse_args()
    alpha = args.alpha if args.alpha is not None else args.rank
    print(f"[config] rank={args.rank} alpha={alpha} epochs={args.epochs} lr={args.lr}")

    model, tok = FastLanguageModel.from_pretrained(
        args.base,
        max_seq_length=MAX_SEQ,
        dtype=torch.bfloat16,
        load_in_4bit=False,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.rank,
        lora_alpha=alpha,
        lora_dropout=0.0,
        bias="none",
        target_modules=TARGET_MODULES,
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )

    raw = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()]

    def fmt(ex):
        return {"text": tok.apply_chat_template(ex["messages"], tokenize=False)}

    ds = Dataset.from_list(raw).map(fmt)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tok,
        train_dataset=ds,
        args=SFTConfig(
            dataset_text_field="text",
            max_seq_length=MAX_SEQ,
            per_device_train_batch_size=BATCH,
            gradient_accumulation_steps=GRAD_ACCUM,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            warmup_ratio=WARMUP_RATIO,
            lr_scheduler_type="cosine",
            weight_decay=0.01,
            optim="adamw_8bit",
            bf16=True,
            logging_steps=5,
            seed=SEED,
            report_to="none",
            output_dir=os.path.join(_HERE, "output", os.path.basename(args.out)),
        ),
    )
    # Mask the prompt — train on the assistant turn only (Qwen3 ChatML markers).
    trainer = train_on_responses_only(
        trainer,
        instruction_part="<|im_start|>user\n",
        response_part="<|im_start|>assistant\n",
    )

    trainer.train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"\nsaved adapter -> {args.out}")


if __name__ == "__main__":
    main()
