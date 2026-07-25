"""GPU offload availability check — run with: uv run -m knaif._gpu_check

Exits 0 when llama_cpp reports GPU offload is supported, 1 otherwise.
"""

from __future__ import annotations

import sys


def main() -> None:
    from knaif.orchestrator import InferenceOrchestrator

    InferenceOrchestrator._prepare_llama_cpp_dlls()
    import llama_cpp

    ok = llama_cpp.llama_supports_gpu_offload()
    print("GPU offload supported:", ok)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
