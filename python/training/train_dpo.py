"""Small custom DPO LoRA pass for JSON-plan preferences."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("UNSLOTH_COMPILE_LOCATION", os.path.join(_HERE, "cache", "unsloth_compiled"))

import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from unsloth import FastLanguageModel  # noqa: E402

MAX_SEQ = 3072
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
SEED = 3407


def completion_logprob(model, tok, messages: list[dict], completion: str) -> torch.Tensor:
    prompt_text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    full_text = tok.apply_chat_template(
        [*messages, {"role": "assistant", "content": completion}],
        tokenize=False,
    )
    prompt_ids = tok(prompt_text, add_special_tokens=False).input_ids
    full_ids = tok(full_text, add_special_tokens=False).input_ids
    if len(full_ids) > MAX_SEQ:
        raise ValueError(f"sequence too long: {len(full_ids)} > {MAX_SEQ}")
    if len(full_ids) <= len(prompt_ids):
        raise ValueError("completion produced no target tokens")

    input_ids = torch.tensor([full_ids], device=model.device)
    logits = model(input_ids=input_ids).logits
    target = input_ids[:, len(prompt_ids) :]
    pred = logits[:, len(prompt_ids) - 1 : -1, :]
    logp = F.log_softmax(pred, dim=-1).gather(-1, target.unsqueeze(-1)).squeeze(-1)
    return logp.sum()


def dpo_loss(policy_chosen, policy_rejected, ref_chosen, ref_rejected, beta: float) -> torch.Tensor:
    policy_logratio = policy_chosen - policy_rejected
    ref_logratio = ref_chosen - ref_rejected
    return -F.logsigmoid(beta * (policy_logratio - ref_logratio))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.path.join(_HERE, "merged/qwen3-1.7b-sft-v3-flat"))
    ap.add_argument("--data", default=os.path.join(_HERE, "ffmpeg_pref_v1.jsonl"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--rank", type=int, default=8)
    ap.add_argument("--alpha", type=int, default=None)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--beta", type=float, default=0.1)
    ap.add_argument("--grad-accum", type=int, default=8)
    args = ap.parse_args()

    torch.manual_seed(SEED)
    alpha = args.alpha if args.alpha is not None else args.rank
    rows = [
        json.loads(line)
        for line in Path(args.data).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(
        f"[config] rows={len(rows)} base={args.base} rank={args.rank} alpha={alpha} "
        f"epochs={args.epochs} lr={args.lr} beta={args.beta}"
    )

    policy, tok = FastLanguageModel.from_pretrained(
        args.base, max_seq_length=MAX_SEQ, dtype=torch.bfloat16, load_in_4bit=False
    )
    policy = FastLanguageModel.get_peft_model(
        policy,
        r=args.rank,
        lora_alpha=alpha,
        lora_dropout=0.0,
        bias="none",
        target_modules=TARGET_MODULES,
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )
    ref, _ = FastLanguageModel.from_pretrained(
        args.base, max_seq_length=MAX_SEQ, dtype=torch.bfloat16, load_in_4bit=False
    )
    ref.eval()
    for param in ref.parameters():
        param.requires_grad_(False)

    opt = torch.optim.AdamW(
        (p for p in policy.parameters() if p.requires_grad), lr=args.lr, weight_decay=0.0
    )
    step = 0
    accum_loss = 0.0
    opt.zero_grad(set_to_none=True)
    for epoch in range(args.epochs):
        order = torch.randperm(
            len(rows), generator=torch.Generator().manual_seed(SEED + epoch)
        ).tolist()
        for pos, idx in enumerate(order, 1):
            row = rows[idx]
            messages = row["messages"]
            with torch.no_grad():
                ref_chosen = completion_logprob(ref, tok, messages, row["chosen"])
                ref_rejected = completion_logprob(ref, tok, messages, row["rejected"])
            policy_chosen = completion_logprob(policy, tok, messages, row["chosen"])
            policy_rejected = completion_logprob(policy, tok, messages, row["rejected"])
            loss = dpo_loss(policy_chosen, policy_rejected, ref_chosen, ref_rejected, args.beta)
            (loss / args.grad_accum).backward()
            accum_loss += float(loss.detach().cpu())
            if pos % args.grad_accum == 0 or pos == len(order):
                torch.nn.utils.clip_grad_norm_(policy.parameters(), 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                print(
                    f"epoch={epoch + 1} step={step} loss={accum_loss / args.grad_accum:.4f}",
                    flush=True,
                )
                accum_loss = 0.0

    policy.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"\nsaved adapter -> {args.out}")


if __name__ == "__main__":
    main()
