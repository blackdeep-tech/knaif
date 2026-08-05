---
title: Connecting a model
description: Point the SDK at Ollama or a local GGUF — and avoid the reasoning-model default that hangs.
sidebar:
  order: 4
---

knaif is local-only. There is no key to configure and no hosted endpoint; you point it at
a model running on the same machine.

## Ollama

```python
import knaif.cli as nk

orch = nk.local_ollama(model="qwen3:4b")   # None + a warning if Ollama is unreachable
app = nk.App([now], orchestrator=orch)
```

**Prefer `local_ollama()` over building the orchestrator yourself.** It is not a
convenience wrapper — it picks the settings that make reasoning models work.

:::danger[The raw constructor hangs on reasoning models]
`InferenceOrchestrator`'s defaults are `json_mode=True` and a 256-token budget. Against a
reasoning model (Qwen3, DeepSeek-R1) that combination **hangs and then times out**, with
nothing in the output to explain why.

The cause is not obvious: `think: false` does *not* stop the model reasoning. It only
stops Ollama separating the reasoning from the answer — so the reasoning lands in
`message.content`, destroys the JSON, and the JSON-constrained decode never terminates.

`local_ollama()` sets `thinking_enabled=True`, `json_mode=False`, `max_tokens=2048`, which
is why it works.
:::

If you do construct it directly, pass the same three:

```python
from knaif.orchestrator import InferenceOrchestrator

orch = InferenceOrchestrator(
    backend="ollama",
    model_name="qwen3:4b",
    model_config={"json_mode": False, "thinking_enabled": True, "max_tokens": 2048},
)
```

## llama.cpp (GGUF)

```python
orch = InferenceOrchestrator(backend="llama_cpp", model_path="models/qwen3-4b.gguf")
app = nk.App([now], orchestrator=orch)
```

## No model at all

Leave the orchestrator out and `App.invoke()` uses a **mock backend** with seeded
responses. This is the right default for tests: it exercises retrieval, validation,
coercion and dispatch without downloading anything, so CI stays fast and offline.

```python
result = nk.App([now]).invoke("what time is it in Tokyo", dry_run=True)
```

## Which model

Start with a 4B. Below that, accuracy on argument extraction drops off sharply, and
knaif's own evaluation says so rather than hiding it.

knaif publishes two fine-tunes of Qwen3 on HuggingFace — `knaif-qwen3-4b-v1` (2.5 GB, the
default) and `knaif-qwen3-1.7b-v1` (1.32 GB). Both are trained on its bundled skills, so
they are a reasonable starting point for an SDK app too, though they are tuned for *those*
skills' vocabulary — if your commands are far from media and document work, a stock
instruct model may route just as well. Measuring beats guessing; see
[Evaluate a skill](/evaluate/) for the harness.

Names, sizes, checksums and the evidence behind each choice: [Released models](/models/).

## A custom backend

To use something knaif does not ship, implement the `InferenceBackend` protocol from
`knaif.orchestrator` and pass the instance straight to `App`:

```python
from collections.abc import Iterator

class MyBackend:
    def infer(self, prompt: str) -> str: ...
    def infer_stream(self, prompt: str) -> Iterator[str]: ...

app = nk.App([now], orchestrator=MyBackend())
```

The contract is deliberately small — prompt in, text out. Everything knaif does with that
text is identical regardless of where it came from.
