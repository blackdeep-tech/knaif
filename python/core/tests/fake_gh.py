"""A stand-in for `gh` in the relay tests: the issue's comments live in $FAKE_GH_DIR/comments.jsonl.

Only what `knaif.evalsuite.relay` calls: `issue list` (one issue, #7, titled $FAKE_GH_TITLE),
posting a comment (`api --method POST .../comments --input FILE`, as the Mac's account) and listing
them (`api --paginate .../comments --jq ...`, one JSON object per line). A posted comment that
contains $FAKE_GH_ANSWER_ON is answered at once by the owner with $FAKE_GH_ANSWER.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

d = Path(os.environ["FAKE_GH_DIR"])
store = d / "comments.jsonl"
args = sys.argv[1:]


def load() -> list[dict]:
    if not store.exists():
        return []
    return [json.loads(x) for x in store.read_text(encoding="utf-8").splitlines() if x.strip()]


def add(login: str, body: str) -> int:
    comments = load()
    cid = (comments[-1]["id"] if comments else 100) + 1
    with store.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps({"id": cid, "body": body, "user": {"login": login}, "html_url": f"u/{cid}"})
            + "\n"
        )
    return cid


if args[:2] == ["issue", "list"]:
    print(json.dumps([{"number": 7, "title": os.environ["FAKE_GH_TITLE"]}]))
elif args[:3] == ["api", "--method", "POST"]:
    body = json.loads(Path(args[args.index("--input") + 1]).read_text(encoding="utf-8"))["body"]
    cid = add("mac-dev", body)
    trigger = os.environ.get("FAKE_GH_ANSWER_ON")
    if trigger and trigger in body and "```knaif-report" in body:
        add("GalinDinkov", os.environ["FAKE_GH_ANSWER"])
    print(json.dumps({"id": cid}))
elif args[:2] == ["api", "--paginate"]:
    for c in load():
        print(json.dumps(c))
else:
    print(f"fake gh: unexpected {args}", file=sys.stderr)
    sys.exit(1)
