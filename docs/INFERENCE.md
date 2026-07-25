# Inference and Model Setup

**This doc is about *backends* — how to get inference running.** For the models themselves —
which ones are released, where they live on HuggingFace, and why they were chosen — see
[MODELS.md](MODELS.md).

knaif supports three inference paths: **mock** (always available, no setup), **Ollama**
(a running server), and **llama.cpp** (in-process GGUF, optionally GPU-accelerated).
Tests and `just eval` use mock inference by default, so nothing here is required to
develop against the planner.

## Runtime models — `models.yaml`

[`models.yaml`](../models.yaml) at the repo root resolves `--model NAME` (or a skill's
`recommended_model`) to a full backend configuration. Its schema mirrors
[`eval_backends.yaml`](../eval_backends.yaml), but the role is different:

- **`models.yaml`** — the *one* model used at runtime for a CLI invocation or library call.
- **`eval_backends.yaml`** — every backend the eval suite benchmarks side-by-side.

Resolution precedence at runtime (highest first):

1. `--model-path PATH` (raw GGUF, no tuning options applied)
2. `--model NAME` (looked up in `models.yaml`)
3. The skill's `recommended_model` from `skill.yaml`
4. `models.yaml`'s top-level `default:`
5. Mock inference (`--backend mock`)

So `just cli ffmpeg compress video.mp4` with no flags picks up `ffmpeg`'s recommended
model with full GPU offload, while `just cli ffmpeg compress video.mp4 --model qwen3-1.7b`
overrides it for a single run. Edit `models.yaml` to add, remove, or retune entries.

### Is a local 4B actually good enough?

For the everyday cases, yes — and it is worth knowing that before you spend an afternoon on
backend setup. On eleven real-world ffmpeg requests, the promoted `knaif-qwen3-4b-v1`
produced a correct, `ffprobe`-verified artifact for all nine artifact requests, matching
Claude Code (`opus-4-8`), GitHub Copilot CLI (`sonnet-5`), and OpenAI Codex CLI (`gpt-5.5`)
at 9/9 each — at ~1.2 s per request instead of 11–16 s, and at zero marginal cost. The gap
to a premium agent is concentrated in the hard, ambiguous, and multilingual tail (0.967 vs
0.989 success over the full 846-utterance corpus), not in bread-and-butter work. See
[the full experiment](experiments/2026-07-02-agent-vs-knaif-realworld.md); latency by
machine and backend is in [PERFORMANCE.md](PERFORMANCE.md).

The native runtime resolves models through its own store — `knaif models pull <name>`
verifies the download against a pinned SHA-256 from
[`contracts/models/model-manifest.yaml`](../contracts/models/model-manifest.yaml). See
[NATIVE.md](NATIVE.md).

## Ollama

1. Install Ollama from [ollama.com](https://ollama.com):

   ```bash
   winget install Ollama.Ollama   # Windows
   brew install ollama            # macOS
   ```

2. Pull the models used in `eval_backends.yaml`:

   ```bash
   ollama pull qwen3:4b
   ollama pull qwen3:1.7b
   ollama pull phi4-mini
   ```

3. Start the server (runs in the background):

   ```bash
   ollama serve
   ```

4. Run the eval suite against it. **`eval_backends.yaml` ships no Ollama entries** — every
   committed backend is llama.cpp — so add one first (see the settings block below; the
   defaults are wrong for a reasoning model), then:

   ```bash
   uv run -m knaif.evalsuite run --skill ffmpeg --config eval_backends.yaml \
     --backends qwen3-4b-ollama --verifier cheap
   ```

### Reasoning models on Ollama — leave thinking ON

Counterintuitive, and the single most likely thing to waste an afternoon. With a
reasoning model (Qwen3, DeepSeek-R1), the settings that *look* right produce unusable
output:

| Payload | `message.thinking` | `message.content` | Result |
|---|---|---|---|
| `think: true` | the reasoning | **clean JSON** | works |
| `think: false` | *(absent)* | **the reasoning** | plan won't parse |

`think: false` does **not** stop the model reasoning — it only stops Ollama *separating*
the reasoning, which then lands in `content` and destroys the JSON. Left enabled, Ollama
quarantines it in `message.thinking` and knaif reads `content`, which is clean. Measured
on Ollama 0.32.3 + `qwen3:4b`: identical prompt, 2,900–5,500 characters of reasoning
either in `thinking` (parses) or in `content` (does not). Neither `think: false` nor the
`/no_think` system-prompt suffix suppressed the reasoning itself.

Two consequences for configuration:

- **`json_mode` must be off.** Ollama's `format: "json"` demands valid JSON from the
  first token, which a reasoning template cannot satisfy while emitting its preamble.
  Generation never completes and the request times out rather than failing fast.
- **Raise `max_tokens`.** Ollama counts reasoning against the budget. A preamble can run
  well past a thousand tokens, so the agent-level default of 256 is spent before the
  answer begins — the symptom is a `clarify` ("could not parse"), not an error.

`nk.local_ollama()` already defaults to all of this (`thinking_enabled=True`,
`json_mode=False`, `max_tokens=2048`), so SDK users get working behaviour without knowing
any of the above. Set them explicitly in `eval_backends.yaml` / `models.yaml`, where the
defaults do not apply:

```yaml
  qwen3-4b-ollama:
    backend: ollama
    model: qwen3:4b
    options:
      json_mode: false        # `format: json` deadlocks a reasoning template
      thinking_enabled: true  # let Ollama split reasoning out of `content`
      max_tokens: 2048        # reasoning is charged against this
```

**This applies to Ollama only.** The llama.cpp path has no `think` parameter; it uses a
`/no_think` system-prompt suffix and is unaffected by any of the above.

> **Quality note.** Stock `qwen3:4b` routes correctly with these settings but spends
> 15–30 s per request reasoning its way there. knaif's fine-tuned models are trained to
> emit the plan directly. To get one into Ollama, import the GGUF:
> `ollama create knaif-qwen3-4b -f Modelfile` with `FROM ./knaif-qwen3-4b-v1-q4_k_m.gguf`.

## llama.cpp

The `llama` extra builds `llama-cpp-python`. Install it *after* the base install.

```bash
just install-llama
```

What that gives you depends on the platform:

| Platform | Result | Requirements |
|---|---|---|
| Windows | prebuilt CPU wheel from PyPI | none |
| Linux | source build, CPU | a C/C++ compiler and CMake (`sudo apt install build-essential cmake`) |
| macOS | source build with **Metal GPU support**, enabled automatically | Xcode command-line tools |

Then download a GGUF model (e.g. `Qwen3-4B-Instruct-Q4_K_M.gguf`) into `models/`, or let
the CLI resolve one through `models.yaml`.

### NVIDIA GPU (CUDA)

```bash
just install-cuda
```

This recipe is platform-split, and each branch finishes by running `just gpu-check`, which
exits non-zero if the resulting build is CPU-only. There is no need to eyeball load traces
to confirm offload — but you can, by setting `verbose: true` on a backend entry once:
llama.cpp then prints `using device CUDA0 (<your GPU>)` and per-layer offload lines.

- **Windows** — installs a prebuilt CUDA 12.4 wheel from
  `https://abetlen.github.io/llama-cpp-python/whl/cu124`, pinned with `--index-url` +
  `--no-deps` so the resolver can't silently fall back to the identically-versioned CPU
  wheel on PyPI. Because `--no-deps` also skips the package's own runtime dependencies,
  they are reinstalled explicitly. Adds `nvidia-cuda-runtime-cu12` + `nvidia-cublas-cu12`
  (about 1.4 GB of CUDA runtime DLLs in the venv).
- **Linux (incl. WSL2)** — no usable prebuilt CUDA wheels exist, so it builds from source
  against the local CUDA toolkit. Needs `build-essential`, CMake, and `nvcc` at
  `/usr/local/cuda/bin/nvcc` (override with `just cuda_nvcc=... install-cuda`). Target
  architecture comes from `cuda_arch`, which defaults to `native` — pin a number to
  cross-build without the GPU visible, e.g. `just cuda_arch=120 install-cuda` for
  Blackwell / RTX 50xx. On WSL the host Windows driver supplies the GPU: install only the
  toolkit, never a Linux display driver.
- **macOS** — errors out. CUDA is NVIDIA-only; use `just install-llama`, which enables
  Metal automatically.

The CUDA install is **not** declared in `pyproject.toml`, because alternate package indexes
can't be expressed in PEP 621 metadata. Each clone with a GPU has to run `just install-cuda`
once after `just install`.

### Verifying offload

```bash
just gpu-check      # exits non-zero if the installed build is CPU-only
```

## Adding or changing backends

Edit [`eval_backends.yaml`](../eval_backends.yaml) to add models or adjust options
(temperature, context size). Each entry maps a backend name to its type and model
reference. Pass `--backends name1,name2` to run a subset:

```bash
uv run -m knaif.evalsuite run --skill ffmpeg --config eval_backends.yaml \
  --backends qwen3-4b --verifier cheap
```

## Mock inference

`InferenceOrchestrator` falls back to mock inference with no model present. Mock routing is
driven by each tool's registry metadata — `keywords`, `mock_args`, and `unsafe_phrases` —
which is why the whole test suite and `just eval` run without a download. See
[TOOL_SCHEMA.md](TOOL_SCHEMA.md).

## Native runtime GPU backends

The Rust runtime selects its backend through Cargo features rather than a pip extra
(`llama,pdfium` by default; `llama,vulkan,pdfium` or `llama,cuda,pdfium` for GPU). The
first build with a given GPU backend compiles llama.cpp's kernels and can take 15–30
minutes. See [NATIVE.md](NATIVE.md) and the `native-cuda` / `native-vulkan` recipes in the
[justfile](../justfile).
