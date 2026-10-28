# URL inputs through the `net` toolkit

**Status:** Active · **Created:** 2026-10-26 · **Completed:** —
**Owner:** core + ffmpeg + documents · **Ref:** [toolkits](2026-10-26-toolkits.md) (builds on it);
[TOOL_SCHEMA.md](../TOOL_SCHEMA.md) (*URL inputs*); `toolkits/net/SPEC.md`
**Release:** 1.4.0

> **Status note (2026-10-26).** Built and measured on both runtimes with the shipped 4B model; a
> prompt-only change made it plan URL inputs. Left for the release: the URL rows joined each skill's
> `eval.jsonl` on 2026-10-28 (tag `url_input`, release index *Decisions*) and their floor is written
> from the re-lock that first measures them, on the generation-1 prompts, and the release evidence runs per
> platform with the eval's local HTTP server.

**Goal:** A request may name a file by its http(s) link; it is fetched into the run scratch and
worked on like a local file, with the user's consent and never in a dry run.

---

## What it does

- **A URL is a value class of its own, never a path.** A pipeline stage downloads each URL input
  into the run scratch before preflight and expansion, through the `net` toolkit's
  `download_file` (effects `net`, `fs.write`). Every later step sees a local file. The output name
  comes from the URL's file name, on the user's side.
- **Consent.** No request reaches the network without approval: the CLI asks, or `--allow-download`
  grants it; `--yes` alone does not download. A dry run makes zero requests.
- **Refused, with a message and no file left:** schemes other than http(s), credentials in the
  URL, a redirect to another scheme, an HTML content type, a size over the cap (by header and by
  stream), a timeout.
- **Still unsupported, by design:** downloading on its own, uploads, sending. The prompt's
  unsupported list says so and the model clarifies.
- **Both runtimes:** `knaif.urls` and `knaif_core::urls` with shared contract cases; the fetch
  stage and the flag in both CLIs; `canon_token` keeps URLs whole.

## Bar and evidence

Fixed before measuring. The new rows were in `skills/<skill>/data/eval_url.jsonl` (in `eval.jsonl`, tag
`url_input`, since 2026-10-28; ffmpeg 14 rows,
documents 10, three wordings each), executed against the eval's local server, graded on the
result file and on nothing else left behind.

| Bar | Result |
|---|---|
| URL rows, executing verifier, shipped 4B | ffmpeg 0.952, documents 1.000 (bar ≥ 0.85 per skill); exact files clean in both runtimes |
| Nothing else moves without a reason | both regression gates hold on the full corpus; every moved row traced to the prompt change or to execution noise |
| Safety | 100% on both skills |
| Policy, in tests, both runtimes | consent, dry run, every refusal above |
| L1 / L2 | 100%, re-recorded for the deliberate prompt change only |
| Native L3 | 0 port bugs, 0.0000 plan disagreement on the URL rows and the sample |

Re-measured 2026-10-25 on the final code with the shipped 4B: ffmpeg 0.952 outcome /
1.0 knaif score, documents 1.0 / 1.0.

## Noted, not blocking

A few ffmpeg rows are sensitive to the prompt wording around the unsupported list; they are a
candidate for the training mix (the `train_url.jsonl` rows), not for more prompt text.
