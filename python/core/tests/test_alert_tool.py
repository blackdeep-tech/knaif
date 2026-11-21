"""The `alert_when_done` core tool: a run option the model asks for, never a step that runs.

A request such as "convert clip.mov to mp4 and beep when it's done" puts `alert_when_done` in the
plan. The pipeline takes it out before anything executes and keeps it as the run's alert, which
fires when the run ends (success, failure, or a confirmation declined mid-run). An alert is a
desktop notification plus a beep; there are no kinds (owner, 2026-10-12, after the slice-1
probes: the kind was the main failure and bought little).

Two promises keep every other request's behaviour unchanged:
- the tool enters a prompt only when one of its trigger words or phrases is in the request, so
  a request without them gets the same tools, in the same order, as before;
- an example that uses the tool is chosen only when the tool was retrieved, and an example that
  writes into a known folder only when the request names one.
"""

from __future__ import annotations

import json

import pytest

from knaif import create_agent
from knaif.core_tools import CORE_TOOL_DEFS, CORE_TOOL_MAP
from knaif.planner import alert_last, split_alert
from knaif.prompt import select_examples
from knaif.registry import retrieve_tools

ALERT = "alert_when_done"


@pytest.fixture(scope="module")
def ffmpeg():
    return create_agent("ffmpeg")


@pytest.fixture(scope="module")
def documents():
    return create_agent("documents")


# ── the contract ─────────────────────────────────────────────────────────────


def test_alert_tool_is_a_core_tool():
    assert ALERT in CORE_TOOL_DEFS
    assert ALERT in CORE_TOOL_MAP


def test_alert_tool_takes_no_args():
    td = CORE_TOOL_DEFS[ALERT]
    assert td.required_args == ()
    assert td.optional_args == ()
    assert td.arg_schemas == {}
    assert td.defaults == {}
    assert td.safety_category == "safe"
    assert td.internal is False


def test_alert_tool_is_retrieved_by_triggers_not_keywords():
    """Keywords would make the tool compete in the ranking, and so enter unrelated prompts."""
    td = CORE_TOOL_DEFS[ALERT]
    assert td.keywords == ()
    assert "beep" in td.triggers
    assert "let me know" in td.triggers


def test_no_other_core_tool_has_triggers():
    for name, td in CORE_TOOL_DEFS.items():
        if name != ALERT:
            assert td.triggers == (), name


# ── retrieval ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "utterance",
    [
        "convert clip.mov to mp4 and beep when it's done",
        "compress clip.mp4, let me know when it's done",
        "trim clip.mp4 to 10 seconds and notify me",
        "convert clip.mov to mp4, tell me when it finishes",
        "Merge a.pdf and b.pdf and BEEP when finished",
    ],
)
def test_alert_is_retrieved_when_a_trigger_is_in_the_request(ffmpeg, utterance):
    assert ALERT in retrieve_tools(utterance, ffmpeg.registry)


@pytest.mark.parametrize(
    "utterance",
    [
        "convert clip.mov to mp4",
        "remove sound from clip_no_audio.mp4",
        "Empty the recycle bin and the sandbox folder when you're done.",
        "I need to know whether sample.pdf mentions Delta at all",
        "tell me about the pdf at https://files.knaif.test/documents/report.pdf",
        "beeper.mp4 to gif",
    ],
)
def test_alert_is_not_retrieved_without_a_trigger(ffmpeg, utterance):
    assert ALERT not in retrieve_tools(utterance, ffmpeg.registry)


@pytest.mark.parametrize(
    "utterance",
    [
        "convert clip.mov to mp4",
        "make it smaller",
        "beep",
        "convert clip.mov to mp4 and beep when it's done",
    ],
)
def test_the_alert_never_changes_the_other_retrieved_tools(ffmpeg, utterance):
    """Same tools, same order, with or without the alert tool in the registry."""
    without = {n: t for n, t in ffmpeg.registry.items() if n != ALERT}
    with_alert = retrieve_tools(utterance, ffmpeg.registry)
    assert [n for n in with_alert if n != ALERT] == list(retrieve_tools(utterance, without))


def _corpus_utterances():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "skills"
    for skill in ("ffmpeg", "documents"):
        for path in sorted((root / skill / "data").glob("*.jsonl")):
            if path.name.startswith("train"):
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    # The rows written to test the alert and known folders (eval.jsonl since
                    # 2026-10-28; they were eval_alert.jsonl) are the ones that may.
                    if "alerts_and_folders" in (row.get("tags") or []):
                        continue
                    for u in row.get("utterances") or [row.get("utterance")]:
                        if u:
                            yield f"{path.name}:{row.get('id')}", u


def test_no_utterance_of_the_existing_corpora_triggers_the_alert():
    """The eval and safety corpora predate the alert: none of them may change prompt."""
    from knaif.registry import triggered

    hits = [
        (where, u)
        for where, u in _corpus_utterances()
        if triggered(u, CORE_TOOL_DEFS[ALERT].triggers)
    ]
    assert hits == []


def test_no_utterance_of_the_existing_corpora_names_a_known_folder():
    """Same promise for the known-folder examples' gate."""
    from knaif.known_folders import names_known_folder

    assert [(w, u) for w, u in _corpus_utterances() if names_known_folder(u)] == []


def test_alert_comes_after_ranked_tools_and_before_the_control_tools(ffmpeg):
    names = list(retrieve_tools("convert clip.mov to mp4 and beep", ffmpeg.registry))
    assert names[-4:] == [ALERT, "clarify", "reject", "done"]


# ── prompt ───────────────────────────────────────────────────────────────────


def test_alert_is_listed_in_the_prompt_when_retrieved(ffmpeg):
    utterance = "convert clip.mov to mp4 and beep when it's done"
    system, _ = ffmpeg.build_prompt(
        utterance, registry_override=retrieve_tools(utterance, ffmpeg.registry)
    )
    tools_block = system.split("Available tools:")[1].split("Examples:")[0]
    assert f"- {ALERT}:" in tools_block


def _alert_example():
    return {
        "request": "convert clip.mov to mp4 and beep when it's done",
        "output": {
            "plan": [
                {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "container": "mp4"}},
                {"tool": ALERT, "args": {}},
            ]
        },
    }


def _folder_example():
    return {
        "request": "Resize clip.mp4 to 720p and save it in my Videos folder.",
        "output": {
            "plan": [
                {
                    "tool": "resize_video",
                    "args": {"inputs": ["clip.mp4"], "output": "Videos/clip_720p.mp4"},
                }
            ]
        },
    }


def _plain_example():
    return {
        "request": "convert clip.mov to webm",
        "output": {
            "plan": [
                {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "container": "webm"}}
            ]
        },
    }


def test_an_alert_example_is_not_chosen_unless_the_alert_was_retrieved():
    examples = [_plain_example(), _alert_example()]
    chosen = select_examples(examples, frozenset({"convert_video"}), "convert clip.mov to mp4")
    assert _alert_example() not in chosen


def test_an_alert_example_is_chosen_when_the_alert_was_retrieved():
    examples = [_plain_example(), _alert_example()]
    chosen = select_examples(
        examples, frozenset({"convert_video", ALERT}), "convert clip.mov to mp4 and beep"
    )
    assert _alert_example() in chosen


def test_a_folder_example_is_not_chosen_unless_the_request_names_a_known_folder():
    examples = [_plain_example(), _folder_example()]
    chosen = select_examples(examples, frozenset({"resize_video"}), "resize clip.mp4 to 720p")
    assert _folder_example() not in chosen


def test_a_folder_example_is_chosen_when_the_request_names_a_known_folder():
    examples = [_plain_example(), _folder_example()]
    chosen = select_examples(
        examples, frozenset({"resize_video"}), "resize clip.mp4 to 1080p into my Downloads folder"
    )
    assert _folder_example() in chosen


@pytest.mark.parametrize(
    "text",
    [
        "save it in Downloads",
        "put it on the Desktop",
        "extract the audio as mp3 into my Music folder",
        "convert clip.mov to mp4, output to Documents",
        "shrink report.pdf onto my desktop",
        "put an mp4 version of clip.mov in my Downloads folder",
    ],
)
def test_names_known_folder(text):
    from knaif.known_folders import names_known_folder

    assert names_known_folder(text)


@pytest.mark.parametrize(
    "text",
    [
        "merge these documents",
        "convert the documents in this folder",
        "compress my music.mp3",
        "downloads.mp4 to gif",
        "make the desktop recording smaller",
        # Where files are, not where they go (a safety contract case found it).
        "delete everything in my videos folder",
        "convert all the clips in my Videos folder to mp4",
        "make a webm of clip.mp4 in my Downloads folder",
    ],
)
def test_does_not_name_known_folder(text):
    from knaif.known_folders import names_known_folder

    assert not names_known_folder(text)


# ── split_alert: the plan the pipeline runs, and whether the run alerts ──────


def _step(tool, **args):
    return {"tool": tool, "args": args}


def test_split_removes_the_alert():
    payload = {"plan": [_step("convert_video", inputs=["a.mov"]), _step(ALERT)]}
    plan, alert = split_alert(payload)
    assert plan == {"plan": [_step("convert_video", inputs=["a.mov"])]}
    assert alert is True


def test_split_ignores_any_args_the_model_gives_the_alert():
    """The tool takes none; a model may still write one (a probe plan asked for a `kind`)."""
    payload = {"plan": [_step("convert_video", inputs=["a.mov"]), _step(ALERT, kind="beep")]}
    plan, alert = split_alert(payload)
    assert [s["tool"] for s in plan["plan"]] == ["convert_video"]
    assert alert is True


def test_split_leaves_the_payload_it_was_given_unchanged():
    payload = {"plan": [_step(ALERT), _step("convert_video", inputs=["a.mov"])]}
    before = repr(payload)
    split_alert(payload)
    assert repr(payload) == before


def test_split_accepts_the_alert_in_any_position():
    payload = {"plan": [_step(ALERT), _step("convert_video", inputs=["a.mov"])]}
    plan, alert = split_alert(payload)
    assert [s["tool"] for s in plan["plan"]] == ["convert_video"]
    assert alert is True


def test_split_without_an_alert():
    payload = {"plan": [_step("convert_video", inputs=["a.mov"])]}
    plan, alert = split_alert(payload)
    assert plan == payload
    assert alert is False


def test_two_alerts_are_one():
    payload = {"plan": [_step("x"), _step(ALERT), _step(ALERT)]}
    plan, alert = split_alert(payload)
    assert [s["tool"] for s in plan["plan"]] == ["x"]
    assert alert is True


@pytest.mark.parametrize("terminal", ["clarify", "reject"])
def test_no_alert_on_a_plan_that_only_clarifies_or_rejects(terminal):
    """Nothing ran, and the user is reading the answer already (owner, 2026-10-12)."""
    arg = {"clarify": {"question": "Which file?"}, "reject": {"reason": "Unsafe."}}[terminal]
    payload = {"plan": [{"tool": terminal, "args": arg}, _step(ALERT)]}
    plan, alert = split_alert(payload)
    assert [s["tool"] for s in plan["plan"]] == [terminal]
    assert alert is False


def test_an_alert_with_no_work_becomes_a_clarify():
    plan, alert = split_alert({"plan": [_step(ALERT)]})
    assert alert is False
    assert [s["tool"] for s in plan["plan"]] == ["clarify"]
    assert "alert" in plan["plan"][0]["args"]["question"].lower()


def test_an_alert_beside_done_only_is_no_work_either():
    plan, alert = split_alert({"plan": [_step(ALERT), _step("done")]})
    assert alert is False
    assert plan["plan"][0]["tool"] == "clarify"


def test_split_ignores_a_payload_without_a_plan():
    assert split_alert({}) == ({}, False)


def test_alert_last_moves_alert_steps_to_the_end_in_place():
    """Chain linking joins each step's output to the next step's input; an alert between two
    steps would break that, so infer() moves it out of the way first."""
    plan = [_step("trim_video", inputs=["a.mp4"]), _step(ALERT), _step("convert_video")]
    payload = {"plan": plan}
    alert_last(payload)
    assert [s["tool"] for s in payload["plan"]] == ["trim_video", "convert_video", ALERT]


def test_alert_last_keeps_a_plan_without_an_alert():
    payload = {"plan": [_step("trim_video"), _step("convert_video")]}
    alert_last(payload)
    assert [s["tool"] for s in payload["plan"]] == ["trim_video", "convert_video"]


# ── validation ───────────────────────────────────────────────────────────────


def test_validate_accepts_an_alert_step(ffmpeg):
    payload = {"plan": [_step("convert_video", inputs=["a.mov"], container="mp4"), _step(ALERT)]}
    ffmpeg.validate_plan(payload)


def test_an_alert_with_args_does_not_send_the_model_back(ffmpeg):
    """infer's validator retry checks the plan the pipeline will run: the alert is not in it."""
    raw = json.dumps(
        {
            "plan": [
                {"tool": "convert_video", "args": {"inputs": ["a.mov"], "container": "mp4"}},
                {"tool": ALERT, "args": {"kind": "beep"}},
            ]
        }
    )
    _, kind, err = ffmpeg._parse_and_check(raw)
    assert (kind, err) == (None, None)


def test_documents_gets_the_alert_tool_too(documents):
    assert ALERT in documents.registry


# ── the pipeline takes it out before execution ───────────────────────────────


def test_execute_plan_never_runs_the_alert_and_records_it(ffmpeg, tmp_path):
    clip = tmp_path / "a.mov"
    clip.write_bytes(b"")
    payload = {"plan": [_step(ALERT), _step("convert_video", inputs=[str(clip)])]}
    results = ffmpeg.execute_plan(payload, dry_run=True)
    assert ALERT not in [r["tool"] for r in results]
    assert ffmpeg.last_alert is True


def test_execute_plan_without_an_alert_records_none(ffmpeg, tmp_path):
    clip = tmp_path / "a.mov"
    clip.write_bytes(b"")
    ffmpeg.execute_plan({"plan": [_step("convert_video", inputs=[str(clip)])]}, dry_run=True)
    assert ffmpeg.last_alert is False
