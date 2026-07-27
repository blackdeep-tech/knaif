# Code Signing — certificate acquisition and the signing pipeline

**Status:** Planning · **Created:** 2026-07-27 · **Completed:** —
**Owner:** packaging · **Ref:** extracted from [windows-installer-polish](2026-07-25-windows-installer-polish.md) (W4, F5) · [`installers/windows/knaif.iss`](../../installers/windows/knaif.iss) · [`installers/package.sh`](../../installers/package.sh)

> **Why this is its own plan.** Signing was W4 of the installer-polish plan, but it is the only
> workstream there gated on an **external party** — every other one is code the owner can write
> today. Leaving it in place meant a plan that could never close. Extracted 2026-07-27 so the
> installer work ships as 1.0.2 while this waits on a certificate.
>
> **Deliberately NOT bound to the CI plan.** The obvious home would be
> [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md), since SignPath Foundation
> signs CI-built artifacts and `release.yml` lives there. That coupling is rejected: signing may be
> needed **sooner than CI lands**, and every path below except SignPath signs perfectly well from a
> developer machine — which is how v1 was cut anyway. CI integration is an optimisation recorded at
> the end, not a prerequisite.

**Goal:** Ship signed artifacts — `knaif.exe`, the bundled `llama.dll` / `ggml-*.dll`, `setup.exe`
and its uninstaller — from a repeatable, provider-agnostic pipeline, so that switching certificate
providers later changes one environment variable rather than the build.

---

## Decision log

**2026-07-27 — the SignPath Foundation application is deferred, not abandoned.** Their programme
favours established projects, and knaif is one release old. Revisit when the project has more
history and download volume. This is a timing judgement, not a change to the earlier finding that
knaif *qualifies* on licence and composition grounds.

**2026-07-25 — knaif stays OSS.** A future commercial product would *use* Apache-2.0 knaif rather
than ship it under other terms. That is not dual-licensing, so Foundation eligibility is unaffected
and no relicensing is ever required.

**Corollary — no CLA is needed, ever, for this purpose.** Apache-2.0 §5 already licenses inbound
contributions under the same terms. A CLA would only preserve *unilateral relicensing*, which the
decision above rules out. If outside PRs start, adopt a **DCO** sign-off instead: it certifies the
right to submit, changes no ownership, and costs no contributor goodwill. Not urgent — unlike a CLA,
which can only be adopted before the first outside contribution.

> **The constraint that survives, and it is easy to break later.** SignPath Foundation signs **OSS
> artifacts**. If a proprietary UI executable ever ships, it needs its **own certificate and its own
> artifact** — it must not be folded into the knaif installer, because that would put a proprietary
> component inside a Foundation-signed artifact and void the grant. Folding the UI into the existing
> installer is the obvious thing to do at that point, and it is precisely the move that breaks this.
> **Record it wherever UI packaging gets planned, not only here.**

---

## - [ ] S0 — Not blocked, do this now

- [ ] **Submit each published artifact to Microsoft's Security Intelligence portal** (the Defender
  false-positive / developer submission form). Free, needs no certificate, no entity, no owner
  decision. It addresses the **antivirus-detection** half of F5 — which signing would not fix
  anyway, since a signature does not exempt a binary from heuristic detection. An unsigned,
  low-reputation `setup.exe` that also ships loadable `ggml-*.dll` backends and downloads a 2.5 GB
  file post-install is exactly the shape that draws a heuristic hit.
  **Fold the submission into [`docs/RELEASE.md`](../RELEASE.md) as a release step, not a one-off.**

---

## Certificate landscape

Since June 2023 every publicly-trusted code-signing key must live on FIPS-140 hardware, so a plain
`.pfx` is no longer purchasable from any CA. Any pipeline design that assumes a key file on disk is
already obsolete.

> **No certificate buys a clean first download any more.** Microsoft removed EV's
> instant-SmartScreen privilege in 2024 (all EV code-signing OIDs were pulled from the Trusted Root
> Program roots in August 2024) because malware operators were buying EV certs through shell
> companies precisely to inherit that trust. **EV, OV and Azure Artifact Signing are now identical
> to SmartScreen** — each accrues reputation per file hash through download volume. Any purchasing
> argument resting on "EV skips the warning" is stale. The honest reasons to sign are integrity,
> enterprise allow-listing, and not looking abandoned.

| Option | Cost | Availability | Needs CI? | Notes |
|---|---|---|---|---|
| **SignPath Foundation** (OSS programme) | **Free** | Qualifying OSS projects | **Yes** | OV-level, key on their HSM. knaif qualifies on licence and composition; **deferred 2026-07-27 on project maturity** |
| **Azure Artifact Signing** (ex-Trusted Signing) | ~$9.99/mo | **Orgs:** US, CA, EU, UK · **Individuals:** US/CA only | No | No token; native GitHub Actions / Azure DevOps integration |
| OV cert on HSM token / cloud HSM | ~$150–300/yr | Worldwide | No | The fallback when the two above do not fit. A physical token makes unattended signing awkward; cloud HSM avoids that |
| EV cert on token | $400+/yr | Worldwide | No | **No longer justified for SmartScreen.** Only for enterprise procurement or kernel-mode drivers |

- [ ] **Verify Azure Artifact Signing geography at signup, not from the docs.** Microsoft's April
  2026 comparison lists **orgs in US/CA/EU/UK**, but an April 2025 service update restricted
  onboarding to **US/CA orgs with 3+ years of verifiable history**. The two have not obviously been
  reconciled.
- [ ] **Budget for annual renewal.** From **2026-03-01** the CA/Browser Forum caps publicly-trusted
  code-signing certificate validity at **458 days**, so any purchased-cert path is a roughly-annual
  renewal, not a 2–3 year one.
- [ ] **Re-evaluate SignPath Foundation** once knaif has more release history. Free and CI-integrated
  is the best long-term shape if the project grows into their criteria.

---

## - [ ] S1 — Payload signing

Two layers, and signing only the installer leaves every bundled DLL unsigned.

- [ ] **Sign `knaif.exe` and the shipped `llama.dll` / `ggml-*.dll`** after `cargo build`, before
  `package.sh` stages them. Hook via an env var — **`KNAIF_SIGN_CMD`** — applied per staged binary,
  so unsigned local builds keep working unchanged and no provider is baked into the script.
- [ ] **Decide the upstream-DLL question before signing them.** `llama.dll` and `ggml-*.dll` are
  built from llama.cpp via `llama-cpp-sys-2`, not from knaif-authored source. Some programmes —
  SignPath Foundation among them — restrict signing third-party binaries under their certificate,
  though they may travel unsigned inside a signed installer. Confirm the chosen provider's position
  rather than assuming; the answer changes what S1 signs.

## - [ ] S2 — Installer signing

- [ ] **`SignTool=knaifsign $f`** plus **`SignedUninstaller=yes`** in `[Setup]`, with the tool
  supplied at compile time (`ISCC /Sknaifsign="…"`). Without `SignedUninstaller`, `unins000.exe` is
  unsigned and carries its own SmartScreen friction — a detail that is easy to miss because the
  installer itself looks fine.
- [ ] **Always timestamp** (`/tr` + `/td sha256`). Untimestamped binaries stop validating the day the
  certificate expires, which with a 458-day cap is now a yearly cliff rather than a distant one.

## - [ ] S3 — Docs

- [ ] Update [`docs/RELEASE.md`](../RELEASE.md): the §6 "ships unsigned" note and the checksum
  guidance both change, and S0's Defender submission becomes a standing release step.
- [ ] Update [`installers/windows/README.md`](../../installers/windows/README.md) if it describes the
  artifact as unsigned.
- [ ] **Reconcile `AppPublisher` with the certificate subject** — this is
  [windows-installer-polish](2026-07-25-windows-installer-polish.md) W3's deferred item, and it
  belongs to whoever lands the certificate. The cert subject is **not self-declared**: the CA issues
  it from official registry records, so read the issued certificate rather than assuming it matches.
  Windows shows the cert subject as the *verified publisher* while Add/Remove Programs shows
  `AppPublisher`, so those two are the pair that must agree — a user comparing them cannot tell a
  benign mismatch from a malicious one. Leave `LICENSE`/`NOTICE` alone: they are ownership
  statements with no matching requirement against a certificate.

---

## Definition of done

A published artifact set where `knaif.exe`, the bundled backends, `setup.exe` and `unins000.exe` all
carry a valid, timestamped signature naming the same publisher that Add/Remove Programs shows; the
signing command is injected by environment variable so no provider is hard-coded; and `RELEASE.md`
describes the signed cut, including the Defender submission step.

---

## Out of scope

- **CI-driven signing.** When [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md)
  lands `release.yml`, the `KNAIF_SIGN_CMD` hook from S1 is where a CI secret plugs in. That is an
  optimisation of this plan, **not a dependency of it** — a hand-cut signed release is a complete
  outcome. If SignPath Foundation is ever adopted, this becomes a hard dependency, since it signs
  only CI-built artifacts; revisit the ordering at that point and not before.
- **macOS notarization** — no macOS installer exists yet.
- **Kernel-mode or driver signing** — knaif ships no drivers.
