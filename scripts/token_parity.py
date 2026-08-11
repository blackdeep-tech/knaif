#!/usr/bin/env python3
"""Do both runtimes feed the model the same integers?

``contracts/parity/prompt_cases.json`` pins the ``(system, user)`` **strings**, and
``scripts/dump_prompt.py`` vs ``knaif plan --dump-prompt`` proves they are byte-identical for
every corpus utterance. Nothing pinned what happens *after* that, and the two runtimes apply the
GGUF's chat template through different implementations::

    native   llama.cpp's C++ llama_chat_apply_template, then str_to_token(AddBos::Never)
    python   llama-cpp-python's Jinja2 rendering of the same template, inside
             create_chat_completion

Two implementations of one template string can disagree on whitespace, a trailing newline, or
whether a BOS is prepended — and any of those changes the token sequence, hence the output, with
no visible prompt difference. `docs/NATIVE.md` §4.1 called this out as necessary-but-not-sufficient
for parity.

Both paths are reachable from Python, so this compares them **without involving the native
binary**: lane B calls the very C function native calls. That is the whole trick — it removes the
build, the backend and the CLI from the comparison, leaving only the template and the tokenizer.

**Needs a GGUF**, so it cannot run in CI (`models/` is gitignored) — local tooling, like
`just parity`. Everything else in `contracts/parity/` is model-free by design.

Result on knaif-qwen3-4b-v1 (2026-08-11): prompts and token IDs identical, 1993 tokens, no BOS
discrepancy. Recorded as a **ruled-out** cause of the residual native/Python plan divergence.

    uv run python scripts/token_parity.py --skill ffmpeg "trim clip.mp4 to 5 seconds"
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def build_prompt(skill: str, utterance: str) -> tuple[str, str]:
    """The exact ``(system, user)`` the Python runtime sends, retrieval included."""
    from knaif import CommandAgent
    from knaif.registry import retrieve_tools

    agent = CommandAgent.from_skill(REPO_ROOT / "skills" / skill)
    override = retrieve_tools(utterance, agent.registry)
    return agent.build_prompt(utterance, registry_override=override)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("utterance")
    ap.add_argument("--skill", default="ffmpeg")
    ap.add_argument(
        "--model", type=Path, default=REPO_ROOT / "models" / "knaif-qwen3-4b-v1-q4_k_m.gguf"
    )
    args = ap.parse_args()

    if not args.model.exists():
        print(f"model not found: {args.model}", file=sys.stderr)
        return 2

    system, user = build_prompt(args.skill, args.utterance)
    # Both runtimes append the thinking suffix to the system turn, identically:
    # native  llama.rs  `format!("{system}\n\n/no_think")`
    # python  orchestrator._apply_thinking  `f"{system}\n\n/no_think"`
    system = f"{system}\n\n/no_think"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    # Load through the runtime's own orchestrator: on Windows it registers the CUDA DLL
    # directories, and importing llama_cpp before that fails outright.
    from knaif.orchestrator import InferenceOrchestrator

    orch = InferenceOrchestrator(
        backend="llama_cpp",
        model_config={
            "path": str(args.model),
            "n_ctx": 8192,
            "n_gpu_layers": 0,
            "json_mode": False,
        },
    )
    llm = orch.llm
    import llama_cpp
    from llama_cpp import llama_chat_format

    tmpl = llm.metadata.get("tokenizer.chat_template")
    if not tmpl:
        print("model has no chat template in its metadata", file=sys.stderr)
        return 2

    # lane A — llama-cpp-python's Jinja2 rendering (what create_chat_completion sends).
    bos_id = llm.token_bos()
    py_prompt = llama_chat_format.Jinja2ChatFormatter(
        template=tmpl,
        eos_token=llm._model.token_get_text(llm.token_eos()),
        bos_token=llm._model.token_get_text(bos_id) if bos_id != -1 else "",
        stop_token_ids=[llm.token_eos()],
    )(messages=messages).prompt

    # lane B — llama.cpp's own C++ template application (what native calls).
    n = len(messages)
    c_msgs = (llama_cpp.llama_chat_message * n)()
    keep: list[bytes] = []  # keep the buffers alive across the call
    for i, m in enumerate(messages):
        role, content = m["role"].encode(), m["content"].encode()
        keep += [role, content]
        c_msgs[i].role, c_msgs[i].content = role, content
    buf = ctypes.create_string_buffer(1 << 20)
    written = llama_cpp.llama_chat_apply_template(
        tmpl.encode(), c_msgs, ctypes.c_size_t(n), True, buf, ctypes.c_int32(len(buf))
    )
    if written < 0:
        print(f"llama_chat_apply_template failed: {written}", file=sys.stderr)
        return 2
    native_prompt = buf.raw[:written].decode("utf-8", errors="replace")

    py_tokens = llm.tokenize(py_prompt.encode(), add_bos=False, special=True)
    native_tokens = llm.tokenize(native_prompt.encode(), add_bos=False, special=True)
    prompts_equal, tokens_equal = py_prompt == native_prompt, py_tokens == native_tokens

    print(f"utterance      : {args.utterance!r}")
    print(
        f"prompts equal  : {prompts_equal}  (python {len(py_prompt)} chars, native {len(native_prompt)})"
    )
    print(f"tokens  equal  : {tokens_equal}  ({len(py_tokens)} vs {len(native_tokens)})")
    print(
        f"bos            : id={bos_id} add_bos_token={llm.metadata.get('tokenizer.ggml.add_bos_token')}"
    )

    if not prompts_equal:
        # strict=False deliberately: unequal lengths are a real outcome here, and the else-branch
        # below reports the prefix case that a strict zip would raise on.
        for i, (a, b) in enumerate(zip(py_prompt, native_prompt, strict=False)):
            if a != b:
                print(f"\nfirst char divergence at {i}:")
                print(f"  python: ...{py_prompt[max(0, i - 70):i + 70]!r}")
                print(f"  native: ...{native_prompt[max(0, i - 70):i + 70]!r}")
                break
        else:
            short, long_ = sorted((py_prompt, native_prompt), key=len)
            side = "python" if len(py_prompt) > len(native_prompt) else "native"
            print(f"\none is a prefix of the other; extra tail on {side}: {long_[len(short):]!r}")
    if not tokens_equal:
        for i, (a, b) in enumerate(zip(py_tokens, native_tokens, strict=False)):
            if a != b:
                print(f"first token divergence at index {i}: python={a} native={b}")
                break

    return 0 if (prompts_equal and tokens_equal) else 1


if __name__ == "__main__":
    raise SystemExit(main())
