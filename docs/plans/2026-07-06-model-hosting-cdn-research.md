# Model Hosting & Download Speed — CDN / Own-Host Research

**Status:** Done · **Created:** 2026-07-06 · **Completed:** —
**Owner:** core · **Ref:** [`2026-06-17-monorepo-dual-runtime.md`](2026-06-17-monorepo-dual-runtime.md) Phase 8
(incl. the `installers-models-dependencies` and `model-management` decisions) ·
[`native/crates/knaif-models/src/fetcher.rs`](../../native/crates/knaif-models/src/fetcher.rs) ·
[`contracts/models/model-manifest.yaml`](../../contracts/models/model-manifest.yaml)

**Goal:** Research findings (no code changes) — decide where knaif hosts its GGUF models and how the
native client downloads them fast enough; the analysis behind keeping HuggingFace with a client-side
parallel fetcher, R2 as the vetted fallback.

> **Kept 2026-07-23** (S7 decision — research findings). This is the cost/vendor analysis
> behind the hosting decision; its durable conclusion — HF kept, R2 the vetted fallback, the
> swap URL-only because models are content-addressed — is now in
> [MODELS.md §7](../MODELS.md). Referenced only from docs, not source.
>
> **⚠️ The central premise is now historically true but describes shipped software as if it
> doesn't exist (annotated 2026-07-23).** Every "our fetcher is a single synchronous stream,
> no parallelism, no range, no resume" statement below was accurate on 2026-07-06 and is
> **false today**: the recommendation was taken. [`fetcher.rs`](../../native/crates/knaif-models/src/fetcher.rs)
> now does exactly the "Cheap immediate win" — parallel byte-range chunks with an
> `Accept-Ranges` probe and single-stream fallback, resume via a sidecar, and 429/503
> retry-with-backoff honoring `Retry-After`. **Migration was correctly *not* pursued**: per the
> plan's own ordering, the client fix came first, HF proved fast enough, so hosting stayed on
> HF (manifest still points at `blackdeep/knaif`). R2 remains the pre-vetted target if that
> ever changes. Read §1b/§4/§5 as the *reasoning that led to the shipped fetcher*, not as an
> open to-do list.
>
> **Prices, limits, and provider behavior below are as of 2026-07 — verify before acting.**
> Numbers are cited inline; treat every dollar figure and every rate limit as a snapshot that
> providers change without notice.

---

## Executive summary (headline recommendation)

**The bottleneck is almost certainly the client, not Hugging Face.** HF's published limits are
**request-count** limits over 5-minute windows (anonymous: 3,000 "resolver" requests / 5 min /
IP), not a bytes-per-second bandwidth throttle — and a single end-user pulling one 2.5 GB GGUF
issues only a handful of resolve requests, nowhere near that ceiling ([HF Hub rate limits](https://huggingface.co/docs/hub/rate-limits)).
Our fetcher is a **single synchronous `ureq` HTTP/1.1 connection with no parallelism, no HTTP/2,
no range requests, and no resume** ([fetcher.rs](../../native/crates/knaif-models/src/fetcher.rs)).
That is exactly the profile that downloads slowly on a fast connection: one TCP stream cannot
saturate a high-bandwidth link against a distant CDN edge because of the bandwidth-delay product,
and it gets none of the speedups that `hf_transfer`/`hf_xet` are built around — those tools hit
**up to 64 parallel range streams** ([Xet download internals](https://huggingface.co/docs/hub/en/models-downloading)).

**Recommended path:**

1. **Cheap immediate win (do first, keep HF): make the client download in parallel.** Add a
   concurrent chunked range downloader (N parallel `Range:` GETs) plus **429/5xx
   retry-with-backoff and resume**. This is host-agnostic and typically the single biggest
   real-world speedup. Adding a token is *not* the fix — a token raises request-count limits,
   not per-stream bandwidth.
2. **Only if HF still disappoints, migrate hosting — to Cloudflare R2.** R2 has **$0 egress**,
   supports range/parallel/resume natively, and allows tokenless public GETs. Migration is cheap
   by design (our fetcher is host-agnostic and models are content-addressed): re-host the GGUFs
   and swap each manifest `url`; the `sha256`/`size_bytes` stay valid.
3. **Do NOT** pursue HF paid tiers or "give end-users a token" to fix speed — neither targets the
   real cause, and a token contradicts the locked tokenless-download decision.

---

## 1. Is the bottleneck HF or the client? (the central question)

### 1a. Server-side: what HF actually limits

HF's documented limits are **request counts over fixed 5-minute windows**, split into three
buckets — *Hub APIs*, *Resolvers* (any URL with a `/resolve/` segment — this is exactly what our
fetcher hits), and *Pages* ([HF Hub rate limits](https://huggingface.co/docs/hub/rate-limits)).
Current tiers (doc says "as of September '25"):

| Plan | API / 5min | **Resolvers / 5min** | Pages / 5min |
|---|---|---|---|
| **Anonymous (per IP)** | 500 | **3,000** | 100 |
| Free user | 1,000 | 5,000 | 200 |
| PRO | 2,500 | 12,000 | 400 |
| Enterprise | 6,000 | 50,000 | 600 |

Source: [HF Hub rate limits → Rate limit Tiers](https://huggingface.co/docs/hub/rate-limits).

Key reads for our case:

- **These are request-count caps, not bandwidth caps.** The docs describe no documented
  bytes/sec throttle. Over-limit returns **HTTP 429** with standardized `RateLimit` /
  `RateLimit-Policy` headers (IETF `draft-ietf-httpapi-ratelimit-headers`) telling you exactly
  how many seconds until reset ([HF rate limits → HTTP Headers](https://huggingface.co/docs/hub/rate-limits)).
- **Anonymous limits are per-IP**, so distinct end-users don't share a budget — matching the
  Phase 8 rationale. Shared-IP egress (corporate NAT, CI fleets) is where the 3,000/5min pinches.
- **A single end-user download is nowhere near the cap.** One GGUF pull = one (or a few) resolve
  requests. Even a *parallel* client splitting a 2.5 GB file into, say, 250 × 10 MB range GETs
  issues ~250 resolve requests — still well under 3,000 / 5 min. So neither the current serial
  client nor a future parallel one should trip the anonymous resolver limit for a normal user.
- **Token effect = higher request-count limits, not more bandwidth.** HF's own guidance ("always
  pass a `HF_TOKEN`… the number one reason users get rate limited") is about avoiding **429s**,
  i.e. raising the count ceiling (anonymous 3,000 → free 5,000 resolvers) — *not* a per-connection
  speed boost ([HF rate limits → "What if I get rate-limited"](https://huggingface.co/docs/hub/rate-limits)).
  Third-party "token = faster downloads" claims (e.g. [luispa.com, 2026-02](https://luispa.com/en/posts/2026-02-22-limitar-hf/))
  conflate this: the measurable speed lever is client **parallelism**, not the credential.
- **Storage backend is now Xet, not classic LFS.** The Hub is "fully powered by the Xet storage
  backend," and the old `hf_transfer` LFS accelerator "can't be used anymore" — the current
  accelerator is `hf_xet` ([Xet download internals](https://huggingface.co/docs/hub/en/models-downloading)).
  Our `/resolve/<sha>/<file>` URLs still work: they 302 to the CDN / Xet gateway
  (`cas-bridge.xethub.hf.co`) and stream the bytes. We are effectively using the "dumb" resolve
  path and getting none of Xet's client-side parallelism.
- **Anecdotal anonymous slowness exists but is not a documented policy.** Forum reports describe
  intermittent low bandwidth / 403s on unauthenticated pulls
  ([HF forum: intermittent 403 / low bandwidth](https://discuss.huggingface.co/t/downloads-intermittently-fail-403-low-bandwidth/140198)).
  Treat these as edge conditions to survive (retry/backoff/resume), not as the primary cause of
  "slow on a fast connection."

**Verdict (server-side):** HF does **not** document a bandwidth throttle on anonymous file
downloads, and a single user is far below the per-IP request ceiling. HF is unlikely to be the
dominant cause of "slow on a very fast connection."

### 1b. Client-side: why one `ureq` stream is slow

Our fetcher ([fetcher.rs](../../native/crates/knaif-models/src/fetcher.rs)) does
`ureq::get(url).call()` → follow redirects → read the body in 64 KiB chunks to disk. Properties:

- **`ureq` (v2) is HTTP/1.1 only and synchronous** — it "does not support HTTP/2" and does "plain
  old blocking I/O," one connection at a time ([Rust HTTP client comparison, 2026](https://rustify.rs/articles/rust-reqwest-vs-ureq-vs-hyper-2026)).
- **A single TCP stream can't fill a fast pipe over long RTT.** Throughput on one stream is capped
  by the bandwidth-delay product and any per-connection shaping at the CDN edge. This is the
  classic "fast internet, slow single-stream download" symptom and is *independent of host*.
- **No resume**: an interrupted 2.5 GB pull restarts from zero (the fetcher's own doc note admits
  "Resume is still a future add").
- **No 429 handling**: a rate-limit or transient 5xx surfaces as a hard error (`.call()` returns
  `Err` on non-2xx), with no backoff — brittle exactly when the network is flaky.

**How the fast tools get their speed (the target to emulate):**

- **`hf_transfer` (legacy):** concurrent **10 MB range reads**, one system thread per chunk,
  writing to disk in parallel ([Xet download internals](https://huggingface.co/docs/hub/en/models-downloading)).
- **`hf_xet` (current):** files are content-defined chunks packed into "xorbs"; the client fetches
  reconstruction metadata + presigned URLs and pulls xorb byte-ranges with **adaptive
  concurrency — starting at 1 stream and scaling up to 64 parallel streams** as bandwidth allows
  (`HF_XET_NUM_CONCURRENT_RANGE_GETS`) ([Xet download internals](https://huggingface.co/docs/hub/en/models-downloading)).

The common ingredient is **parallel HTTP range requests**, not the credential and not HTTP/2 per se
(HTTP/2 multiplexing over one connection helps latency-bound many-small-file cases; for one big
file, *multiple parallel range connections* is what saturates the link).

### 1c. Client-side recommendation

Build a **concurrent chunked range downloader** in `knaif-models`, regardless of host:

1. **Probe** the target with a `HEAD` (or a first ranged GET) to read `Content-Length` and confirm
   `Accept-Ranges: bytes`.
2. **Split** into fixed-size segments (e.g. 8–16 MB) and fetch **N in parallel** (start with
   N≈8–16; make it configurable, e.g. `KNAIF_DOWNLOAD_CONCURRENCY`). Write each segment at its
   offset (pre-allocated file + positioned writes, or per-segment temp files concatenated).
3. **Resume**: persist a `.part` + completed-segment map so an interrupted pull continues via
   `Range:` on only the missing segments.
4. **429 / 5xx retry-with-backoff**: honor the `RateLimit`/`Retry-After` header when present
   ([HF rate limits headers](https://huggingface.co/docs/hub/rate-limits)); otherwise exponential
   backoff with jitter and a cap.
5. **Verify** the existing `sha256` after assembly (already in the manifest) — parallel assembly
   makes end-to-end checksum verification more important, not less.

Implementation options, cheapest-first:
- **Keep `ureq`, add parallelism manually**: spawn N threads, each doing a ranged `ureq` GET. Small
  dependency footprint, no async runtime. Range + retry logic is ours to write. Good fit for the
  "no inference deps, small crate" spirit of `knaif-models`.
- **Swap to `reqwest` (blocking or async on `tokio`)**: gets HTTP/2, connection pooling, and a
  mature client; more compile weight and an async runtime. Reasonable if we want HTTP/2 and richer
  retry middleware, but a heavier dependency than `knaif-models` currently carries.
- **Range caveat on HF specifically:** the `/resolve/` redirect now lands on the Xet gateway
  (`cas-bridge.xethub.hf.co`), and there are open reports of **range/CORS quirks** there
  ([datasets #7931 — Range on cas-bridge](https://github.com/huggingface/datasets/issues/7931),
  [hub #2197 — 416 Range Not Satisfiable](https://github.com/huggingface/huggingface_hub/issues/2197)).
  A parallel-range client **must** probe `Accept-Ranges` and **fall back to a single stream** if
  the target refuses ranges. This is another reason an **own host with guaranteed range support
  (R2)** pairs well with a parallel client.

**Bottom line:** the parallel/range/resume/backoff client is the highest-leverage, host-agnostic
fix. Do it first; it may make the hosting question moot.

---

## 2. Own-CDN / object-storage options

**Cost assumptions (state-and-verify):** dominant artifact ≈ **2.5 GB** (the 4B GGUF;
`size_bytes: 2497280960` ≈ 2.5 GB / 2.325 GiB). Total stored bytes are tiny (~4 GB for both
models + a little version headroom), so **storage cost is negligible everywhere** — the number
that matters is **egress**. Three illustrative scales, at ~2.5 GB/download:

- **Small:** 1,000 downloads/mo ≈ **2.5 TB/mo** egress
- **Medium:** 10,000 downloads/mo ≈ **25 TB/mo**
- **Large:** 100,000 downloads/mo ≈ **250 TB/mo**

### Comparison table (as of 2026-07 — verify before acting)

| Option | Egress cost model | Tokenless public GET? | Range / parallel / resume | ~Monthly egress cost (2.5 / 25 / 250 TB) | Ops effort | Notes |
|---|---|---|---|---|---|---|
| **Hugging Face (current)** | Free hosting; request-count limits, no $ egress | **Yes** (already relied on) | Range works on resolve path but Xet-gateway range quirks exist | **$0 / $0 / $0** | ~none (already live) | Free, but single-stream via our client is slow; anon per-IP request caps |
| **Cloudflare R2** | **$0 egress**, all classes; pay storage + ops | **Yes** (public bucket / `r2.dev` or custom domain) | **Yes** — native range GETs, resume | **~$0 / ~$0 / ~$0** (+ ~$0.06/mo storage; Class B ops trivial) | Low | Zero egress, no caps/throttle ratios ([R2 pricing](https://developers.cloudflare.com/r2/pricing/)) |
| **Backblaze B2 + Cloudflare (Bandwidth Alliance)** | Egress **free via Cloudflare**; storage $6/TB-mo | **Yes** (behind CF) | **Yes** | **~$0 / ~$0 / ~$0** (must route through CF) | Low–medium | Free egress only *through Cloudflare*; direct B2 egress is $0.01/GB after 3× stored ([B2 + CF, 2026](https://leanopstech.com/blog/backblaze-b2-pricing-2026/)) |
| **Bunny CDN (Volume tier)** | ~**$0.005/GB** first 500 TB, global flat | **Yes** | **Yes** | **~$12.50 / ~$125 / ~$1,250** | Low | Cheap real CDN, pay-as-you-go, $1/mo min ([Bunny CDN pricing](https://bunny.net/pricing/cdn/)) |
| **AWS S3 + CloudFront** | CloudFront **~$0.085/GB** (US/EU) after 1 TB free/mo; S3→CF origin free | **Yes** | **Yes** | **~$127 / ~$2,040 / ~$17k+** | Medium | Best-in-class perf, worst egress cost at scale ([CloudFront pricing](https://aws.amazon.com/cloudfront/pricing/)) |
| **Google Cloud Storage (+ Cloud CDN)** | Egress ~$0.08–0.12/GB (similar to AWS class) | Yes | Yes | High (S3/CF-class) | Medium | No egress advantage vs AWS; no reason to prefer for this use |
| **Plain VPS + nginx** | Bundled bandwidth then overage; single region | **Yes** | **Yes** (nginx serves ranges) | ~$5–40/mo small; bare-metal/unmetered ~$100–300/mo at 250 TB | **High** | No global edge, you own uptime/TLS/scaling; only sane at big volume with unmetered bare metal |
| **GitHub Releases** | **Free, unlimited bandwidth** | **Yes** | **Yes** (range on asset CDN) | **$0 / $0 / $0** | Low | **2 GiB per-asset cap** — the **4B model (2.325 GiB) does NOT fit**; would need splitting; 1.7B (1.32 GiB) fits ([GitHub asset limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)) |

Cost math is linear on egress: e.g. CloudFront medium ≈ (25 TB − 1 TB free) × 1000 GB × $0.085 ≈
**$2,040/mo**; Bunny Volume medium ≈ 25,000 GB × $0.005 ≈ **$125/mo**; R2/B2-via-CF ≈ **$0**.

### Per-option notes

- **Cloudflare R2** — Zero egress on every storage class, "no fair-use caps, no throttling at high
  ratios"; you pay only storage ($0.015/GB-mo) + operations (Class A writes $4.50/M, Class B reads
  $0.36/M) ([R2 pricing](https://developers.cloudflare.com/r2/pricing/)). At our ~4 GB storage and
  even 100k downloads/mo, storage + read ops are a few dollars at most. Public access via a bucket
  bound to a custom domain (or `r2.dev` for dev); supports standard S3 range GETs, so it pairs
  perfectly with the parallel client. **This is the recommended migration target.**
- **Backblaze B2 + Cloudflare** — Storage is cheap ($6/TB-mo). Egress is free **only when routed
  through Cloudflare** (Bandwidth Alliance); direct B2 egress is free up to 3× stored bytes/day then
  $0.01/GB ([B2 pricing 2026](https://leanopstech.com/blog/backblaze-b2-pricing-2026/),
  [B2 free-egress caveats](https://leanopstech.com/blog/backblaze-b2-free-egress-trap-restore-costs-2026/)).
  Net cost ≈ R2, but with **two vendors to wire together** and the "must go through CF" constraint.
  R2 achieves the same $0 with one vendor — prefer R2 unless already invested in B2.
- **Bunny CDN / Storage** — A genuinely cheap global CDN: Volume tier ~$0.005/GB (first 500 TB),
  Standard region-priced ($0.005 EU/NA up to $0.06 Africa), storage $0.01/GB-mo HDD, $1/mo account
  minimum ([Bunny pricing](https://bunny.net/pricing/), [Bunny storage](https://bunny.net/pricing/storage/)).
  Not zero-egress, but at small/medium scale the absolute dollars are low and it's a real CDN with
  simple pay-as-you-go. A reasonable #2 if R2's operational model is undesirable.
- **AWS S3 + CloudFront** — Technically excellent (S3→CloudFront origin transfer is free; range +
  resume native; 1 TB/mo always-free CloudFront egress), but **CloudFront egress at ~$0.085/GB** is
  the most expensive option at scale — thousands/mo at medium volume ([S3 pricing](https://aws.amazon.com/s3/pricing/),
  [CloudFront pricing](https://aws.amazon.com/cloudfront/pricing/)). Only justified if already
  AWS-committed. Not recommended for a free end-user download.
- **Google Cloud Storage** — Egress in the same ~$0.08–0.12/GB class as AWS; no advantage here.
- **Plain VPS + nginx** — Full control and range support, but you own TLS, uptime, scaling, and
  single-region latency; bandwidth is metered on most VPS plans and overage/unmetered bare metal is
  the only way to reach 250 TB. High ops burden for no benefit over R2 at our scale.
- **GitHub Releases** — Free and unlimited bandwidth with range support, but the **hard 2 GiB
  per-asset limit** ([GitHub about-releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases))
  **excludes the 2.5 GB 4B GGUF** unless split into parts and reassembled client-side. Viable as a
  free mirror for the **1.7B** model or for split multi-part assets, but the split/reassemble
  complexity undercuts its simplicity. Usable as a zero-cost fallback mirror, not the primary host
  for the 4B lane.

---

## 3. HF paid options — do they help end-user download speed?

**No — not for anonymous end-user downloads.** HF PRO / Team / Enterprise raise **request-count
rate limits** (e.g. resolvers 3,000 anon → 12,000 PRO → 50,000 Enterprise per 5 min) and add
priority support ([HF rate limit tiers](https://huggingface.co/docs/hub/rate-limits)). They do
**not** advertise a per-connection bandwidth increase, and — critically — **a paid plan applies to
the account that authenticates**, so it does nothing for our **tokenless, anonymous** end users:
their requests are keyed to their own IP at the anonymous tier no matter what our org pays for.

- **Xet** improves throughput via **client-side parallelism** (up to 64 range streams), and it's
  available on the free public path too ([Xet internals](https://huggingface.co/docs/hub/en/models-downloading));
  the win comes from the *client library*, which we don't use — so the lever is ours to pull in our
  own client, not something an HF plan buys us.
- Paying HF would only matter if *we* were being rate-limited as an authenticated puller (e.g. CI),
  which is not the end-user download scenario.

**Conclusion:** HF paid tiers are **irrelevant** to the reported end-user slowness. Don't buy them
to solve this.

---

## 4. Recommendation

### Decision matrix

| Concern | Client-side parallel fix (keep HF) | Migrate to R2 | HF paid tier |
|---|---|---|---|
| Fixes "slow on fast internet" | **Yes** (parallel range streams) | Partly (only if paired with parallel client) | **No** |
| Cost | ~0 (dev time) | ~$0/mo egress + trivial storage | $ (and doesn't help anon users) |
| Effort | Medium (write range/resume/backoff) | Low host swap **+** the same client work | Low but ineffective |
| Removes HF dependency / anon caps | No (but survives them via backoff) | **Yes** | No |
| Guaranteed range support | HF Xet-gateway quirks possible | **Yes** (native) | n/a |
| Reversibility | Fully (host-agnostic client) | Fully (content-addressed, manifest swap) | n/a |

### Recommended path (ordered)

**Cheap immediate wins (do now, keep HF):**
1. **Add 429/5xx retry-with-backoff + resume** to the fetcher (already a Phase 8 line item; do it
   regardless of everything else). Honor `RateLimit`/`Retry-After` headers.
2. **Add a concurrent chunked range downloader** (N parallel `Range:` GETs, configurable
   concurrency, `Accept-Ranges` probe with single-stream fallback). Verify the manifest `sha256`
   after assembly. This is the actual speed fix and is host-agnostic.
3. **Measure** before/after on a fast connection against the current HF URLs. If parallel-from-HF
   is now fast enough, **stop here** — no migration needed.

**Migrate hosting (only if HF still underperforms after the client fix):**
4. **Cloudflare R2** is the recommended target: **$0 egress**, native range/resume, tokenless
   public GET, one vendor. Bunny CDN (Volume) is the low-cost runner-up; B2+Cloudflare ties R2 on
   cost but needs two vendors. Avoid S3/CloudFront and GCS on cost; GitHub Releases only works for
   the 1.7B model (2 GiB asset cap blocks the 4B).

### Migration steps (if we move hosting) — cheap by design

The fetcher is host-agnostic and models are **content-addressed**, so a host swap is nearly free:

1. Upload the exact same GGUF bytes to the new host (e.g. an R2 bucket on a custom domain).
2. In [`contracts/models/model-manifest.yaml`](../../contracts/models/model-manifest.yaml), change each
   entry's **`url`** to the new host's URL. **`sha256` and `size_bytes` stay identical** — same
   bytes, same checksum, so `verify` keeps passing. (Extend the publish tooling
   `scripts/publish_model.py` to write the new-host URL instead of the HF resolve URL.)
3. Keep URLs **immutable/content-pinned** (an R2 object key that includes the version, or a
   never-overwritten path) — the same discipline as HF's commit-SHA pinning, so a fixed `sha256`
   can't silently break.
4. **No fetcher or runtime code change** for the swap itself (the parallel/resume/backoff work is
   independent and benefits either host). Optionally keep HF (or GitHub Releases for the 1.7B) as a
   fallback mirror — the manifest could later carry a mirror list.

---

## 5. Next actions & open questions

**Ordered next actions:**
1. **Instrument the current download** — capture real throughput (MB/s) and time-to-complete on the
   reporter's fast connection, single-stream from HF, to establish a baseline. (Confirms the
   client-is-the-bottleneck hypothesis with a number, not just theory.)
2. **Implement 429/5xx retry-with-backoff + resume** in `knaif-models` (Phase 8 item; low risk).
3. **Implement the parallel chunked range downloader** with `Accept-Ranges` probe + single-stream
   fallback + post-assembly `sha256` verify; make concurrency configurable.
4. **Re-measure** parallel-from-HF. Decide go/no-go on migration from the delta.
5. **If migrating:** stand up a Cloudflare R2 bucket, mirror the GGUFs, update the manifest `url`s +
   publish script, keep HF as a fallback mirror during a soak period.

**Open questions to verify before acting:**
- **Does the HF Xet gateway (`cas-bridge.xethub.hf.co`) reliably serve `Range:` GETs to a non-browser
  client for our GGUFs?** Test directly; if range support is flaky there, that alone argues for R2.
  (Browser/CORS range issues are reported; server-to-server behavior needs a real probe —
  [datasets #7931](https://github.com/huggingface/datasets/issues/7931).)
- **Is the reported slowness reproducible, or was it a transient anon-throttle / CDN-edge event?**
  A one-off should not drive a migration.
- **Expected download volume?** All hosting cost math hinges on downloads/month; the recommendation
  (R2) is robust across scales, but the *urgency* of moving off HF depends on volume and whether
  users share IPs (corporate NAT / CI) that hit the per-IP anon caps.
- **`ureq` vs `reqwest` decision** for the parallel client: stay lightweight (`ureq` + manual
  threads) vs. adopt `reqwest`/HTTP/2 (heavier deps, richer client). Weigh against the
  "no heavy deps in `knaif-models`" principle.

---

### Sources (as of 2026-07)

- [Hugging Face Hub — Rate limits](https://huggingface.co/docs/hub/rate-limits) (tiers, 5-min
  windows, 429 headers, token guidance)
- [Hugging Face — Downloading models / Xet internals](https://huggingface.co/docs/hub/en/models-downloading)
  (Xet backend, `hf_transfer` 10 MB range reads, `hf_xet` up to 64 parallel streams)
- [Rust HTTP client comparison — reqwest vs ureq vs hyper (2026)](https://rustify.rs/articles/rust-reqwest-vs-ureq-vs-hyper-2026)
  (ureq is HTTP/1.1-only, synchronous, single-connection)
- [HF datasets #7931 — Range/CORS on cas-bridge.xethub.hf.co](https://github.com/huggingface/datasets/issues/7931)
  and [huggingface_hub #2197 — 416 Range Not Satisfiable](https://github.com/huggingface/huggingface_hub/issues/2197)
- [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/) (zero egress; storage +
  ops rates)
- [Backblaze B2 pricing 2026 (+ Cloudflare Bandwidth Alliance)](https://leanopstech.com/blog/backblaze-b2-pricing-2026/)
  and [B2 free-egress caveats](https://leanopstech.com/blog/backblaze-b2-free-egress-trap-restore-costs-2026/)
- [Bunny CDN pricing](https://bunny.net/pricing/) · [Bunny Storage pricing](https://bunny.net/pricing/storage/)
- [AWS S3 pricing](https://aws.amazon.com/s3/pricing/) · [Amazon CloudFront pricing](https://aws.amazon.com/cloudfront/pricing/)
- [GitHub — About releases (2 GiB per-asset limit, unlimited bandwidth)](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
- [luispa.com — Limiting Hugging Face Bandwidth (2026-02)](https://luispa.com/en/posts/2026-02-22-limitar-hf/)
  and [HF forum — intermittent 403 / low bandwidth](https://discuss.huggingface.co/t/downloads-intermittently-fail-403-low-bandwidth/140198)
  (anecdotal anon-download slowness)
