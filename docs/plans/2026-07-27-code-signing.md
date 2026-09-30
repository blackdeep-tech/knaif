# Code Signing — certificate acquisition and the signing pipeline

**Status:** Planning · **Created:** 2026-07-27 · **Completed:** —
**Owner:** packaging · **Ref:** extracted from [windows-installer-polish](2026-07-25-windows-installer-polish.md) (W4, F5) · [`installers/windows/knaif.iss`](../../installers/windows/knaif.iss) · [`installers/package.sh`](../../installers/package.sh)
**Release:** 1.2.1

> **Why this is its own plan.** Signing was W4 of the installer-polish plan, but it is the only
> workstream there gated on an **external party** — every other one is code the owner can write
> today. Leaving it in place meant a plan that could never close. Extracted 2026-07-27 so the
> installer work ships as 1.1.0 while this waits on a certificate.
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

**2026-09-30 — the owner will issue the Azure Artifact Signing certificate (~$10/mo) for Windows,
aiming at 1.2.1.** Signing the installer changes the artifact, not what knaif does, so it is
proposed for the patch lane; the patch gates (clean room, upgrade over an unsigned install) must
pass with a signed installer, and if they cannot, this moves to 1.3.0. The Microsoft Store (MSIX)
and winget are not pursued now: Store distribution is revisited with the UI app, and until then
the direct download keeps its SmartScreen prompt. The owner-side steps are the runbook at the end
of this plan. macOS signing is a separate plan on the 1.3.0 branch, `macos-signing-certificates`.

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

---

## Owner runbook — getting the Windows signing certificate

*Added 2026-09-30.* Everything the owner has to obtain for Windows, in order. Portal labels and
product names change often (Azure renamed Trusted Signing to Artifact Signing in 2025–26) — where a
step says *verify*, trust the portal over this text. Nothing here goes into the repository:
certificates, keys and identities live only in Azure or in GitHub Actions secrets. The macOS
equivalent is its own plan, `macos-signing-certificates`, on the 1.3.0 release branch.

| Item | From | Cost | Used for |
|---|---|---|---|
| Artifact Signing account + certificate profile | Azure | ~$9.99/mo (Basic) | `knaif.exe`, DLLs, `setup.exe`, uninstaller |

**Step 0 — check eligibility before paying for anything.** The two Microsoft statements in the
landscape section above disagree, so this is the gate. Organisations need a legal entity Microsoft
can verify, and the published onboarding rules have required a period of verifiable business
history (3+ years at one point) and a supported country (US, Canada, EU, UK for organisations;
individuals US/Canada only). Read the current requirements on Microsoft Learn for *Artifact
Signing* and check Blackdeep's registered country and incorporation date against them. **If it does
not qualify, stop here** and take the fallback: an OV certificate on a cloud HSM (~$150–300/yr),
or revisit SignPath Foundation. Do not build the pipeline first.

1. **Azure subscription.** Use a pay-as-you-go subscription owned by the company. A free trial
   subscription is generally not accepted for this service — *verify*.
2. **Create the account.** Portal → *Artifact Signing accounts* → Create. Pick a region close to the
   CI runners (for the EU, West Europe). The region fixes the signing **endpoint URL** (for example
   `https://weu.codesigning.azure.net`) — write it down. Choose the **Basic** tier.
3. **Identity validation.** In the account, *Identity validation* → New → **Organization**. You
   enter the legal name, registration number, address and a contact; Microsoft's verification
   partner checks them against official records. This takes from minutes to several days and may
   ask for documents. You need the *Artifact Signing Identity Verifier* role on the account
   (assign it to yourself under *Access control*).
4. **Certificate profile.** After validation succeeds: *Certificate profiles* → Create → type
   **Public Trust**, pick the validated identity. Write down the account name and profile name.
   The certificate subject is taken from the validated identity — **read what it says**; it must
   match `AppPublisher` in `installers/windows/knaif.iss` (S3 above).
5. **A signing identity for CI.** Entra ID → App registrations → New (for example `knaif-signing`).
   Grant it the **Artifact Signing Certificate Profile Signer** role on the account. Then add a
   **federated credential** for GitHub Actions (repository `blackdeep-tech/knaif`, the environment
   that runs the release job) so CI authenticates by OIDC and **no client secret exists**.
6. **Hand over** the values in the secrets table below. The certificates Microsoft issues are
   short-lived and rotate on their own, which is why every signature must carry a timestamp
   (`http://timestamp.acs.microsoft.com`) — S2 already requires it.
7. **Local signing** (before CI exists) needs `signtool` plus Microsoft's Artifact Signing client
   (`Azure.CodeSigning.Dlib`) and a small `metadata.json` holding the endpoint, account and profile
   names. That is what `KNAIF_SIGN_CMD` in S1 should wrap.

**Expect:** the first signed installer still gets the SmartScreen prompt. Signing changes the
publisher from "Unknown" to Blackdeep's name and lets reputation build across releases; it does
not skip the prompt (see the landscape section). Keep the Defender submission (S0) going.

### GitHub Actions secrets

Create them as **environment secrets** on a protected `release` environment (required reviewer:
the owner), so only a tag build the owner approves can reach them. Names are suggestions; the
workflow and this table must agree when it is written.

| Secret | Holds |
|---|---|
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_SUBSCRIPTION_ID` | the Entra app from step 5 (not secret in themselves; no password exists) |
| `ARTIFACT_SIGNING_ENDPOINT`, `ARTIFACT_SIGNING_ACCOUNT`, `ARTIFACT_SIGNING_PROFILE` | endpoint URL, account name, certificate profile name |

### Housekeeping

- **Renewals.** The profile is continuous and the subscription bills monthly — set a budget alert so
  a lapsed payment is noticed before a release day.
- **Revocation.** If the signing identity leaks: remove its Azure role assignment and delete the
  Entra app. The repository never held any secret.
- **Public output.** The certificate subject appears inside published binaries. Follow AGENTS.md
  *Public Output Hygiene*: no personal names or machine paths beyond what the subject itself shows.
