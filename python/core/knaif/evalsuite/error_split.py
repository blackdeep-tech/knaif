"""Error split: where each wrong eval row went wrong.

A scored row says *that* a request failed, not *where*. Retrieval runs before the model and
gates run after it, so a wrong answer can come from any of the three. This module joins a saved
scoreboard with the corpus and with the tools each prompt showed, and puts every row in one
bucket (`BUCKETS`), by language and by tag.

What the model was shown is not in a scoreboard. `capture_shown` rebuilds it by running the
eval's own retrieval (`retrieve_tools(utterance, agent.registry)`) per utterance. Retrieval and
tool descriptions change over time, so capture it with the code the scored run used; this module
imports only the standard library at top level so `capture_shown` also runs against an older
`knaif` tree.

A scoreboard row's `plan` is the plan after core's rewrites and gates, not always the model's
own (`model_plan` is recorded from 2026-10 on). The gate buckets therefore read the gates' fixed
wording; see `classify`.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_CORE = {"clarify", "reject", "done", "wait_for_confirmation"}
# The agent's own fixed texts: a parse failure, and the gate that refuses a plan naming a file
# the request never mentioned (`agent.infer`).
_PARSE_QUESTION = "Could not parse your request. Please rephrase."
_INVENTED_FILE = re.compile(r"^You didn't mention .+ in your request")
# A corpus language tag, and the script its utterances are written in. Latin-script languages
# only claim accented text: an all-ASCII paraphrase in such a row is counted as English.
_LANGUAGE_SCRIPT = {
    "zh": "cjk",
    "ja": "cjk",
    "ko": "cjk",
    "bg": "cyrillic",
    "ru": "cyrillic",
    "de": "latin",
    "es": "latin",
    "fr": "latin",
    "it": "latin",
    "pt": "latin",
}
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")

BUCKETS: dict[str, str] = {
    "correct": "right outcome, right tools, full score",
    # A plan was expected.
    "retriever_miss": "an expected tool was not shown, and the model left it out",
    "model_asked": "the model asked a question instead of acting",
    "model_refused": "the model refused instead of acting",
    "gate_asked": "the model planned; a validation gate turned the plan into a question",
    "gate_invented_file": "the model named a file the request never gave; the gate asked",
    "wrong_tool": "the expected tools were shown; the model planned other tools",
    "execution_failed": "the right tools, but running them failed",
    "parse_error": "the model's output was not a valid plan",
    "arg_error": "the right tools with wrong arguments (score below 1)",
    # A question or a refusal was expected.
    "acted_instead": "a question or refusal was expected; the model planned",
    "refused_instead": "a question was expected; the model refused",
    "asked_instead": "a refusal was expected; the model asked",
    "other": "none of the above",
}


def _key(row: dict[str, Any]) -> str:
    return f"{row['id']}#{row.get('utterance_idx', 0)}"


def _steps(row: dict[str, Any]) -> list[dict[str, Any]]:
    plan = (row.get("plan") or {}).get("plan") or []
    return [s for s in plan if isinstance(s, dict)]


def _expected_tools(corpus_row: dict[str, Any]) -> list[str]:
    tools = corpus_row.get("expected_tools") or (
        [corpus_row["expected_tool"]] if corpus_row.get("expected_tool") else []
    )
    return [t for t in tools if t not in _CORE]


def _script(utterance: str) -> str:
    if _CJK.search(utterance):
        return "cjk"
    if _CYRILLIC.search(utterance):
        return "cyrillic"
    return "en" if all(ord(c) < 128 for c in utterance) else "latin"


def language(tags: list[str], utterance: str) -> str:
    """One utterance's language: the row's language tag when the utterance's script fits it,
    else the script itself (`cjk`, `cyrillic`, `latin`, or `en` for plain ASCII).

    A row's utterances are paraphrases, and many rows carry paraphrases in other languages
    than their tags name, so a row tag alone would mislabel them.
    """
    script = _script(utterance)
    for t in tags:
        if _LANGUAGE_SCRIPT.get(t) == script:
            return t
    return script


def classify(row: dict[str, Any], expected_tools: list[str], shown: list[str] | None) -> str:
    """One bucket of `BUCKETS` for a scored row.

    *expected_tools* are the corpus's tools for the row, in order; *shown* the model-visible
    tools its prompt listed, or None when unknown (then there is no retriever bucket).

    The stored plan is read for what the gates leave behind: a real tool beside a `clarify`
    outcome means a gate asked after the model planned; the invented-file gate replaces the
    plan with its own fixed question.
    """
    expected = [t for t in expected_tools if t not in _CORE]
    steps = _steps(row)
    tools = [s.get("tool") for s in steps if s.get("tool") != "done"]
    model_tools = [t for t in tools if t not in _CORE]
    want, got = row["expected_outcome"], row["actual_outcome"]
    question = next(
        (
            str((s.get("args") or {}).get("question", ""))
            for s in steps
            if s.get("tool") == "clarify"
        ),
        "",
    )
    parse = got == "parse_error" or question == _PARSE_QUESTION

    if want != "plan":
        if row["outcome_correct"]:
            return "correct"
        if parse:
            return "parse_error"
        if got == "plan" or (got == "error" and model_tools):
            return "acted_instead"
        if want == "clarify" and got == "reject":
            return "refused_instead"
        if want == "reject" and got == "clarify":
            return "asked_instead"
        return "other"

    tools_match = model_tools == expected
    if row["outcome_correct"]:
        score = row.get("knaif_score")
        if tools_match:
            return "arg_error" if score is not None and score < 1 else "correct"
        # Other tools, graded in full: a chain the corpus names by its first tool only, or an
        # equivalent tool. The grade decides, not the tool names (see `accepted_other_tools`).
        if score is not None and score >= 1:
            return "correct"
    # Retrieval is to blame only for an unshown tool the model then left out: a tool it planned
    # anyway, unshown, means what failed came after retrieval.
    if shown is not None and any(t not in shown and t not in model_tools for t in expected):
        return "retriever_miss"
    if parse:
        return "parse_error"
    if got in ("clarify", "reject"):
        if model_tools:
            return "gate_asked"
        if _INVENTED_FILE.match(question):
            return "gate_invented_file"
        return "model_asked" if got == "clarify" else "model_refused"
    if not tools_match:
        return "wrong_tool"
    if got == "error":
        return "execution_failed"
    return "other"


def _all_shown(expected: list[str], shown: list[str] | None) -> bool | None:
    if shown is None or not expected:
        return None
    return all(t in shown for t in expected)


def split(
    board: dict[str, Any],
    corpus: dict[str, dict[str, Any]],
    shown: dict[str, list[str]] | None,
    *,
    shown_now: dict[str, list[str]] | None = None,
    compare: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bucket every row of *board*; *corpus* maps row id to its corpus row.

    *shown* (as served) and *shown_now* (today's retrieval) map `<id>#<utterance_idx>` to the
    model-visible tools. *compare* is another model's scoreboard on the same corpus, bucketed
    with the same shown tools and paired with this one row by row.
    """
    from .stats import mcnemar_p, paired_flips

    shown = shown or None

    def bucket_rows(b: dict[str, Any]) -> dict[str, tuple[dict[str, Any], str]]:
        out = {}
        for r in b["rows"]:
            if r["id"] not in corpus:
                raise KeyError(f"{r['id']} is in the scoreboard but not in the corpus")
            exp = _expected_tools(corpus[r["id"]])
            sh = shown[_key(r)] if shown is not None else None
            out[_key(r)] = (r, classify(r, exp, sh))
        return out

    mine = bucket_rows(board)
    buckets: Counter[str] = Counter()
    by_language: dict[str, Counter[str]] = defaultdict(Counter)
    by_tag: dict[str, Counter[str]] = defaultdict(Counter)
    rows_out: list[dict[str, Any]] = []
    missed_served: list[str] = []
    missed_now: list[str] = []
    right_unshown: list[str] = []
    accepted_other: list[dict[str, Any]] = []
    unshown_planned: Counter[str] = Counter()
    language_totals: Counter[str] = Counter()
    n_plan = 0

    for key, (r, b) in mine.items():
        c = corpus[r["id"]]
        exp = _expected_tools(c)
        tags = list(c.get("tags") or r.get("tags") or [])
        lang = language(tags, r.get("utterance", ""))
        buckets[b] += 1
        language_totals[lang] += 1
        if r["expected_outcome"] == "plan" and exp and shown is not None:
            n_plan += 1
            if not _all_shown(exp, shown[key]):
                missed_served.append(key)
                # The model named the right tools anyway (it knows them from training).
                if b in ("correct", "arg_error"):
                    right_unshown.append(key)
            if shown_now is not None and not _all_shown(exp, shown_now[key]):
                missed_now.append(key)
        if b == "correct":
            got = [s.get("tool") for s in _steps(r) if s.get("tool") not in _CORE]
            if r["expected_outcome"] == "plan" and exp and got != exp:
                accepted_other.append({"key": key, "expected": exp, "got": got})
            continue
        by_language[lang][b] += 1
        planned = [st.get("tool") for st in _steps(r) if st.get("tool") not in _CORE]
        # Tools the model planned without the prompt showing them: it knows their names from
        # training, not, unshown, their arguments.
        unshown = (
            [t for t in exp if t in planned and t not in shown[key]] if shown is not None else []
        )
        if unshown:
            unshown_planned[b] += 1
        for t in tags:
            by_tag[t][b] += 1
        rows_out.append(
            {
                "key": key,
                "bucket": b,
                "language": lang,
                "tags": tags,
                "utterance": r.get("utterance", ""),
                "expected_outcome": r["expected_outcome"],
                "actual_outcome": r["actual_outcome"],
                "expected_tools": exp,
                "model_tools": [s.get("tool") for s in _steps(r) if s.get("tool") not in _CORE],
                "knaif_score": r.get("knaif_score"),
                "shown": shown[key] if shown is not None else None,
                "unshown_planned": unshown,
                "shown_now": _all_shown(exp, shown_now[key]) if shown_now is not None else None,
            }
        )

    result: dict[str, Any] = {
        "total": len(mine),
        "buckets": dict(buckets.most_common()),
        "by_language": {k: dict(v.most_common()) for k, v in sorted(by_language.items())},
        "language_totals": dict(sorted(language_totals.items())),
        "by_tag": {k: dict(v.most_common()) for k, v in sorted(by_tag.items())},
        "rows": rows_out,
        "accepted_other_tools": accepted_other,
        "unshown_planned": dict(unshown_planned.most_common()),
    }
    if shown is not None:
        result["retrieval"] = {
            "n": n_plan,
            "missed_served": missed_served,
            "right_unshown": right_unshown,
            "missed_now": missed_now if shown_now is not None else None,
        }
    if compare is not None:
        theirs = bucket_rows(compare)
        flips = paired_flips(board, compare)
        transitions: Counter[str] = Counter(
            f"{theirs[k][1]} -> {mine[k][1]}"
            for k in mine.keys() & theirs.keys()
            if theirs[k][1] != mine[k][1]
        )
        result["paired"] = {
            "n": flips["n"],
            "only_this_right": flips["only_a"],
            "only_other_right": flips["only_b"],
            "mcnemar_p": mcnemar_p(flips["only_a"], flips["only_b"]),
            "other_buckets": dict(Counter(b for _, b in theirs.values()).most_common()),
            "transitions": dict(transitions.most_common()),
        }
    return result


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def format_report(result: dict[str, Any], *, title: str) -> str:
    """Markdown report of one `split` result."""
    total = result["total"]
    b = result["buckets"]
    wrong = total - b.get("correct", 0)
    lines = [f"# Error split: {title}", "", f"{total} rows, {wrong} not fully right.", ""]

    lines += ["## Buckets", ""]
    lines += _table(
        ["bucket", "rows", "share", "meaning"],
        [
            [name, b[name], f"{b[name] / total:.1%}", BUCKETS[name]]
            for name in BUCKETS
            if b.get(name)
        ],
    )

    wrong_buckets = [n for n in BUCKETS if n != "correct" and b.get(n)]
    if result["by_language"]:
        lines += ["", "## By language (rows not fully right)", ""]
        totals = result.get("language_totals", {})
        lines += _table(
            ["language", "rows", "not right", "rate", *wrong_buckets],
            [
                [
                    lang,
                    totals.get(lang, ""),
                    sum(counts.values()),
                    f"{sum(counts.values()) / totals[lang]:.1%}" if totals.get(lang) else "",
                    *(counts.get(n, "") for n in wrong_buckets),
                ]
                for lang, counts in result["by_language"].items()
            ],
        )
    if result["by_tag"]:
        lines += ["", "## By tag (rows not fully right)", ""]
        ranked = sorted(result["by_tag"].items(), key=lambda kv: -sum(kv[1].values()))
        lines += _table(
            ["tag", "rows", *wrong_buckets],
            [[tag, sum(c.values()), *(c.get(n, "") for n in wrong_buckets)] for tag, c in ranked],
        )

    r = result.get("retrieval")
    if r:
        lines += ["", "## Retrieval: every expected tool shown? (plan rows)", ""]
        lines.append(f"- as served: {r['n'] - len(r['missed_served'])}/{r['n']} shown")
        lines.append(
            f"- not shown but planned right anyway: {len(r.get('right_unshown', []))} "
            f"of {len(r['missed_served'])}"
        )
        if r["missed_now"] is not None:
            lines.append(f"- today: {r['n'] - len(r['missed_now'])}/{r['n']} shown")
            fixed = sorted(set(r["missed_served"]) - set(r["missed_now"]))
            broken = sorted(set(r["missed_now"]) - set(r["missed_served"]))
            lines.append(f"- shown today, missed as served: {len(fixed)} {fixed}")
            lines.append(f"- missed today, shown as served: {len(broken)} {broken}")
    up = result.get("unshown_planned") or {}
    if up:
        lines.append(
            f"- rows not fully right that planned a tool the prompt did not show (its arguments "
            f"unseen): {sum(up.values())}, by bucket {up}"
        )

    alt = result.get("accepted_other_tools") or []
    if alt:
        lines += ["", "## Graded right with other tools (not errors; corpus review candidates)", ""]
        lines.append(
            f"{len(alt)} rows planned other tools than the corpus row names, and the grader "
            "accepted them in full: usually a chain whose row names only its first tool."
        )
        lines += [""]
        lines += _table(
            ["row", "corpus names", "model planned"],
            [[x["key"], "+".join(x["expected"]), "+".join(x["got"])] for x in alt],
        )

    p = result.get("paired")
    if p:
        lines += ["", "## Against the other scoreboard (outcome, paired by row)", ""]
        lines.append(
            f"- n={p['n']}; only this one right: {p['only_this_right']}; "
            f"only the other right: {p['only_other_right']}; McNemar p={p['mcnemar_p']:.4g}"
        )
        if p["transitions"]:
            lines += ["", "Bucket changes (other -> this):", ""]
            lines += _table(["change", "rows"], [[k, v] for k, v in p["transitions"].items()])

    if result["rows"]:
        lines += ["", "## Rows", ""]
        lines += _table(
            ["row", "bucket", "lang", "expected", "got", "model tools", "utterance"],
            [
                [
                    x["key"],
                    x["bucket"],
                    x["language"],
                    "+".join(x["expected_tools"]) or x["expected_outcome"],
                    x["actual_outcome"],
                    "+".join(x["model_tools"]) or "-",
                    x["utterance"].replace("|", "\\|")[:80],
                ]
                for x in sorted(result["rows"], key=lambda x: (x["bucket"], x["key"]))
            ],
        )
    return "\n".join(lines) + "\n"


def load_corpus_rows(path: Path) -> dict[str, dict[str, Any]]:
    """The corpus as plain dicts by row id (no schema: older corpora load too)."""
    rows = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["id"]] = row
    return rows


def capture_shown(skill: str, corpus_path: Path) -> dict[str, list[str]]:
    """The model-visible tools each utterance's prompt lists, as the eval runner builds them.

    Uses only `create_agent` and `retrieve_tools`, so it runs against an older `knaif` tree
    (load this file by path with that tree first on `sys.path`).
    """
    from knaif import create_agent
    from knaif.registry import retrieve_tools

    agent = create_agent(skill)
    out: dict[str, list[str]] = {}
    for row in load_corpus_rows(corpus_path).values():
        for i, utt in enumerate(row["utterances"]):
            subset = retrieve_tools(utt, agent.registry)
            out[f"{row['id']}#{i}"] = [
                name
                for name, td in subset.items()
                if not getattr(td, "internal", False) and name not in _CORE
            ]
    return out
