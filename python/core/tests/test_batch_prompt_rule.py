"""The per-file batch rule joins the prompt only when the request asks for many files.

(owner, 2026-10-14: the per-file form, measured by a probe; 2026-10-20: in the prompt under
the clarify gate's own test). The rule (`rules:` with `when: many` in prompt.yaml)
and its examples (`when: many`) reach a request with a batch word, a plural file word or a glob,
and nothing else, so a single-file prompt stays byte-identical.
"""

from __future__ import annotations

import pytest

from knaif import create_agent
from knaif.registry import retrieve_tools

MARK = {"ffmpeg": "MANY FILES, SEVERAL STEPS", "documents": "MANY FILES:"}
EXAMPLE = {
    "ffmpeg": "Rotate every mkv in this folder 90 degrees and make them 1080p.",
    "documents": "Rotate all PDFs here by 90 degrees and add a CONFIDENTIAL watermark.",
}


def _prompt(skill: str, utterance: str) -> str:
    agent = create_agent(skill)
    system, _ = agent.build_prompt(
        utterance, registry_override=retrieve_tools(utterance, agent.registry)
    )
    return system


@pytest.mark.parametrize(
    ("skill", "utterance"),
    [
        ("ffmpeg", "convert all the .mov files here to mp4 and make them smaller"),
        ("ffmpeg", "rotate *.mkv by 90 degrees"),
        ("ffmpeg", "把这个文件夹里的每个视频缩放到720p并去掉声音"),
        ("documents", "compress every pdf here and add page numbers"),
    ],
)
def test_a_request_for_many_files_gets_the_rule(skill: str, utterance: str) -> None:
    assert MARK[skill] in _prompt(skill, utterance)


@pytest.mark.parametrize(
    ("skill", "utterance"),
    [
        ("ffmpeg", "convert clip.mov to mp4 and make it smaller"),
        ("ffmpeg", "rotate clip.mp4 90 degrees and make it 1080p"),
        ("documents", "compress report.pdf and add page numbers"),
    ],
)
def test_a_one_file_request_gets_neither_the_rule_nor_its_examples(
    skill: str, utterance: str
) -> None:
    prompt = _prompt(skill, utterance)
    assert MARK[skill] not in prompt
    assert EXAMPLE[skill] not in prompt


def test_a_batch_example_reaches_a_request_for_many_files() -> None:
    prompt = _prompt("ffmpeg", "rotate every video in this folder 90 degrees and make them 1080p")
    assert EXAMPLE["ffmpeg"] in prompt
