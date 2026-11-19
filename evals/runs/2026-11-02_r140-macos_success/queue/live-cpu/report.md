### `live-cpu`: done (exit 3, 11.2 min of 60.0)

COMPLETE:
```text
2026-11-19 21:14:57 GATE MISS live: 1.7b metal documents NOT ACCEPTED: goes to the owner
2026-11-19 21:14:57 END live: ran, with gate misses for the owner
2026-11-19 21:15:00 START live (macos)
2026-11-19 21:25:58 live documents r140-mac-cpu-1.7b: placement CPU; 
2026-11-19 21:26:10 live documents r140-mac-cpu-1.7b: safety exit 0
2026-11-19 21:26:10 live 1.7b cpu documents: NOT ACCEPTED - 1 of 124 thresholds unmet:
2026-11-19 21:26:10 GATE MISS live: 1.7b cpu documents NOT ACCEPTED: goes to the owner
2026-11-19 21:26:10 END live: ran, with gate misses for the owner
```

| Cell | Misses already accepted | New misses |
|---|---|---|
| `1.7b/cpu/documents (live)` | `documents_url_010` | none |

```knaif-report
{
 "step": "live-cpu",
 "status": "done",
 "rc": 3,
 "elapsed_min": 11.2,
 "budget_min": 60.0,
 "cells": [
  {
   "cell": "1.7b/cpu/documents (live)",
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