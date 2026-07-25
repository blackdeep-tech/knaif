# What and why

<!-- What changes, and what problem it solves. Link any issue. -->

## Checks

<!-- There is no CI yet — local runs are the only gate. Say which platform you ran on. -->

- [ ] `just check` passes (Python + native)
- [ ] Ran on: <!-- Windows x64 / Linux x64 / both -->

## If you touched...

<!-- Delete what doesn't apply. -->

**Planning, validation, or expansion** — both runtimes must agree:

- [ ] `just parity <skill>` shows no new divergence

**A skill's behaviour**:

- [ ] `just eval-success <skill>` — quote the number, not `cheap`
- [ ] `just eval-regression <skill>` passes against the committed snapshot
- [ ] If re-locking the snapshot: it's in **its own commit**, and this PR says what
      measured improvement justifies moving the bar

**`contracts/runtime/core_tools.yaml`**:

- [ ] Edited the canonical file and ran `just sync-runtime`

**A `skill.yaml`**:

- [ ] Ran `just gen-skills` and committed the regenerated README inventory

**Dependencies**:

- [ ] Ran `just licenses-all` and committed both reports
- [ ] New dependencies are permissively licensed (no GPL/AGPL/LGPL/SSPL)

**Notebooks**:

- [ ] Outputs cleared

## Notes for the reviewer

<!-- Anything non-obvious: a rejected alternative, a known gap, a follow-up you're
     deliberately leaving out of scope. -->
