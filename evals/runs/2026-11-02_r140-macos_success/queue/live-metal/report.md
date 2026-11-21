### `live-metal`: done (exit 3, 3.9 min of 30.0)

COMPLETE:
```text
2026-11-19 04:50:05 store: 1414 new or changed file(s) under evals/store to commit
2026-11-19 04:50:05 END store
2026-11-19 21:11:07 START live (macos)
2026-11-19 21:14:52 live documents r140-mac-1.7b: placement MTL0; 
2026-11-19 21:14:56 live documents r140-mac-1.7b: safety exit 0
2026-11-19 21:14:57 live 1.7b metal documents: NOT ACCEPTED - 1 of 124 thresholds unmet:
2026-11-19 21:14:57 GATE MISS live: 1.7b metal documents NOT ACCEPTED: goes to the owner
2026-11-19 21:14:57 END live: ran, with gate misses for the owner
```

| Cell | Misses already accepted | New misses |
|---|---|---|
| `1.7b/metal/documents (live)` | `documents_url_010` | none |

```knaif-report
{
 "step": "live-metal",
 "status": "done",
 "rc": 3,
 "elapsed_min": 3.9,
 "budget_min": 30.0,
 "cells": [
  {
   "cell": "1.7b/metal/documents (live)",
   "accepted": [
    {
     "kind": "regression",
     "row": "documents_url_010",
     "text": "documents_url_010#0: Python passed it, the binary did not ('error', expected 'clarify'). Python: clarify -> None; binary: no plan -> None"
    }
   ],
   "new": []
  }
 ]
}
```