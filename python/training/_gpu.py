"""GPU memory guards shared by the training entry points.

Standalone scripts, not a package: they run as `python python/training/train_lora.py`
and are also driven by `runpy.run_path` from the repo root, so each importer puts this
directory on `sys.path` explicitly rather than relying on `sys.path[0]`.
"""

from __future__ import annotations

import os

import torch


def cap_allocator_to_device_memory() -> None:
    """Bound the PyTorch caching allocator to a fraction of physical VRAM.

    **This does not make a model fit that otherwise would not.** It does not offload
    weights, quantize, or shrink anything: the whole base model is resident either way
    (a LoRA trains ~0.8% of the parameters but reads all of them). What it bounds is how
    far the *caching allocator* may grow before it must reuse what it already holds.

    That matters on WDDM — Windows, and WSL2 through the same driver — because an
    allocation the card cannot satisfy does not fail there. The driver silently backs it
    with system RAM across PCIe, so the run keeps going at a fraction of the speed with no
    error to notice. A 4B bf16 LoRA hit exactly that on the 16 GB RTX 5080 on 2026-09-17:
    15.99 GB dedicated **plus 6.69 GB shared**, step time drifting 15.3s -> 17.5s and still
    climbing, no CUDA error at all. Capped to 0.8 the same run stayed resident and finished
    all 288 steps in ~61 min (~12.7 s/step).

    So the cap converts a silent crawl into an honest `CUDA out of memory`. If you see that
    OOM, first try a smaller batch/sequence/rank. Raising the fraction is legitimate when
    the card really does have more room — it is capped against *total* device memory, not
    what is free — but it moves you back toward the spill, so raise it deliberately and
    watch the shared-memory counter rather than nudging it until the error goes away.

    Override with `KNAIF_TRAIN_MEM_FRACTION`; `0` or `1` disables it. 0.8 was chosen to clear
    the spill on a 16 GB card and is **not** a tuned optimum — no cap/throughput sweep has
    been run. See docs/FINE_TUNING.md section 2.
    """
    raw = os.environ.get("KNAIF_TRAIN_MEM_FRACTION", "0.8")
    try:
        fraction = float(raw)
    except ValueError:
        print(f"[mem] ignoring KNAIF_TRAIN_MEM_FRACTION={raw!r}: not a number", flush=True)
        return
    if not torch.cuda.is_available():
        print("[mem] allocator cap skipped: no CUDA device", flush=True)
        return
    if not 0.0 < fraction < 1.0:
        # Reset rather than return. These scripts are also driven through `runpy` inside a
        # long-lived process, where an earlier call may already have installed a cap — and
        # "disabled" that silently leaves the previous limit in force is a lie in the log.
        torch.cuda.set_per_process_memory_fraction(1.0)
        print(f"[mem] allocator cap disabled (fraction={fraction}); limit reset", flush=True)
        return
    torch.cuda.set_per_process_memory_fraction(fraction)
    total_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(
        f"[mem] allocator capped at {fraction:.0%} of {total_gb:.1f} GiB "
        f"(~{total_gb * fraction:.1f} GiB) to keep WDDM from spilling into shared memory",
        flush=True,
    )
