"""Phase-0 feasibility gate: one bf16 LoRA step on Qwen3-1.7B (RTX 5080 / sm_120).

Self-contained: pins Unsloth's compiled cache and the checkpoint output under
training/ so a run never litters the repo root, regardless of CWD. Seed for the
eventual `train-doctor` recipe (see docs/plans/2026-06-29-training-subsystem.md).
"""

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
# Must be set before importing unsloth — keeps the generated trainer cache in training/.
os.environ.setdefault("UNSLOTH_COMPILE_LOCATION", os.path.join(_HERE, "cache", "unsloth_compiled"))

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402
from unsloth import FastLanguageModel  # noqa: E402

model, tok = FastLanguageModel.from_pretrained(
    "Qwen/Qwen3-1.7B",
    max_seq_length=2048,
    dtype=torch.bfloat16,
    load_in_4bit=False,  # bf16 LoRA, NOT QLoRA
)
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    lora_alpha=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth",
)
ds = Dataset.from_dict({"text": ["Smoke test for Blackwell bf16 LoRA."] * 8})
trainer = SFTTrainer(
    model=model,
    tokenizer=tok,
    train_dataset=ds,
    args=SFTConfig(
        dataset_text_field="text",
        max_seq_length=2048,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=1,
        max_steps=1,
        bf16=True,
        logging_steps=1,
        output_dir=os.path.join(_HERE, "output", "unsloth_smoke"),
    ),
)
trainer.train()
print("PHASE 0 PASS — one bf16 LoRA step on", torch.cuda.get_device_name(0))
