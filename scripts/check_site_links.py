#!/usr/bin/env python3
"""Check internal links and anchors across a built site. Run with `just site-links`.

An acceptance gate (plan §9), not a unit test — it needs `dist/`, so it runs after a build
rather than inside pytest.

It exists because `/download/` was linked from all six other .org pages before that page
existed. Astro does not check internal links, so nothing else catches a typo'd href or an
anchor that moved when a heading was reworded.

Deliberately offline: external URLs are not fetched. A gate that depends on someone else's
uptime fails for reasons that are not your fault, and would train people to ignore it.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITES = {"knaif.org": REPO / "site" / "org" / "dist", "knaif.dev": REPO / "site" / "dev" / "dist"}

HREF = re.compile(r'href="(/[^"]*)"')
ID = re.compile(r'id="([^"]+)"')


def _route(dist: Path, page: Path) -> str:
    rel = page.parent.relative_to(dist).as_posix().strip(".")
    return ("/" + rel + "/").replace("//", "/")


def check(name: str, dist: Path) -> list[str]:
    if not dist.is_dir():
        return [f"{name}: {dist.relative_to(REPO).as_posix()} missing - run `just site-build`"]

    pages: set[str] = set()
    anchors: dict[str, set[str]] = {}
    for page in dist.rglob("index.html"):
        route = _route(dist, page)
        pages.add(route)
        anchors[route] = set(ID.findall(page.read_text(encoding="utf-8")))

    failures: dict[str, set[str]] = defaultdict(set)
    for page in dist.rglob("*.html"):
        source = page.relative_to(dist).as_posix()
        for href in HREF.findall(page.read_text(encoding="utf-8")):
            path, _, fragment = href.partition("#")
            path = path or "/"
            if path not in pages and not (dist / path.lstrip("/")).exists():
                failures[f"{href} -> no such page"].add(source)
            elif fragment and path in anchors and fragment not in anchors[path]:
                failures[f"{href} -> no such anchor"].add(source)

    return [
        f"{name}: {problem}  (linked from {', '.join(sorted(sources)[:3])})"
        for problem, sources in sorted(failures.items())
    ]


def main() -> int:
    problems: list[str] = []
    for name, dist in SITES.items():
        found = check(name, dist)
        problems += found
        if not found and dist.is_dir():
            count = sum(1 for _ in dist.rglob("index.html"))
            print(f"ok  {name}: {count} pages, all internal links and anchors resolve")

    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
