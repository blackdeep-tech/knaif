# Contributor sign-off (DCO) and what knaif accepts

**Status:** Active · **Created:** 2026-11-17 · **Completed:** —
**Owner:** project / contributing · **Ref:** [DCO](../../DCO) ·
[CONTRIBUTING.md](../../CONTRIBUTING.md#what-we-accept)
**Release:** main

> **Status note:** the repository side is done on `feat/contributor-sign-off`. A CLA was drafted
> and reviewed first, then dropped the same day once the owner's goals were set out in full (see
> *Decisions*). What's left is the owner's steps below.

**Goal:** knaif stays open source under Apache-2.0, welcomes outside skills, keeps its core with
the company, and can show a buyer that the company owns the core and that every outside
contribution came in properly.

## The facts (owner, 2026-11-17)

- Blackdeep Technologies Ltd is 100% owned by the owner.
- The owner wrote about 99% of knaif. The rest is help from friends on the macOS version.
- knaif is to stay open source, with many people writing skills. Few people should touch the
  core. Skills may move to a repository of their own one day.
- knaif is to serve the owner and the company, and any future commercial benefit is to stay
  available to them.

## Decisions (owner, 2026-11-17)

- **The commercial path is selling the company.** A sale of Blackdeep moves nothing: knaif, and
  everything the company owns or has licensed, stays with it.
- **Others may build on knaif and sell it.** knaif stays **Apache-2.0**, and no licence change is
  planned. Versions already released stay Apache-2.0 for anyone who has them in any case.
- **The core is written by the owner and the company.** Outsiders open an issue first. Small
  fixes may be accepted, and larger changes are made by the maintainers. The company keeps 100%
  of the core.
- **Every skill comes in under Apache-2.0** ("inbound = outbound"). Authors keep their copyright,
  and the company, a future buyer and everyone else may use the skills commercially under that
  licence. The rule holds if skills move to their own repository.
- **Every outside commit is signed off under the Developer Certificate of Origin 1.1**
  (`git commit -s`), checked by the DCO GitHub app. Members of the `blackdeep-tech` organisation
  are exempt (`.github/dco.yml`). The sign-off is each contributor's statement that the change
  was theirs to give under the project's licence, which is the record a buyer checks. A squash
  merge of an outside pull request keeps its `Signed-off-by` lines in the squash message.
  **The app, not our own CI** (owner, 2026-11-18): a pull request cannot edit its way past the
  app, whereas a fork's pull request runs its own version of `ci.yml`. The app also checks a
  first-time contributor's pull request without waiting for workflow approval, and there is
  nothing to maintain. The trade-off is trusting a third-party app (it reads the repository and
  writes check results) and waiting if it is down.
- **The friends' macOS work is assigned to the company** in writing, so the company owns the whole
  core. That is the cleanest position for a buyer and keeps every option open, including a
  different licence for future versions.
- **No CLA.** A CLA (CLA Assistant, a relicensing right, Bulgarian law) was drafted first, and a
  same-day issue review listed what a sound one would need here: coverage of the first pull
  request, signatory versus rightholder, corporate authority, Bulgarian moral rights (Copyright
  and Related Rights Act Arts 15–16), the remuneration rules, the effect of an electronic
  signature, and a GDPR privacy notice for the signing form. With the core kept to the company,
  skills under Apache-2.0 and no licence change planned, a CLA would add nothing the plan needs,
  and no Bulgarian lawyer versed in open-source licensing was available to stand behind a
  self-drafted one. The DCO is a standard text that needs neither.

## Repository changes

- [x] `DCO` at the root: the Developer Certificate of Origin 1.1, verbatim, as its licence
  requires.
- [x] `.github/dco.yml`: the DCO app's configuration (members exempt, individual remediation
  commits allowed).
- [x] `CONTRIBUTING.md`: a *What we accept* section (skills welcome under Apache-2.0; core
  maintained by Blackdeep, issue first, small fixes only). The *Licence* section covers
  inbound = outbound Apache-2.0, how to sign off and fix a missing sign-off, and the squash-merge
  note. The dependency-licence paragraph is unchanged.
- [x] `.github/PULL_REQUEST_TEMPLATE.md`: an "every commit is signed off" check.
- [x] `README.md` *Contributing*: a link to the DCO.

No code or workflow changes: the DCO app runs on GitHub's side.

## Owner's steps (outside the repository)

- [ ] **1. The company owns the owner's code.** LICENSE, NOTICE, the installer, the binaries'
  version information and the sites name Blackdeep Technologies Ltd as the copyright holder, and
  the owner decided (2026-11-17) that the company holds it. The owner works for the company under
  a management contract, not an employment contract. So the rule that gives an employer the rights
  in software an employee writes as part of their job does not apply, and the code stays the
  owner's personally until it is assigned in writing (the law requires writing). A short written
  assignment from the owner to the company of the economic rights in knaif and the related
  software, past and future, makes the notices true. Because the owner signs for both sides, the
  company side is approved by a written decision of the sole owner. Whether the transfer is free
  or for a price is a tax question for the accountant. This is what a buyer checks first, and a
  company lawyer or accountant can do it without open-source expertise.
- [ ] **2. The friends' macOS work.** A short written assignment from each friend who contributed
  that their contributions to knaif belong to Blackdeep. No file in the repository names them.
- [x] **3. Install the DCO app** (https://github.com/apps/dco) on the `blackdeep-tech`
  organisation, for the public `knaif` repository. Installed 2026-11-18 on selected
  repositories only; it reads code, pull requests and members, and writes checks.
- [ ] **4. Require it.** After the first pull request shows the app's check (named `DCO`; confirm
  it there), add it as a required status check in the branch protection or ruleset for `main`.
