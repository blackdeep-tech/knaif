# Website operations — knaif.org and knaif.dev

Running the two published sites: what deploys, what to check before it does, and how to get
the previous version back. Design decisions and the build history live in
[plans/2026-08-04-website-split.md](plans/2026-08-04-website-split.md); this file is the
operational half, and it outlives the plan.

## 1. What is deployed, and from where

Two AWS Amplify apps over this one repository. Each selects its half by setting
`AMPLIFY_MONOREPO_APP_ROOT`, which AWS requires to match an `appRoot` in the committed
[`amplify.yml`](../amplify.yml) — a mismatch fails the build rather than deploying the wrong
site.

| Site | App root | Source | Serves |
|---|---|---|---|
| knaif.org | `site/org` | Astro | End users; the download is the point |
| knaif.dev | `site/dev` | Starlight | Developers; five tracks |

**The apex is canonical.** `site:` in each `astro.config.mjs` is the bare domain, so every
canonical tag and both sitemaps name it. `www` must redirect to the apex, never the reverse
— serving the apex from `www` would make every published canonical point away from the host
that served it.

**A branch deploys only if Amplify is configured for it.** Production builds from `main`.
Any other branch — including the feature branch a plan is assembled on — needs pointing at
in the console before "test it on the branch" means anything in a browser.

`amplify.yml` is the authority: a repo build spec overrides build settings saved in the
console, which is what makes the file reviewable in a PR rather than advisory.

## 2. Before you merge to `main`

The site gates are deliberately outside `just check` — they need a full production build of
both sites, and one of them needs a browser. Run them against a fresh build:

```bash
just site-build      # both sites, exactly as Amplify builds them
just site-links      # internal links + anchors, offline
just site-a11y       # contrast + keyboard nav, both themes, real browser
```

`just site-a11y` needs Chromium once: `just site-a11y-install`.

If the generated data changed, regenerate and commit it — a drift guard fails otherwise:

```bash
just site-data       # the skill catalog both sites read
just release-data    # after publishing a release; see RELEASE.md §5 step 7
```

**After a release, re-check the download links.** `site/data/release.json` is refreshed by
hand today, and every URL in it carries a version. A missed refresh advertises assets that
404, and nothing in the unit suite can catch it — the check is that every URL in the file
returns 200 against the live release.

## 3. Rolling back

Amplify keeps previous builds per app, and each app rolls back independently — a bad
knaif.org deploy does not require touching knaif.dev.

**Fastest path, no git involved.** In the Amplify console, open the app, pick the last known
good build in the branch's deployment history, and redeploy it. This restores the served
artifact within a build cycle and changes nothing in the repository.

**Then fix forward.** A console redeploy does not change `main`, so the next push to `main`
redeploys the bad commit. Either revert it (`git revert <sha>`, its own PR) or land the fix.
Treat the console redeploy as stopping the bleeding, not as the repair.

**What a rollback does not cover:**

- **Domain and DNS changes.** Route 53 ALIAS records and the `www` redirect are console
  state, not build artifacts, and rolling a build back does not restore them.
- **A stale `release.json`.** Its URLs point at GitHub release assets; rolling the site back
  restores whatever snapshot that build carried, which may be older still.
- **Anything already crawled.** A wrong canonical or a wrong `robots.txt` can be served long
  enough to be indexed, and the rollback does not un-index it.

**If a build fails rather than deploying something wrong**, the previous deployment stays
live — Amplify does not publish a failed build. That is what the page-count assertion in
`amplify.yml` is for: a Starlight build with no content collection emits only `404.html` and
still exits 0, so exit status alone would have deployed an empty site.

## 4. Checking the live sites

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://knaif.org/
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://www.knaif.org/   # want 301
curl -s https://knaif.dev/sdk/ | grep -o '<link rel="canonical"[^>]*>'
```

`www` returning **200 rather than 301** means that subdomain is serving the site instead of
redirecting to the apex — duplicate content under a hostname none of the canonicals name.
It is console state, so no repository change fixes it.
