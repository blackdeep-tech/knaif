### `cleanroom`: waiting (exit 0, 15.7 min of 120.0)

- host signatures: the .pkg notarized and accepted; every Mach-O in the .zip verifies

check_macho_deps --min-os 12.0 (information: TODO.md, the PDFium library declares 13):
```text
FAIL: 1 dependency/portability issue(s)

  - libpdfium.dylib: deployment floor is 13.0.0, expected 12.0.0 — MACOSX_DEPLOYMENT_TARGET was not honoured for this binary (it is not a rerun-if-env-changed input, so a stale cached build silently keeps the old floor — see D9)

An unresolved @rpath dependency resolves on THIS machine only because the file happens
to exist here (a build box, a Mac with Homebrew, ...). Stage it beside the binary, or
remove the dependency (see D3 for the OpenMP case).
```
**For the owner:** run1-zip: the VM did not come up
- PDFium on macOS 12 (information): 

PDFium on macOS 12:
```text
ssh: connect to host 192.168.64.24 port 22: Operation timed out
```
**For the owner:** run2-pkg-offline: the VM did not come up
**For the owner:** run3-pkg-upgrade: the VM did not come up

To answer, comment (only the owner's accounts are read):

    ```knaif-decision
    step: cleanroom
    action: continue        # continue | rerun | stop
    accept:                 # optional: rules in accepted.yaml's form
      - {kind: regression, row: <row id>, models: [<model>], backends: [<backend>]}
    note: <optional>
    ```

```knaif-report
{
 "step": "cleanroom",
 "status": "waiting",
 "rc": 0,
 "elapsed_min": 15.7,
 "budget_min": 120.0,
 "cells": []
}
```