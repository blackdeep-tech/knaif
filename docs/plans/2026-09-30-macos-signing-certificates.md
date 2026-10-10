# macOS signing — certificates and notarization credentials

**Status:** Planning · **Created:** 2026-09-30 · **Completed:** —
**Owner:** owner · **Ref:** macOS plan §9 (workstream F), currently on `feat/macos-support`; Windows
equivalent: `code-signing` plan, on the 1.2.1 release branch
**Release:** 1.3.0

> **Status note:** owner runbook only — the owner-side steps to obtain what macOS signing needs. The
> signing and notarization *pipeline* is built in the macOS plan §9 (F1–F6); this plan is what
> that one waits on. Portal labels change often; trust Apple's pages over this text.

**Goal:** Get the Developer ID certificates, the notarization key and the CI secrets in place so
the macOS plan can sign and notarize a build.

The Apple Developer Program membership already exists (Blackdeep) and renews yearly. **If it
lapses, new builds can no longer be signed or notarized**; software already notarized keeps
working. Nothing here goes into the repository: certificates, keys and passwords live only in a
password manager or in GitHub Actions secrets.

## What you need at the end

| # | Item | From | Cost | Used for |
|---|---|---|---|---|
| 1 | Apple Team ID | developer.apple.com | — | the signing identity names |
| 2 | Developer ID Application certificate | Apple Developer | included in the membership | binaries and dylibs |
| 3 | Developer ID Installer certificate | Apple Developer | included | the `.pkg` |
| 4 | App Store Connect API key (`.p8`) | App Store Connect | included | notarization |

Only the **Account Holder** can create Developer ID certificates, so the steps below are done
signed in as the owner. A Mac is **not** required to get the certificates: Apple's portal accepts
a certificate signing request made anywhere.

### - [ ] M1 — Team ID

developer.apple.com → Membership details → copy the 10-character **Team ID**.

### - [ ] M2 — Keys and certificate requests

Two keys, two CSRs — keep them separate:

```bash
openssl req -new -newkey rsa:2048 -nodes -keyout developer_id_app.key \
  -out developer_id_app.csr -subj "/CN=Blackdeep Developer ID Application"
openssl req -new -newkey rsa:2048 -nodes -keyout developer_id_installer.key \
  -out developer_id_installer.csr -subj "/CN=Blackdeep Developer ID Installer"
```

Keep the `.key` files out of the repository and off shared drives. They are the signing identity.

### - [ ] M3 — Request the certificates and make the `.p12` files

developer.apple.com → Certificates, Identifiers & Profiles → Certificates → **+** →
*Developer ID Application*, upload the first CSR; repeat with *Developer ID Installer* and the
second CSR. Choose the *G2 Sub-CA* profile type if asked. Download each `.cer`; they are valid for
five years. Then convert each into the `.p12` format CI imports, per certificate:

```bash
openssl x509 -inform der -in developer_id_app.cer -out developer_id_app.pem
openssl pkcs12 -export -inkey developer_id_app.key -in developer_id_app.pem \
  -name "Developer ID Application" -out developer_id_app.p12
```

Repeat for the installer pair. Use a long random export password and keep it in a password
manager. (On a Mac, Keychain Access can export the same files — either way works.)

### - [ ] M4 — Notarization key

appstoreconnect.apple.com → Users and Access → Integrations → App Store Connect API → **Team
Keys** → generate a key with the **Developer** role (raise it only if `notarytool` reports a
permissions error). Note the **Key ID** and the **Issuer ID**, and download the `.p8` — **Apple
lets you download it once.** The macOS plan (F1) prefers this over an app-specific password because
it is revocable and scoped.

### - [ ] M5 — First signed build, by hand

Per the 2026-09-30 macOS decision, the contributor's Mac signs and notarizes the first build,
importing the two `.p12` files into a temporary keychain and storing the notary credentials with
`xcrun notarytool store-credentials`. Hand the files over through a password manager, not chat or
email, and delete the copies afterwards.

### - [ ] M6 — CI secrets

Once the manual run works, add these as **environment secrets** on a protected `release`
environment, so only a run the owner starts and approves can reach them. The workflow reads
exactly these names (`release.yml`, job `macos`).

1. GitHub → the repository → Settings → Environments → **New environment** `release`.
2. *Required reviewers*: the owner. *Deployment branches and tags*: **Selected branches and
   tags**, add the branch rule `release/*`. Since 2026-10-10 the job signs only when run by hand
   on a release branch at the freeze, never at the tag (RELEASE.md §2, *macOS*).
3. Add the seven secrets below to that environment, not to the repository.
4. Settings → Secrets and variables → Actions → *Variables* → **New repository variable**
   `MACOS_SIGNING` = `enabled`. Without it the job is skipped.

| Secret | Holds |
|---|---|
| `MACOS_CERT_APP_P12_BASE64` | `base64` of `developer_id_app.p12` |
| `MACOS_CERT_INSTALLER_P12_BASE64` | `base64` of `developer_id_installer.p12` |
| `MACOS_CERT_PASSWORD` | the `.p12` export password |
| `NOTARY_KEY_P8_BASE64`, `NOTARY_KEY_ID`, `NOTARY_ISSUER_ID` | the App Store Connect API key |
| `APPLE_TEAM_ID` | the 10-character Team ID |

Make each base64 value as one line with no line breaks: `base64 -i developer_id_app.p12 | tr -d
'\n'` on a Mac or Linux, `[Convert]::ToBase64String([IO.File]::ReadAllBytes("developer_id_app.p12"))`
in PowerShell.

## What to expect

A notarized, stapled `.pkg` opens with no Gatekeeper warning at all. Unlike Windows SmartScreen
there is no reputation period on macOS. The identity names read
`Developer ID Application: <name> (<Team ID>)`, where `<name>` is the enrolled legal name —
check it says what you want users to see.

## Housekeeping

- **Renewals.** Membership yearly, certificates every five years.
- **Revocation.** If a key leaks: revoke the Developer ID certificate in the Apple portal and
  delete the App Store Connect key. The repository never held any of them.
- **Public output.** The identity appears inside published binaries. Follow AGENTS.md *Public
  Output Hygiene*: no personal names or machine paths beyond what the certificate subject shows.

## Definition of done

The four items above exist, the contributor has produced one signed and notarized build by hand,
and the CI secrets are set — so macOS plan §9 can run unattended.
