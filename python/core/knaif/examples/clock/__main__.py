"""Run the clock example: ``python -m knaif.examples.clock "what time is it in Tokyo"``.

Delegates to the ``@nk.command`` variant. The ``from_click()`` variant is the same app
built from an existing ``click`` group — run it with
``python -m knaif.examples.clock.app_click``.

Inference is resolved at run time, best-effort: Ollama if it is reachable, else a local
GGUF if one is present, else mock inference. Mock needs no model and still exercises the
whole pipeline, so this always does *something* on a fresh install.
"""

from __future__ import annotations

import knaif.cli as nk
from knaif.examples.clock.app_decorator import convert, diff, now, zones


def main() -> None:
    orchestrator = nk.local_ollama() or nk.local_llama_cpp("model.gguf")
    nk.App([now, convert, diff, zones], orchestrator=orchestrator).run()


if __name__ == "__main__":
    main()
