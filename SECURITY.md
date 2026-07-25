# Security Policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report privately to **knaif@blackdeep.tech**, or use GitHub's
[private vulnerability reporting](https://github.com/blackdeep-tech/knaif/security/advisories/new)
on this repository.

Please include:

- what an attacker can achieve, and what access they need to start;
- the **exact utterance** and skill involved, if the issue is reachable through natural language;
- OS, runtime (Python or native CLI), backend (CPU / Vulkan / CUDA), and model;
- a minimal reproduction.

You will get an acknowledgement within **5 working days**. If a report is accepted we will
agree a disclosure timeline with you, and credit you in the advisory unless you prefer
otherwise.

## Supported versions

knaif is at its first release. Security fixes land on the latest released version; there
are no maintained older release lines yet.

| Version | Supported |
|---|---|
| 1.0.x | yes |
| < 1.0 | no (pre-release) |

## Threat model

**knaif executes model-proposed plans against a user's filesystem.** That is the whole
point of the tool, and it is also the entire reason this threat model exists. The design
premise is that **the model is not trusted**:

> The model only ever emits `{ "plan": [...] }`. Everything that decides whether that plan
> runs — validation, safety classification, expansion, path checking, confirmation — is
> deterministic code, not model output.

The controls that enforce this are specified in
[`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md):

- the model **never executes directly**, and emits only the JSON plan envelope;
- every tool call is validated against the active registry — unknown tools and unsupported
  arguments are rejected;
- tools marked `safety_category: destructive` require `dry_run=True` or `confirmed=True`;
- handlers must honour `ctx.dry_run`;
- sandbox-sensitive file operations must reject paths outside the intended sandbox —
  **checked before execution and again after variable resolution**, because a plan can
  introduce a new path when variables are substituted.

Deliberately **out of scope by design** — these are not vulnerabilities, they are the
boundary of what knaif attempts:

- general-purpose autonomous agent behaviour;
- free-form shell command generation by the model;
- background autonomous execution;
- cloud-hosted service behaviour.

### What we consider a vulnerability

- A plan that **escapes the sandbox** — reaching a path outside it, including via variable
  resolution, symlinks, `..` traversal, or platform-specific path handling.
- A **destructive tool executing without** `confirmed=True` or `dry_run=True`.
- An **unregistered tool or unsupported argument** surviving validation.
- A handler that **ignores `ctx.dry_run`** and causes side effects during a preview.
- **Argument injection** into an external tool invocation (for example, a filename that
  becomes an FFmpeg flag rather than a path).
- A crafted **model output** that causes any of the above, or that induces the runtime to
  execute something the plan schema should have rejected.
- A **model download** that bypasses SHA-256 verification, or a manifest that can be made
  to fetch an unpinned artifact.

### What we do not consider a vulnerability

- **A model proposing a bad or destructive plan.** That is expected — the confirmation gate
  and `dry_run` exist precisely because model output is untrusted. It becomes a
  vulnerability only if the plan bypasses those gates.
- **Damage from a user confirming a destructive operation.** Confirmation is the security
  boundary; crossing it deliberately is a supported action.
- **Vulnerabilities in external binaries** knaif invokes but does not ship (FFmpeg, and
  other per-skill tools). Report those upstream. If knaif passes them **unsanitised input**,
  that is ours — report it.
- **Anything requiring the attacker to already have write access** to the skill bundles,
  `models.yaml`, or the model manifest. Someone who can edit your skill definitions has
  already won.
- Missing hardening in a **development or evaluation** path (`sandbox/`, `evals/`,
  notebooks) that is not part of a released artifact.

## Operational notes

- **Windows artifacts are unsigned at v1.** SmartScreen will warn on first run. Verify
  downloads against the published `SHA256SUMS`.
- **Models are downloaded, not bundled.** URLs are pinned to a commit SHA and verified
  against a recorded SHA-256; `knaif models verify` re-checks a local file. A model with no
  recorded checksum is reported as unverified rather than trusted silently.
- **Skills are loaded by path from a checkout.** Treat a skill bundle as executable code:
  loading one from an untrusted source is equivalent to running that source.
