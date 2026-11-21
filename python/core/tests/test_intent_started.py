"""The run's shape for a CLI's display: `plan_ready(steps, notes)` once before
anything runs, then `intent_started(index, total, step)` before each step, as native's views
draw them (`Plan · N steps`, `Step i/N · tool`, `step i of N:`;
contracts/parity/cli_output_cases.json).
"""

from __future__ import annotations

from knaif import create_agent

CHAIN = {
    "plan": [
        {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}},
        {
            "tool": "resize_video",
            "args": {"inputs": ["silent.mp4"], "height": 720, "output": "s.mp4"},
        },
    ]
}

BATCH = {
    "plan": [
        {
            "tool": "convert_video",
            "args": {"inputs": ["*.mov"], "container": "mp4", "output": "c.mp4"},
        },
        {"tool": "resize_video", "args": {"inputs": ["c.mp4"], "height": 480}},
    ]
}


def _agent(tmp_path, events: list):
    agent = create_agent("ffmpeg", sandbox=tmp_path)
    agent.root = tmp_path
    agent.plan_ready = lambda steps, notes: events.append(
        ("plan", [s["tool"] for s in steps], notes)
    )
    agent.intent_started = lambda index, total, step: events.append(
        ("start", index, total, step["tool"])
    )
    agent.intent_completed = lambda results, dry_run: events.append(("done",))
    return agent


def test_each_intent_is_announced_before_it_runs(tmp_path) -> None:
    events: list[tuple] = []
    _agent(tmp_path, events).execute_plan(
        CHAIN, utterance="strip audio from clip.mp4 and resize it", dry_run=True
    )
    assert events == [
        ("plan", ["strip_audio", "resize_video"], ["leaves s.mp4"]),
        ("start", 1, 2, "strip_audio"),
        ("done",),
        ("start", 2, 2, "resize_video"),
        ("done",),
    ]


def test_a_batch_previews_its_first_file_and_announces_every_step(tmp_path) -> None:
    for name in ("beach.mov", "city.mov"):
        (tmp_path / name).write_bytes(b"")
    events: list[tuple] = []
    _agent(tmp_path, events).execute_plan(
        BATCH, utterance="convert all the mov files to mp4 and make them 480p", dry_run=True
    )
    assert events[0] == (
        "plan",
        ["convert_video", "resize_video"],
        ["the same for 1 more file: city.mov", "leaves 2 mp4"],
    )
    assert [e[1:] for e in events if e[0] == "start"] == [
        (1, 4, "convert_video"),
        (2, 4, "resize_video"),
        (3, 4, "convert_video"),
        (4, 4, "resize_video"),
    ]
