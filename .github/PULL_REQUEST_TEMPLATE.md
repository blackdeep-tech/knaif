<!-- Title: type(scope): subject — imperative, no trailing period, ~65 chars
     (GitHub appends " (#123)"). See CONTRIBUTING.md -> Git conventions. -->

# Summary

<!-- What changed, and where. Link any issue. -->

## Checks

- [ ] `just check` passes (Python + native)
- [ ] Ran on: Windows x64 / Linux x64 / both <!-- delete the rest -->

## Judgement calls

<!-- Delete any that don't apply. Hooks already cover formatting, generated-copy drift,
     the README skill inventory, and notebook outputs. -->

- [ ] Planning, validation, or expansion changed — `just parity <skill>` shows no new divergence
- [ ] Skill behaviour changed — `just eval-success <skill>` quoted, not `cheap`, and
      `just eval-regression <skill>` passes
- [ ] Snapshot re-lock — own PR, measured improvement stated
- [ ] New dependency — permissive licence (no GPL/AGPL/LGPL/SSPL); if it ships in the
      wheel rather than a dev extra, `just licenses-all` re-run and committed

## Notes

<!-- Optional. Known gaps, or follow-ups left out of scope. -->
