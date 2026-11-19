### `preflight`: waiting (exit 0, 0.1 min of 10.0)

- free disk: 36 GiB
- sandbox/r140/ci/knaif-1.4.0-macos-arm64.zip: sha256 as run_all.sh names it
- sandbox/r140/ci/knaif-1.4.0-macos-arm64.pkg: sha256 a6639fdbb21c7c67f8e90d4667016bc4bf0d3627f86e607fd56dc0ba30e00f54
- tart and knaif-room-base: present
**For the owner:** knaif-1.3.0-macos-arm64.pkg does not match the release's SHA256SUMS

To answer, comment (only the owner's accounts are read):

    ```knaif-decision
    step: preflight
    action: continue        # continue | rerun | stop
    accept:                 # optional: rules in accepted.yaml's form
      - {kind: regression, row: <row id>, models: [<model>], backends: [<backend>]}
    note: <optional>
    ```

```knaif-report
{
 "step": "preflight",
 "status": "waiting",
 "rc": 0,
 "elapsed_min": 0.1,
 "budget_min": 10.0,
 "cells": []
}
```