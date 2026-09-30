# Release workflow — release branches, feature branches, one index per release

**Status:** Active · **Created:** 2026-09-30 · **Completed:** —
**Owner:** owner + release · **Ref:** branch `docs/release-workflow`; lessons from
[release-1.2](2026-09-25-release-1.2.md)
**Release:** main

> **Status note:** workflow agreed with the owner 2026-09-30 (decisions below); plan approved the
> same day. W1–W4 done on `docs/release-workflow` (PR #65, awaiting owner review). W5 is in the
> PR description; W6 set and verified 2026-09-30; W7 happens when 1.3 opens.

**Goal:** Let more than one release be developed at once, each on its own branch with its own
short index of feature plans, with a fast lane for patch releases.

## Why

Release 1.2 shipped, but the process was hard to follow:

- [release-1.2.md](2026-09-25-release-1.2.md) held scope, work steps, a journal of status notes
  stacked at the top, and evidence — 1,026 lines. The current state was never visible at a glance.
- Feature plans do not say which release they belong to, and a plan that lives only on its
  feature branch is invisible from `main`.
- The branch model is written down nowhere. [RELEASE.md](../RELEASE.md) still says there is no CI,
  that a release merges "to `dev`", and walks through the one-off v1.0.0 OSS prep.
- Nothing moves an item from the TODO backlog into a release.

## Decisions (owner, 2026-09-30)

1. Every release has its own `release/X.Y.Z` branch; several may be open at once.
2. Each feature is one plan and one `feat/*` branch, merged into a release branch by PR.
3. A feature starts from **`main`** unless it needs code that exists only on a release branch, so
   it can still land in any release. Its release is chosen later.
4. A tested release branch merges to `main` with a merge commit (the existing merge policy) and is
   published from there.
5. Two lanes: **minor** (features, models) and **patch** (fixes). A patch must not change behavior
   on platforms already shipped; a new platform is a minor. macOS goes in 1.3.0.
6. The release index lives **on its release branch**; `main` carries only a one-line pointer.
7. Worktrees are **out of scope** for now (to be added later, in a gitignored `.worktrees/`).
8. The CHANGELOG is written once scope is final (at freeze) from the index, not maintained during
   the cycle.

## The workflow

### Branches

| Branch | Starts from | Merges into | Holds |
|---|---|---|---|
| `main` | — | — | released code, plus docs/site/plan records. **Release tags point at commits on `main`'s history.** |
| `release/X.Y.0` | `main` | `main` (merge commit) | one minor release in development |
| `release/X.Y.Z` (patch) | tag `vX.Y.(Z-1)` | `main` (merge commit) | fixes only |
| `feat/<topic>` | `main` (default) or its release | a release branch, by PR | one feature = one plan |
| `fix/<topic>` | the release it fixes | that release, by PR | one fix |
| `exp/<topic>` | anything | **never merged** | experiments and training runs; the result reaches a release as evidence or a decision |
| `docs/…`, `ci/…`, `site/…` | `main` | `main`, by PR | changes that ship in no release artifact |

**What must go through a release branch:** anything that ends up in a published artifact — the
binary, the installers, the wheel, a model, or the contracts and skill bundles they carry. Everything
else (plans, site, CI, eval records) may go straight to `main`.

### Branch rules

- **A feature is tied to a release only when you choose it.** Merging `main` into a feature to stay
  current is always fine; merging a release branch into it binds it to that release, so do that
  only after choosing (step 4 below) or when the feature needs that release's code.
- **Choosing a feature's release:** set `**Release:** X.Y.Z` in its plan, add its row to the release
  index, open the PR into `release/X.Y.Z`. CI tests the merge result; resolve conflicts by merging
  the release into the feature.
- **An idea that may not work** starts as `exp/`. If it proves out, start a `feat/` from `main`
  and carry over what is worth keeping.
- **Version bump only at freeze, on the release branch.** `main` always carries the last released
  version, so open releases do not fight over `Cargo.toml` / `pyproject.toml` / `knaif.iss`. If a
  patch ships after a minor has frozen, merging `main` into the minor conflicts on those three
  lines: keep the minor's version.
- **Carry releases forward.** When any release merges to `main`, every other open release branch
  merges `main` in — merge, never rebase. This is how a 1.2.1 fix reaches 1.3.0.
- **Tag the tested commit.** Before the final gates, merge `main` into the release branch; run the
  gates on that commit; merge it to `main` with a merge commit (same tree); tag the tested commit.
  The evidence SHA and the tag are then the same commit.
- **`release/*` is protected** by a ruleset: PR required, `ci` check required, no force-push.
  Deletion stays allowed, because merging the release PR auto-deletes the branch. (Owner task W6.)

### Release lanes

| | Minor `X.Y.0` | Patch `X.Y.Z` |
|---|---|---|
| Scope | features (plans), model changes, new platforms | code bug fixes only |
| Not allowed | — | a new or retrained model, prompt wording, `tools.yaml` / contract changes, new CLI flags, any behavior change on a platform already shipped |
| Starts from | `main` | the previous release tag |
| Gates | all of [RELEASE.md](../RELEASE.md) §4: L3, full L4 matrix, clean room, upgrade path | `just check`, the affected skill's L4 **sampled** (RELEASE.md §4's existing "sampled" evidence), clean room, upgrade path |
| If the rule is broken | — | the change moves to the next minor, or the patch runs the minor gates |

### Release lifecycle

1. **Propose** (optional) — a Draft index on `main`: goal, lane, rough scope. The existing
   [release-1.3](2026-09-27-release-1.3-skill-adapters-and-superskill.md) draft is this stage.
2. **Open** — create `release/X.Y.Z` from `main` (or from the tag, for a patch). The index becomes
   Active on the release branch. On `main`, add the row to *Releases in flight* in
   [README.md](README.md) and do not edit `main`'s copy of the index again, so the two never
   conflict when the release merges back.
3. **Develop** — `feat/*` and `fix/*` PRs into the release branch. Merge `main` in whenever `main`
   moves.
4. **Freeze** — no new scope. Bump the version. Write the CHANGELOG section from the index's
   scope table plus `git log --first-parent release/X.Y.Z`, so it is in the commit that gets
   tagged. Merge `main` in.
5. **Verify** — the lane's gates on the frozen commit; evidence to `evals/` as today. A fix after
   freeze goes through a `fix/*` PR, and the existing staleness rules decide which evidence reruns.
6. **Ship** — merge to `main`, tag the tested commit, publish per RELEASE.md §5. Merge `main` into
   every other open release branch. Delete the release branch.
7. **Close** — index to Done; remove the *Releases in flight* row.

### Planning documents

The canonical text now lives in [README.md](README.md): *Releases in flight*, the `**Release:**`
header field, and the *Release index* template. In short:

- **Release index** — `docs/plans/YYYY-MM-DD-release-X.Y.Z.md`, on its release branch. Short and
  fixed-shape (current state, exit gates, scope table, decisions, deferred, evidence links); it
  links to work, it does not hold it.
- **`**Release:**`** on every plan from 2026-09-30: `X.Y.Z`, `—` (not chosen) or `main` (ships in
  no release — docs, site, CI, process).
- **Who edits what** — a feature PR edits its own plan and its own scope row; the rest of the index
  is edited by whoever integrates the release.
- **Where to start** — *Releases in flight* at the top of README.md, changed only when a release
  opens or ships.
- **The backlog** — TODO.md *Open / Next* stays the list of unassigned items; assigning one to a
  release moves it into that release's scope table.

## Out of scope

- **Worktrees** and parallel agent sessions — postponed by the owner (decision 7).
- **Restructuring existing plans or TODO.md.** Old plans keep their headers; the `Release:` field is
  required only from this plan's date on.
- **CHANGELOG automation.** Writing it from the index is a manual ship step for now.

## Tasks

All of W1–W5 land on `docs/release-workflow` and go to `main` as one PR.

### - [x] W1 — RELEASE.md: branches and lanes

- [x] Add a *Branches and release lanes* section (the tables and rules above), before §0.
- [x] §0: the version bump happens at freeze, on the release branch.
- [x] Fix the stale intro (no `.github/workflows/`, "local green, not CI").
- [x] Replace §5 steps 1–4 (merge to `dev`, "there is no CI", v1.0.0 OSS prep) with the ship steps
  above; keep steps 5–8.
- [x] §5 *Rehearse*: it said no release had ever been published; now it rehearses only a publish
  path that has not run before (macOS in 1.3), and §5's steps get their own heading.

### - [x] W2 — plans/README.md: index template and pointer table

- [x] *Releases in flight* table at the top (empty until a release opens).
- [x] Release index template (as above) and the naming rule.
- [x] `**Release:**` in the plan header format, with the "required from 2026-09-30" rule.

### - [x] W3 — lint the release fields (test first)

In [test_plan_headers.py](../../python/core/tests/test_plan_headers.py), each rule written as a
failing test before the code:

- [x] Plans created on or after 2026-09-30 carry `**Release:**` with `X.Y.Z`, `—` or `main`.
- [x] A release index (`YYYY-MM-DD-release-X.Y.Z.md`) carries `**Lane:**` (`minor`/`patch`) and
  `**Branch:** release/X.Y.Z`, and its `Branch` matches its filename.
- [x] Two-way consistency, checked only against what is in the checkout: every plan with
  `Release: X.Y.Z` is listed in that release's index **if the index is present**, and every plan
  file the scope table links to (and that exists) says `Release: X.Y.Z`. Plans still on
  unmerged feature branches are skipped, so `just check` passes on a feature branch.
- [x] Fix `_plan_files()`'s `2026-*.md` glob, which stops checking every plan written from 2027 on.

### - [x] W4 — AGENTS.md pointer

- [x] *Plans and Todos*: one line each for the release index, the `Release:` field and
  *Releases in flight*, pointing at RELEASE.md for the branch rules.

### - [ ] W5 — record the open work under the new rules

No branch is recreated; these are the current feature branches, all started from `main`.

- [ ] List them in the PR description with their proposed release, for the owner to confirm:
  `feat/ai-skill-direct-calls`, `feat/local-site-analytics` (both hold only a plan; `Release: —`
  until chosen), `origin/feat/macos-support` (1.3.0 per decision 5).
- [ ] Their plans get the `Release:` line on their own branches, at their next commit — not from
  this branch.

### - [x] W6 — owner: `release/*` ruleset

- [x] In GitHub settings, a ruleset `release-branches` on `release/*`: PR required, `ci` required,
  no force-push; deletion allowed (the repo auto-deletes a merged PR's head branch, which is the
  ship step). Verify with `gh api repos/blackdeep-tech/knaif/rulesets`. Set and verified
  2026-09-30 (ruleset 24240930; status checks not required on creation).
- [x] `release-tags`: add *Restrict updates*. Today it blocks deletion and non-fast-forward
  updates only, so a tag can still be moved forward to a descendant commit. Done 2026-09-30:
  deletion, non-fast-forward and update are restricted; creation is not.

### - [ ] W7 — first use: open 1.3.0 (separate, when the owner starts 1.3)

- [ ] Create `release/1.3.0`; turn the 1.3 draft into the index on that branch; add the
  *Releases in flight* row on `main`; assign features. Not part of this branch's PR.
