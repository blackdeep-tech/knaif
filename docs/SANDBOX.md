# Sandbox Directory

`sandbox/` is generated local scratch space. It is intentionally gitignored and can be
recreated from checked-in skill manifests and corpora.

Evaluation fixtures are generated per skill:

```bash
uv run python -m knaif.evalsuite fixtures regen --skill ffmpeg
uv run python -m knaif.evalsuite fixtures regen --skill documents
```

By default, generated fixture files live under `sandbox/fixtures/<skill>/`.

## Why fixtures are partitioned by skill

The per-skill subdirectory is load-bearing, not tidiness. A flat `sandbox/fixtures/` had
two concrete defects:

- **Corpus rows reference bare filenames.** A row says `fixture: "sample.pdf"`, resolved
  relative to the skill's fixture dir. In a flat folder, two skills that both pick a
  natural name — `sample.pdf`, `clip.mp4` — silently overwrite each other's generated
  media, and the eval grades whichever skill regenerated last. The partition is what lets
  skills choose obvious fixture names independently, so **keep referencing bare filenames
  in corpora** and let `fixture_dir` do the scoping.
- **A single shared regen cache coupled every skill.** One `.cache.json` for all skills
  meant regenerating one skill's fixtures invalidated or skipped another's. Each skill now
  owns its cache under its own directory.

Anything resolving a fixture path should go through the shared resolver
(`_default_fixture_dir(sandbox, skill)` in `knaif/evalsuite/cli.py`) rather than joining
`sandbox / "fixtures"` itself — that flat join is exactly the bug, and it previously
appeared at four separate call sites.
