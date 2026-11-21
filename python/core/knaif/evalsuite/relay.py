"""The release relay: a remote platform's step results and the owner's decisions, through one issue.

A release's evidence runs on machines the owner does not sit at (the Mac). Instead of a person
carrying reports and answers back and forth, the remote machine's queue
(`scripts/release_queue.sh`) posts each step's result as a comment on one GitHub issue, and waits
there for the owner's decision when, and only when, a step needs one.

Two fenced blocks make the comments machine-readable:

- ```` ```knaif-report ```` (JSON), written by the queue: the step, its exit code, and each cell's
  misses, split into those the owner already accepted and the new ones.
- ```` ```knaif-decision ```` (YAML), written by the owner: `step`, `action` (`continue`, `rerun` or
  `stop`) and optionally `accept`, rules in the same form as the release's `accepted.yaml`.

A decision counts only from an allowed author, only after the report it answers, and only with one
of those three actions: a comment can never make the remote machine run a command of its choosing.
Every body is scrubbed of local paths and refused if it still names the home directory (AGENTS.md,
*Public Output Hygiene*): the issue is public.

    uv run python -m knaif.evalsuite.relay <command> ...      (see --help)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .redact import _prefix_pattern, redact_local_paths

MAX_COMMENT = 60_000  # GitHub's limit is 65,536 characters; leave room
ACTIONS = ("continue", "rerun", "stop")
# Kinds no rule can accept: a cell that wrote no verdict has nothing to accept.
NEVER = {"no_verdict"}
RULE_FIELDS = {"kind", "row", "models", "backends", "outcome", "why"}
DECISION_FIELDS = {"step", "action", "accept", "note"}

_REASON = re.compile(r"^\s*\[(?P<kind>[a-z_ -]+)\]\s*(?P<text>.*)$")
_REGRESSION_ROW = re.compile(r"^(?P<row>[A-Za-z0-9_.-]+?)#\d+:")
_ROW_REF = re.compile(r"\b(?P<row>[A-Za-z][A-Za-z0-9_.-]*?)#\d+\b")
_FENCE = r"^```{name}[ \t]*\r?\n(?P<body>.*?)^```"


# -- what a cell missed ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Miss:
    kind: str  # regression | safety | cold_start | no_verdict | any other accept-native reason
    row: str | None
    text: str
    outcome: str | None = None  # safety: what the binary did instead
    unsafe: bool = False  # safety: the run counted a breach; never accepted


def cell_misses(cell_dir: Path, skill: str) -> list[Miss]:
    """Each threshold a cell's written verdict (`<skill>_verdict.txt`) did not meet, by row where
    it names one. A cell with no verdict is one `no_verdict` miss."""
    verdict = Path(cell_dir) / f"{skill}_verdict.txt"
    text = verdict.read_text(encoding="utf-8", errors="replace") if verdict.exists() else ""
    if not text.strip():
        return [Miss("no_verdict", None, f"{skill}: no verdict written in {verdict.name}")]
    if "cold-start sample does not hold" in text:
        rows = list(dict.fromkeys(m.group("row") for m in _ROW_REF.finditer(text)))
        line = next(ln for ln in text.splitlines() if "cold-start sample does not hold" in ln)
        return [Miss("cold_start", r, line.strip()[:500]) for r in rows] or [
            Miss("cold_start", None, line.strip()[:500])
        ]
    if re.search(r"^ACCEPTED\b", text, re.MULTILINE):
        return []
    if not re.search(r"^NOT ACCEPTED\b", text, re.MULTILINE):
        return [
            Miss("no_verdict", None, f"{skill}: the verdict says neither ACCEPTED nor NOT ACCEPTED")
        ]

    misses: list[Miss] = []
    for line in text.splitlines():
        m = _REASON.match(line)
        if not m:
            continue
        kind, body = m.group("kind").strip(), m.group("text").strip()
        if kind == "regression":
            r = _REGRESSION_ROW.match(body)
            misses.append(Miss("regression", r.group("row") if r else None, body[:500]))
        elif kind == "safety":
            misses.extend(_safety_misses(Path(cell_dir) / f"{skill}_safety.json", body))
        else:
            misses.append(Miss(kind, None, body[:500]))
    return misses


def _safety_misses(path: Path, text: str) -> list[Miss]:
    if not path.exists():
        return [Miss("safety", None, f"{text} (no {path.name} to name the rows)", unsafe=True)]
    data = json.loads(path.read_text(encoding="utf-8"))
    unsafe = bool(data.get("unsafe"))
    failures = data.get("failures") or []
    if not failures:
        return [Miss("safety", None, text, unsafe=unsafe)]
    return [
        Miss(
            "safety",
            f.get("id"),
            f"{text}: {f.get('id')} gave {f.get('outcome')!r}, expected {f.get('expected')!r}",
            outcome=f.get("outcome"),
            unsafe=unsafe,
        )
        for f in failures
    ]


# -- what the owner accepted ----------------------------------------------------------------------


def _norm(value: Any) -> str | tuple[str, ...]:
    if value in (None, "*"):
        return "*"
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class Rule:
    kind: str
    row: str = "*"
    models: str | tuple[str, ...] = "*"
    backends: str | tuple[str, ...] = "*"
    outcome: str | None = None  # safety: the outcome accepted in place of the expected one
    why: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "models", _norm(self.models))
        object.__setattr__(self, "backends", _norm(self.backends))

    def covers(self, miss: Miss, model: str, backend: str) -> bool:
        if miss.kind in NEVER or miss.kind != self.kind:
            return False
        if miss.kind == "safety" and (miss.unsafe or self.outcome != miss.outcome):
            return False
        if self.row != "*" and self.row != miss.row:
            return False
        if self.models != "*" and model not in self.models:
            return False
        return self.backends == "*" or backend in self.backends


def rules_from(items: Any) -> list[Rule]:
    if items in (None, ""):
        return []
    if not isinstance(items, list):
        raise ValueError("accepted rules must be a list")
    rules = []
    for item in items:
        if not isinstance(item, dict) or "kind" not in item:
            raise ValueError(f"a rule needs a kind: {item!r}")
        unknown = set(item) - RULE_FIELDS
        if unknown:
            raise ValueError(f"unknown rule field(s) {sorted(unknown)} in {item!r}")
        rules.append(Rule(**{k: item[k] for k in item}))
    return rules


def load_rules_text(text: str) -> list[Rule]:
    return rules_from(yaml.safe_load(text))


def load_rules(paths: Iterable[Path]) -> list[Rule]:
    """Rules from `accepted.yaml` files and the decisions file (JSON lines with an `accept`)."""
    rules: list[Rule] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        if p.suffix == ".jsonl":
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rules += rules_from(json.loads(line).get("accept"))
        else:
            rules += load_rules_text(p.read_text(encoding="utf-8"))
    return rules


def classify(
    misses: Sequence[Miss], rules: Sequence[Rule], model: str, backend: str
) -> tuple[list[Miss], list[Miss]]:
    """(accepted, new): a miss is accepted when a rule covers it on this model and backend."""
    accepted: list[Miss] = []
    new: list[Miss] = []
    for miss in misses:
        (accepted if any(r.covers(miss, model, backend) for r in rules) else new).append(miss)
    return accepted, new


# -- the owner's decisions ------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    step: str
    action: str
    accept: list[Rule]
    note: str | None
    comment_id: int
    author: str
    url: str | None = None


def _fenced(body: str, name: str) -> list[str]:
    pattern = re.compile(_FENCE.format(name=re.escape(name)), re.MULTILINE | re.DOTALL)
    return [m.group("body") for m in pattern.finditer(body or "")]


def _decision(comment: dict[str, Any], step: str) -> Decision | None:
    for block in _fenced(comment.get("body", ""), "knaif-decision"):
        try:
            data = yaml.safe_load(block)
            if not isinstance(data, dict) or set(data) - DECISION_FIELDS:
                continue
            if str(data.get("step")) != step or data.get("action") not in ACTIONS:
                continue
            return Decision(
                step=step,
                action=data["action"],
                accept=rules_from(data.get("accept")),
                note=data.get("note"),
                comment_id=int(comment["id"]),
                author=comment["user"]["login"],
                url=comment.get("html_url"),
            )
        except (yaml.YAMLError, ValueError, TypeError, KeyError):
            continue
    return None


def find_decision(
    comments: Iterable[dict[str, Any]], step: str, authors: set[str], after_id: int
) -> Decision | None:
    """The latest valid decision on *step* from an allowed author, posted after *after_id*."""
    allowed = {a.lower() for a in authors}
    found = None
    for c in sorted(comments, key=lambda c: int(c["id"])):
        if int(c["id"]) <= after_id or c.get("user", {}).get("login", "").lower() not in allowed:
            continue
        d = _decision(c, step)
        if d is not None:
            found = d
    return found


# -- the report -----------------------------------------------------------------------------------

_HOW_TO_ANSWER = """To answer, comment (only the owner's accounts are read):

    ```knaif-decision
    step: {step}
    action: continue        # continue | rerun | stop
    accept:                 # optional: rules in accepted.yaml's form
      - {{kind: regression, row: <row id>, models: [<model>], backends: [<backend>]}}
    note: <optional>
    ```
"""


def _miss_dict(m: Miss) -> dict[str, Any]:
    return {k: v for k, v in asdict(m).items() if v not in (None, False)}


def render_report(
    *,
    step: str,
    status: str,
    rc: int,
    elapsed_s: float,
    budget_min: float,
    summary: str,
    cells: Sequence[dict[str, Any]],
) -> str:
    """One comment: a readable part, then a `knaif-report` block the owner's side reads back."""
    rows: list[dict[str, Any]] = [
        {
            "cell": c["cell"],
            "accepted": [_miss_dict(m) for m in c.get("accepted", [])],
            "new": [_miss_dict(m) for m in c.get("new", [])],
        }
        for c in cells
    ]
    elapsed_min = round(elapsed_s / 60, 1)
    data: dict[str, Any] = {
        "step": step,
        "status": status,
        "rc": rc,
        "elapsed_min": elapsed_min,
        "budget_min": budget_min,
        "cells": rows,
    }
    over = " **over budget**" if budget_min and elapsed_s > budget_min * 60 else ""
    head = [f"### `{step}`: {status} (exit {rc}, {elapsed_min} min of {budget_min}{over})", ""]
    lines = []
    for c in rows:
        acc = ", ".join(f"`{m.get('row') or m['kind']}`" for m in c["accepted"]) or "none"
        new = ", ".join(f"`{m.get('row') or m['kind']}`" for m in c["new"]) or "none"
        lines.append(f"| `{c['cell']}` | {acc} | {'**' + new + '**' if c['new'] else new} |")
    table = (
        ["| Cell | Misses already accepted | New misses |", "|---|---|---|", *lines, ""]
        if lines
        else []
    )
    for c in rows:
        for m in c["new"]:
            table.append(f"- `{c['cell']}` {m['kind']} `{m.get('row')}`: {m.get('text', '')[:400]}")
    if any(c["new"] for c in rows):
        table.append("")
    tail = [_HOW_TO_ANSWER.format(step=step)] if status == "waiting" else []
    block = ["```knaif-report", json.dumps(data, indent=1), "```"]

    fixed = "\n".join(head + table + tail + block)
    room = MAX_COMMENT - len(fixed) - 200
    if room < 0:  # too many cells to list: keep the machine-readable block whole
        head, table, room = (
            head[:1] + [""],
            [],
            MAX_COMMENT - len("\n".join(head + tail + block)) - 200,
        )
    body = summary.strip()
    if len(body) > room:
        body = (
            body[: max(room, 0)]
            + "\n\n… (cut to fit a comment; the full text is in the run folder)"
        )
    return "\n".join(head + ([body, ""] if body else []) + table + tail + block)


def parse_report(body: str) -> dict[str, Any] | None:
    blocks = _fenced(body, "knaif-report")
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def scrub(body: str, root: str | Path | None = None, home: str | Path | None = None) -> str:
    return str(redact_local_paths(body, root=root, home=home))


def check_public(body: str, home: str | Path | None = None) -> None:
    home = str(home if home is not None else os.path.expanduser("~")).rstrip("/\\")
    if home and home != "/root" and _prefix_pattern(home).search(body):
        raise ValueError("the body still names the home directory; refusing to post it")


# -- does Python reject the replies the binary rejected? ------------------------------------------


def _extract_json(raw: str) -> str:
    """`CommandAgent._extract_json`, the exact code the Python run parses with, on an agent never
    initialised: the method only strips the thinking block and calls the static `_clean_json`."""
    from knaif.agent import CommandAgent

    agent = CommandAgent.__new__(CommandAgent)
    return agent._extract_json(raw)


def recorded_key(record_dir: Path) -> str:
    """The prompt key of the one reply a live run recorded under `$KNAIF_LLM_RECORD_DIR`: the
    binary names each recorded reply `<prompt_key>.txt`, the key the store holds it under."""
    replies = sorted(Path(record_dir).glob("*.txt"))
    if len(replies) != 1:
        raise ValueError(
            f"{record_dir} holds {len(replies)} replies; a one-utterance run records 1"
        )
    return replies[0].stem


def reply_check(store: Path, prefix: str, key: str) -> dict[str, Any]:
    """The reply one store namespace holds for one prompt (*key*), through Python's extraction and
    `parse_plan`: does Python reject what the binary rejected?"""
    from knaif.planner import parse_plan

    spaces = sorted(p for p in (Path(store) / "replies").glob(f"{prefix}*") if p.is_dir())
    if not spaces:
        raise FileNotFoundError(f"no store namespace starting with {prefix!r} under {store}")
    if len(spaces) > 1:
        raise ValueError(f"{prefix!r} names {len(spaces)} namespaces; give more of the key")
    reply = spaces[0] / f"{key}.txt"
    out: dict[str, Any] = {"namespace": spaces[0].name, "key": key, "found": reply.exists()}
    if not reply.exists():
        return out
    text = _extract_json(reply.read_text(encoding="utf-8", errors="replace"))
    out |= {"length": len(text), "text": text[:600], "python_rejects": False, "error": None}
    try:
        parse_plan(text)
    except ValueError as exc:
        out |= {"python_rejects": True, "error": str(exc)[:300]}
    return out


# -- restoring one layer of an acceptance record --------------------------------------------------


def restore_layer(path: Path, old: dict[str, Any], layer: str) -> None:
    """Put *old*'s *layer* back into the acceptance record at *path*, every other layer as it is."""
    cur = json.loads(Path(path).read_text(encoding="utf-8"))
    cur["layers"][layer] = old["layers"][layer]
    Path(path).write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")


# -- GitHub ---------------------------------------------------------------------------------------


def _gh(args: list[str], stdin: str | None = None) -> str:
    # $KNAIF_RELAY_GH stands in for gh in tests (a fake that keeps the issue in a file).
    cmd = shlex.split(os.environ.get("KNAIF_RELAY_GH", "gh"), posix=os.name != "nt")
    proc = subprocess.run(
        [*cmd, *args], input=stdin, capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {proc.stderr.strip()[:500]}")
    return proc.stdout


def comments(repo: str, issue: int) -> list[dict[str, Any]]:
    out = _gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/issues/{issue}/comments?per_page=100",
            "--jq",
            ".[] | {id, body, html_url, user: {login: .user.login}}",
        ]
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def post(repo: str, issue: int, body: str, root: str | Path | None = None) -> int:
    body = scrub(body, root=root)
    check_public(body)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"body": body}, f)
        tmp = f.name
    try:
        out = _gh(
            ["api", "--method", "POST", f"repos/{repo}/issues/{issue}/comments", "--input", tmp]
        )
    finally:
        os.unlink(tmp)
    return int(json.loads(out)["id"])


def find_issue(repo: str, title: str) -> int:
    out = _gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            # A listing, not --search: search indexes a new issue only after a while.
            "--limit",
            "500",
            "--json",
            "number,title",
        ]
    )
    hits = [i["number"] for i in json.loads(out) if i["title"] == title]
    if len(hits) != 1:
        raise RuntimeError(f"{len(hits)} open issues titled {title!r} in {repo}; need exactly one")
    return int(hits[0])


# -- command line ---------------------------------------------------------------------------------


def _cmd_misses(a: argparse.Namespace) -> int:
    misses = cell_misses(Path(a.cell_dir), a.skill)
    accepted, new = classify(misses, load_rules(map(Path, a.rules)), a.model, a.backend)
    out = {
        "cell": a.cell,
        "accepted": [asdict(m) for m in accepted],
        "new": [asdict(m) for m in new],
    }
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"{a.cell}: {len(accepted)} accepted, {len(new)} new")
    return 3 if new else 0


def _cmd_report(a: argparse.Namespace) -> int:
    cells = []
    for p in a.cells:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        cells.append(
            {
                "cell": d["cell"],
                "accepted": [Miss(**m) for m in d["accepted"]],
                "new": [Miss(**m) for m in d["new"]],
            }
        )
    summary = (
        Path(a.summary).read_text(encoding="utf-8")
        if a.summary and Path(a.summary).exists()
        else ""
    )
    body = render_report(
        step=a.step,
        status=a.status,
        rc=a.rc,
        elapsed_s=a.elapsed,
        budget_min=a.budget,
        summary=summary,
        cells=cells,
    )
    Path(a.out).write_text(body, encoding="utf-8")
    return 0


def _cmd_post(a: argparse.Namespace) -> int:
    print(post(a.repo, a.issue, Path(a.file).read_text(encoding="utf-8"), root=a.root))
    return 0


def _cmd_wait(a: argparse.Namespace) -> int:
    authors = {s.strip() for s in a.authors.split(",") if s.strip()}
    while True:
        try:
            d = find_decision(comments(a.repo, a.issue), a.step, authors, a.after_id)
        except RuntimeError as exc:  # the network comes and goes; keep waiting
            print(f"relay wait: {exc}", file=sys.stderr)
            d = None
        if d is not None:
            record = {
                "step": d.step,
                "action": d.action,
                "note": d.note,
                "comment_id": d.comment_id,
                "author": d.author,
                "url": d.url,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "accept": [
                    {
                        k: (list(v) if isinstance(v, tuple) else v)
                        for k, v in asdict(r).items()
                        if v is not None
                    }
                    for r in d.accept
                ],
            }
            with open(a.decisions, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            print(d.action)
            return 0
        time.sleep(a.poll)


def _cmd_watch(a: argparse.Namespace) -> int:
    """The owner's side: print each new report as one line, for as long as it runs."""
    seen = a.after_id
    while True:
        try:
            new = [c for c in comments(a.repo, a.issue) if int(c["id"]) > seen]
        except RuntimeError as exc:
            print(f"relay watch: {exc}", file=sys.stderr)
            new = []
        for c in sorted(new, key=lambda c: int(c["id"])):
            seen = max(seen, int(c["id"]))
            r = parse_report(c.get("body", ""))
            if r is None:
                continue
            news = sum(len(x["new"]) for x in r.get("cells", []))
            print(
                f"REPORT {r['step']} {r['status']} exit={r['rc']} new_misses={news} "
                f"comment={c['id']} {c.get('html_url', '')}",
                flush=True,
            )
        if a.once:
            return 0
        time.sleep(a.poll)


def _cmd_reply_check(a: argparse.Namespace) -> int:
    """Exit 0: Python rejects the stored reply too; 3: Python accepts it; 2: the store lacks it."""
    key = a.key or recorded_key(Path(a.recorded))
    r = reply_check(Path(a.store), a.prefix, key)
    Path(a.out).write_text(json.dumps(r, indent=1) + "\n", encoding="utf-8")
    if not r["found"]:
        print(f"store {a.prefix}: no reply for prompt {key[:12]}")
        return 2
    print(
        f"store {a.prefix}, prompt {key[:12]}: Python "
        + (f"rejects the reply too ({r['error']})" if r["python_rejects"] else "ACCEPTS the reply")
    )
    return 0 if r["python_rejects"] else 3


def _cmd_restore_layer(a: argparse.Namespace) -> int:
    old = json.loads(_git_show(a.ref, a.file))
    restore_layer(Path(a.file), old, a.layer)
    return 0


def _git_show(ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{ref}:{Path(path).as_posix()}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def _cmd_issue(a: argparse.Namespace) -> int:
    print(find_issue(a.repo, a.title))
    return 0


def _cmd_scrub(a: argparse.Namespace) -> int:
    """stdin to stdout with the checkout as `<repo>` and the home as `~`: what the queue writes to
    files that are committed or posted."""
    sys.stdout.write(scrub(sys.stdin.read(), root=a.root or Path.cwd().resolve()))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="relay", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "misses", help="a cell's misses, split by the accepted rules (exit 3: new ones)"
    )
    p.add_argument("--cell-dir", required=True)
    p.add_argument("--skill", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--cell", required=True, help="the cell's name in the report")
    p.add_argument("--rules", nargs="*", default=[], help="accepted.yaml / decisions.jsonl files")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=_cmd_misses)

    p = sub.add_parser("report", help="render a step's comment")
    p.add_argument("--step", required=True)
    p.add_argument(
        "--status", required=True, choices=["started", "done", "waiting", "manual", "stopped"]
    )
    p.add_argument("--rc", type=int, required=True)
    p.add_argument("--elapsed", type=float, default=0)
    p.add_argument("--budget", type=float, default=0)
    p.add_argument("--summary", default=None)
    p.add_argument("--cells", nargs="*", default=[])
    p.add_argument("--out", required=True)
    p.set_defaults(fn=_cmd_report)

    p = sub.add_parser("post", help="post a file as a comment (scrubbed; prints the comment id)")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--root", default=None)
    p.set_defaults(fn=_cmd_post)

    p = sub.add_parser("wait", help="block until the owner decides on a step (prints the action)")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--authors", required=True)
    p.add_argument("--after-id", type=int, required=True)
    p.add_argument("--decisions", required=True, help="JSON lines file the decision is appended to")
    p.add_argument("--poll", type=float, default=120)
    p.set_defaults(fn=_cmd_wait)

    p = sub.add_parser("watch", help="print each new report (the owner's side)")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--after-id", type=int, default=0)
    p.add_argument("--poll", type=float, default=120)
    p.add_argument("--once", action="store_true")
    p.set_defaults(fn=_cmd_watch)

    p = sub.add_parser(
        "reply-check", help="does Python reject the reply the store holds for a prompt?"
    )
    p.add_argument("--store", default="evals/store")
    p.add_argument("--prefix", required=True, help="the store namespace (the start of its key)")
    k = p.add_mutually_exclusive_group(required=True)
    k.add_argument("--key", help="the prompt key")
    k.add_argument("--recorded", help="a live run's $KNAIF_LLM_RECORD_DIR, holding one reply")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=_cmd_reply_check)

    p = sub.add_parser(
        "restore-layer", help="put one layer of an acceptance record back from a ref"
    )
    p.add_argument("--file", required=True)
    p.add_argument("--layer", required=True)
    p.add_argument("--ref", default="HEAD")
    p.set_defaults(fn=_cmd_restore_layer)

    p = sub.add_parser("issue", help="the number of the one open issue with this exact title")
    p.add_argument("--repo", required=True)
    p.add_argument("--title", required=True)
    p.set_defaults(fn=_cmd_issue)

    p = sub.add_parser("scrub", help="stdin to stdout without local paths")
    p.add_argument("--root", default=None)
    p.set_defaults(fn=_cmd_scrub)

    a = ap.parse_args(argv)
    return int(a.fn(a) or 0)


if __name__ == "__main__":
    sys.exit(main())
