"""The corpus review's duplicate scan (plan 2026-10-14-faster-release-evidence, Part G, T7).

Part G removes duplicate and near-duplicate rows and appends new ones; kept rows never change.
This module finds the candidates. It is deliberately conservative about what it **proposes**:
a row is proposed for removal only when every one of its utterances is already in another row
that expects exactly the same result (same tools, outcome, fixture and success criteria), and
never when it is a safety row (G2.5). Everything looser — the same words with a different
expected result, paraphrases inside a group of rows that expect the same result — is listed for
a person to judge. Nothing is removed until the owner approves the list (T7).

Text is compared after `normalize`, which folds case and width and strips punctuation from the
edges of words in any script. The 2026-10-15 one-off scan kept only `a-z0-9`, so every
non-Latin utterance collapsed to its file names and unrelated rows looked identical.
"""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

Row = dict[str, Any]


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "PS"


def normalize(text: str) -> str:
    """Case- and width-folded, words split on whitespace, punctuation stripped from each word's
    edges (so `clip.mp4` keeps its dot and `silent.` loses it)."""
    words = []
    for word in unicodedata.normalize("NFKC", text).casefold().split():
        start, end = 0, len(word)
        while start < end and _is_punct(word[start]):
            start += 1
        while end > start and _is_punct(word[end - 1]):
            end -= 1
        if start < end:
            words.append(word[start:end])
    return " ".join(words)


def expectation(row: Row) -> str:
    """Everything a row expects, as one comparable string: two rows with the same expectation
    test the same thing, whatever their words."""
    tools = row.get("expected_tools") or [row.get("expected_tool")]
    return json.dumps(
        {
            "tools": tools,
            "outcome": row.get("expected_outcome"),
            "fixture": row.get("fixture"),
            "criteria": row.get("success_criteria") or {},
            "outputs": [o.get("criteria") for o in row.get("outputs") or []],
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _is_safety(row: Row) -> bool:
    return "safety" in (row.get("tags") or [])


def _differs_in(rows: Sequence[Row]) -> list[str]:
    """Which parts of the expected result differ between *rows* (empty when they agree)."""
    parts = [json.loads(expectation(r)) for r in rows]
    return [k for k in parts[0] if any(p[k] != parts[0][k] for p in parts[1:])]


def duplicate_utterances(rows: Sequence[Row]) -> dict[str, list[dict[str, Any]]]:
    """Utterances that appear in more than one row, and utterances repeated inside one row."""
    seen: dict[str, list[str]] = defaultdict(list)
    within: list[dict[str, Any]] = []
    by_id = {r["id"]: r for r in rows}
    for row in rows:
        counts: dict[str, int] = defaultdict(int)
        for u in row["utterances"]:
            counts[normalize(u)] += 1
        for text, n in counts.items():
            seen[text].append(row["id"])
            if n > 1:
                within.append({"row": row["id"], "text": text, "count": n})
    across = [
        {
            "text": text,
            "rows": ids,
            "same_expectation": len({expectation(by_id[i]) for i in ids}) == 1,
            "differs_in": _differs_in([by_id[i] for i in ids]),
        }
        for text, ids in seen.items()
        if len(ids) > 1
    ]
    return {"across_rows": across, "within_row": within}


def label_groups(rows: Sequence[Row]) -> list[dict[str, Any]]:
    """Rows that expect exactly the same result, in groups of two or more, in corpus order."""
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        groups[expectation(row)].append(row["id"])
    return [
        {"expectation": json.loads(key), "rows": ids} for key, ids in groups.items() if len(ids) > 1
    ]


def _similarity(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x | y else 1.0


def near_duplicates(rows: Sequence[Row], *, threshold: float = 0.8) -> list[dict[str, Any]]:
    """Pairs of rows in the same group whose closest utterances share at least *threshold* of
    their words (Jaccard over normalized words) without being the same text. Exact repeats are
    `duplicate_utterances`; this
    finds the paraphrases a person should look at. Word overlap undercounts scripts written
    without spaces, so a non-Latin pair can be missed here, never invented."""
    by_id = {r["id"]: r for r in rows}
    pairs = []
    for group in label_groups(rows):
        ids = group["rows"]
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                best = max(
                    (
                        (_similarity(normalize(u), normalize(v)), u, v)
                        for u in by_id[a]["utterances"]
                        for v in by_id[b]["utterances"]
                    ),
                    key=lambda t: t[0],
                )
                # an exact repeat is already listed by `duplicate_utterances`
                if best[0] >= threshold and normalize(best[1]) != normalize(best[2]):
                    pairs.append(
                        {
                            "a": a,
                            "b": b,
                            "similarity": round(best[0], 2),
                            "utterance_a": best[1],
                            "utterance_b": best[2],
                        }
                    )
    return pairs


def removal_candidates(rows: Sequence[Row]) -> list[dict[str, str]]:
    """Rows to PROPOSE for removal: every utterance is already in another row that expects the
    same result, so removing it loses no test. The larger row is kept (the earlier one when
    both hold the same utterances), a kept row is never itself removed, and safety rows are
    never proposed (G2.5). Returned in corpus order."""
    order = {r["id"]: i for i, r in enumerate(rows)}
    texts = {r["id"]: {normalize(u) for u in r["utterances"]} for r in rows}
    exp = {r["id"]: expectation(r) for r in rows}
    kept: list[str] = []
    proposed = []
    # Largest first, so the row a smaller one is measured against is already final.
    for row in sorted(rows, key=lambda r: (-len(texts[r["id"]]), order[r["id"]])):
        rid = row["id"]
        match = None
        if not _is_safety(row):
            match = next((k for k in kept if exp[k] == exp[rid] and texts[rid] <= texts[k]), None)
        if match is None:
            kept.append(rid)
        else:
            reason = f"every utterance is in {match}, with the same expected result"
            proposed.append({"id": rid, "kept": match, "reason": reason})
    return sorted(proposed, key=lambda c: order[c["id"]])


def coverage(rows: Sequence[Row], public_tools: Iterable[str]) -> dict[str, Any]:
    """Rows per model-visible tool (single-step plan rows and chain rows), the outcome totals,
    and the tools with no single-step plan row: where Part G's new rows should go first.

    Clarify and reject rows name `clarify`/`reject` or no tool at all, not the tool the request
    was about, so they are counted as outcomes, never per tool."""
    tools = {t: {"plan": 0, "chain": 0} for t in public_tools}
    outcomes: dict[str, int] = defaultdict(int)
    chain_rows = 0
    for row in rows:
        outcomes[row["expected_outcome"]] += 1
        named = row.get("expected_tools") or [row.get("expected_tool")]
        is_chain = len(row.get("expected_tools") or []) > 1 or bool(row.get("outputs"))
        chain_rows += is_chain
        for tool in dict.fromkeys(named):
            if tool in tools and row["expected_outcome"] == "plan":
                tools[tool]["chain" if is_chain else "plan"] += 1
    return {
        "tools": tools,
        "outcomes": dict(outcomes),
        "chain_rows": chain_rows,
        "no_plan_row": [t for t, c in tools.items() if c["plan"] == 0],
    }


def safety_duplicates(safety_rows: Sequence[Row]) -> list[dict[str, Any]]:
    """Repeated utterances in the safety corpus: reported only, since a category never shrinks."""
    seen: dict[str, list[str]] = defaultdict(list)
    for row in safety_rows:
        seen[normalize(row["utterance"])].append(row["id"])
    return [{"text": t, "rows": ids} for t, ids in seen.items() if len(ids) > 1]


def audit_rows(
    skill: str,
    rows: Sequence[Row],
    safety_rows: Sequence[Row],
    public_tools: Iterable[str],
    *,
    corpus_sha256: str,
) -> dict[str, Any]:
    """The whole audit as data: what `render_markdown` prints and the CLI saves."""
    return {
        "skill": skill,
        "corpus_sha256": corpus_sha256,
        "rows": len(rows),
        "utterances": sum(len(r["utterances"]) for r in rows),
        "duplicates": duplicate_utterances(rows),
        "groups": label_groups(rows),
        "near_duplicates": near_duplicates(rows),
        "proposed_removals": removal_candidates(rows),
        "coverage": coverage(rows, public_tools),
        "safety": {"rows": len(safety_rows), "duplicates": safety_duplicates(safety_rows)},
    }


def render_markdown(audit: dict[str, Any]) -> str:
    """A report for the owner: what is proposed, and what needs a person's judgement."""
    d = audit["duplicates"]
    groups = audit["groups"]
    out = [
        f"# Corpus audit — {audit['skill']}",
        "",
        f"Corpus sha256 `{audit['corpus_sha256']}`: {audit['rows']} rows, "
        f"{audit['utterances']} utterances. Generated by `evalsuite corpus-audit`.",
        "",
        "Nothing is removed until the owner approves this list (plan "
        "2026-10-14-faster-release-evidence, T7). Removed ids go to `data/eval_removed.jsonl` "
        "with the row kept in their place; kept rows never change.",
        "",
        "| | |",
        "|---|---:|",
        f"| Utterances in more than one row | {len(d['across_rows'])} |",
        f"| … of which the rows expect different results | "
        f"{sum(not x['same_expectation'] for x in d['across_rows'])} |",
        f"| Utterances repeated inside one row | {len(d['within_row'])} |",
        f"| Groups of rows expecting the same result | {len(groups)} "
        f"({sum(len(g['rows']) for g in groups)} rows) |",
        f"| Near-duplicate pairs in those groups | {len(audit['near_duplicates'])} |",
        f"| Proposed for removal | {len(audit['proposed_removals'])} |",
        "",
        f"## Proposed for removal ({len(audit['proposed_removals'])})",
        "",
    ]
    if audit["proposed_removals"]:
        out += ["| Row | Kept in its place | Reason |", "|---|---|---|"]
        out += [f"| {c['id']} | {c['kept']} | {c['reason']} |" for c in audit["proposed_removals"]]
    else:
        out.append("None.")
    out += ["", "## Same words, different expected result (a labelling question)", ""]
    conflicts = [x for x in d["across_rows"] if not x["same_expectation"]]
    out += [
        f"- `{x['text']}`: {', '.join(x['rows'])} (differ in: {', '.join(x['differs_in'])})"
        for x in conflicts
    ] or ["None."]
    out += [
        "",
        "## Same words, same expected result, not proposed (the row adds other utterances)",
        "",
    ]
    proposed = {c["id"] for c in audit["proposed_removals"]}
    agreeing = [
        x for x in d["across_rows"] if x["same_expectation"] and not set(x["rows"]) & proposed
    ]
    out += [f"- `{x['text']}`: {', '.join(x['rows'])}" for x in agreeing] or ["None."]
    out += ["", "## Repeated inside one row", ""]
    out += [f"- {x['row']}: `{x['text']}` ×{x['count']}" for x in d["within_row"]] or ["None."]
    out += ["", "## Near-duplicates for a person to judge (same expected result)", ""]
    if audit["near_duplicates"]:
        out += ["| Rows | Similarity | Closest utterances |", "|---|---:|---|"]
        out += [
            f"| {p['a']}, {p['b']} | {p['similarity']} | `{p['utterance_a']}` / "
            f"`{p['utterance_b']}` |"
            for p in audit["near_duplicates"]
        ]
    else:
        out.append("None.")
    cov = audit["coverage"]
    out += [
        "",
        "## Coverage (where new rows go first)",
        "",
        f"Chain rows: {cov['chain_rows']}. Expected outcomes: "
        + ", ".join(f"{k} {v}" for k, v in sorted(cov["outcomes"].items()))
        + f". Tools with no single-step plan row: {', '.join(cov['no_plan_row']) or 'none'}.",
        "",
        "| Tool | single-step plan rows | chain rows |",
        "|---|---:|---:|",
    ]
    out += [f"| {t} | {c['plan']} | {c['chain']} |" for t, c in cov["tools"].items()]
    s = audit["safety"]
    out += ["", f"## Safety corpus ({s['rows']} rows; never shrinks)", ""]
    out += [f"- `{x['text']}`: {', '.join(x['rows'])}" for x in s["duplicates"]] or [
        "No repeated utterances."
    ]
    return "\n".join(out) + "\n"
