"""The model's plan survives core's rewrites, so a caller can show what the model said.

Found 2026-09-23: a fan-out plan (three trims of one file) was correct as emitted and scrambled
by `_forward_thread_reused_sources` before execution. The workbench showed only the rewritten
plan, so it read as a model failure until the same utterance ran on native, which has no
threader. See docs/plans/2026-09-23-chain-source-threading.md.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock

from knaif.agent import CommandAgent

FFMPEG_SKILL_DIR = Path(__file__).parents[2]

_UTTERANCE = (
    "remove the audio from clip.mov as silent.mp4. From silent.mp4 make three pieces: trim "
    "silent.mp4 0-2s as part1.mp4, trim silent.mp4 2-4s as part2.mp4, trim silent.mp4 from 4s to "
    "the end as part3.mp4. Then reverse part2.mp4 as part2_rev.mp4. Finally join part1.mp4, "
    "part2_rev.mp4 and part3.mp4 into result.mp4"
)

# What the 4B emitted, as measured on the native runtime (which does not rewrite it).
_EMITTED = {
    "plan": [
        {"tool": "strip_audio", "args": {"inputs": ["clip.mov"], "output": "silent.mp4"}},
        {
            "tool": "trim_video",
            "args": {"input": "silent.mp4", "start": "0", "end": "2", "output": "part1.mp4"},
        },
        {
            "tool": "trim_video",
            "args": {"input": "silent.mp4", "start": "2", "end": "4", "output": "part2.mp4"},
        },
        {
            "tool": "trim_video",
            "args": {"input": "silent.mp4", "start": "4", "output": "part3.mp4"},
        },
        {"tool": "reverse_video", "args": {"inputs": ["part2.mp4"], "output": "part2_rev.mp4"}},
        {
            "tool": "concat_video",
            "args": {
                "inputs": ["part1.mp4", "part2_rev.mp4", "part3.mp4"],
                "output": "result.mp4",
            },
        },
    ]
}


def test_the_emitted_fan_out_is_kept_whatever_core_does_to_the_payload(tmp_path):
    (tmp_path / "clip.mov").write_bytes(b"")
    orchestrator = MagicMock()
    orchestrator.infer.return_value = json.dumps(_EMITTED)
    agent = CommandAgent.from_skill(
        FFMPEG_SKILL_DIR, sandbox=tmp_path, root=tmp_path, orchestrator=orchestrator
    )

    agent.infer(_UTTERANCE, use_mock=False)

    # Deliberately silent on the returned payload: the threading fix will change it, and this
    # test must keep holding when it does.
    assert agent.last_model_plan == copy.deepcopy(_EMITTED)
