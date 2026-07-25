# Auto-imported by Python at interpreter startup for any process whose sys.path
# includes this file's directory (i.e. anything using python/training/.venv —
# scripts, REPL, notebooks). It pins Unsloth's generated trainer cache under
# python/training/cache/unsloth_compiled so it never lands in the repo root as
# ./unsloth_compiled_cache (Unsloth's CWD-relative default).
#
# This is the tracked source. It is copied into the training venv's
# site-packages so the interpreter finds it. After rebuilding the venv, re-copy:
#   cp python/training/sitecustomize.py python/training/.venv/lib/python*/site-packages/
# See docs/FINE_TUNING.md.
#
# Each python/training/*.py already sets UNSLOTH_COMPILE_LOCATION; this is the
# belt-and-suspenders guard for ad-hoc or interactive sessions that forget to.
# It uses setdefault, so an exported override still wins.
import os
from pathlib import Path

if "UNSLOTH_COMPILE_LOCATION" not in os.environ:
    # Walk up from this file to the nearest ancestor named "training" (works
    # whether we run from the tracked location or the copy in site-packages).
    for parent in Path(__file__).resolve().parents:
        if parent.name == "training":
            os.environ["UNSLOTH_COMPILE_LOCATION"] = str(parent / "cache" / "unsloth_compiled")
            break
