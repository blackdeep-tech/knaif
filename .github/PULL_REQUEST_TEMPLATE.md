<!-- PR TITLE: this PR is squash-merged, so the title becomes the commit subject on main.
     It must follow the commit convention — `type(scope): subject`, imperative, no
     trailing period. Keep it to ~65 characters: GitHub appends " (#123)".
     See CONTRIBUTING.md -> Git conventions.
       feat(ffmpeg): add a batch-convert intent
       fix(sdk): validate Arg schemas on Python 3.10 -->

# What and why

<!-- The diff already shows what changed — explain why. The problem being solved, the
     approach, anything you rejected. Link any issue. -->

## Checks

- [ ] `just check` passes (Python + native)
- [ ] Ran on: **Windows x64 / Linux x64 / both** <!-- delete the ones that don't apply -->

There is no CI yet, so the line above is the only evidence a reader has. knaif ships on
Windows and Linux and some failures are platform-specific.

## Judgement calls

<!-- The hooks (`just hooks-install`) already cover generated-copy drift, the README skill
     inventory, formatting, and notebook outputs — those are not repeated here. What is
     left is what no machine can decide for you. Delete any line that doesn't apply. -->

- [ ] **Planning, validation, or expansion changed** — `just parity <skill>` shows no new
      divergence. The two runtimes disagreeing is a bug even when every test passes.
- [ ] **A skill's behaviour changed** — quote `just eval-success <skill>`, never `cheap`,
      and `just eval-regression <skill>` passes against the committed snapshot.
- [ ] **Re-locking a snapshot** — this is its own PR, and the description below says which
      measured improvement justifies moving the bar.
- [ ] **New dependency** — permissively licensed (no GPL/AGPL/LGPL/SSPL). If it ships in
      the wheel rather than a dev extra, `just licenses-all` was re-run and committed.

## Notes

<!-- Anything non-obvious: a rejected alternative, a known gap, a follow-up deliberately
     left out of scope. Written for whoever reads this in six months — usually you. -->
