"""Chain intermediates are routed into the run scratch, so only deliverables reach the folder.

An intermediate is a step's ``output`` that a later step reads. It moves into ``work_dir``
under the same name (so every argv still compares equal by basename), and every later reference
moves with it. Two things stay where the plan put them: an output nothing reads (a deliverable),
and a file the user named in the request (a file the user named is one they asked for).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent

route = CommandAgent._route_intermediates_to_scratch


def _trim_then_resize(output: str = "clip_trimmed.mp4", ref: str = "clip_trimmed.mp4") -> list:
    return [
        {"tool": "trim_video", "args": {"input": "clip.mp4", "end": "00:00:02", "output": output}},
        {"tool": "resize_video", "args": {"inputs": [ref], "height": 240}},
    ]


def test_an_intermediate_moves_into_scratch_with_its_readers(tmp_path: Path) -> None:
    work, sb = tmp_path / "work", tmp_path / "sb"
    plan = route(_trim_then_resize(), work, "trim clip.mp4 to 2 s, then resize it", sb)
    moved = str(work / "clip_trimmed.mp4")
    assert plan[0]["args"]["output"] == moved
    assert plan[1]["args"]["inputs"] == [moved]
    assert "output" not in plan[1]["args"], "the deliverable is untouched"


def test_the_input_plan_is_not_mutated(tmp_path: Path) -> None:
    original = _trim_then_resize()
    route(original, tmp_path / "work", "trim clip.mp4, then resize it", tmp_path / "sb")
    assert original == _trim_then_resize()


def test_a_file_the_user_named_stays_put(tmp_path: Path) -> None:
    plan = _trim_then_resize("short.mp4", "short.mp4")
    got = route(
        plan, tmp_path / "w", "trim clip.mp4 into short.mp4, then resize short.mp4", tmp_path
    )
    assert got == plan


def test_an_output_nothing_reads_is_a_deliverable(tmp_path: Path) -> None:
    plan = [
        {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"], "output": "thumb.jpg"}},
        {"tool": "compress_video", "args": {"inputs": ["clip.mp4"], "output": "small.mp4"}},
    ]
    assert route(plan, tmp_path / "w", "thumbnail and compress clip.mp4", tmp_path) == plan


def test_every_reader_of_a_fan_out_follows(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "clip_c.mp4"}},
        {"tool": "create_thumbnail", "args": {"inputs": ["clip_c.mp4"]}},
        {"tool": "compress_video", "args": {"inputs": ["clip_c.mp4"]}},
    ]
    got = route(plan, work, "convert clip.mov to mp4, then thumbnail and compress it", tmp_path)
    moved = str(work / "clip_c.mp4")
    assert got[0]["args"]["output"] == moved
    assert got[1]["args"]["inputs"] == [moved] and got[2]["args"]["inputs"] == [moved]


def test_spellings_of_the_same_file_match(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = _trim_then_resize("clip_trimmed.mp4", "./clip_trimmed.mp4")
    got = route(plan, work, "trim clip.mp4, then resize it", tmp_path)
    assert got[1]["args"]["inputs"] == [str(work / "clip_trimmed.mp4")]


def test_a_single_input_arg_is_rewritten_too(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "clip.mp4"}},
        {"tool": "trim_video", "args": {"input": "clip.mp4", "end": "00:00:03"}},
    ]
    got = route(plan, work, "convert clip.mov to mp4 and then trim it to 3 seconds", tmp_path)
    assert got[1]["args"]["input"] == str(work / "clip.mp4")


def test_two_intermediates_with_one_name_do_not_collide(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = [
        {"tool": "trim_video", "args": {"input": "a/clip.mp4", "output": "a/x.mp4"}},
        {"tool": "trim_video", "args": {"input": "b/clip.mp4", "output": "b/x.mp4"}},
        {"tool": "concat_video", "args": {"inputs": ["a/x.mp4", "b/x.mp4"], "output": "j.mp4"}},
    ]
    got = route(plan, work, "trim both clips and join them into j.mp4", tmp_path)
    first, second = got[0]["args"]["output"], got[1]["args"]["output"]
    assert first != second
    assert Path(first).name == Path(second).name == "x.mp4"
    assert got[2]["args"]["inputs"] == [first, second]


def test_without_an_utterance_every_name_is_the_callers(tmp_path: Path) -> None:
    plan = _trim_then_resize()
    assert route(plan, tmp_path / "w", None, tmp_path) == plan


def test_variable_references_and_terminals_are_ignored(tmp_path: Path) -> None:
    plan = [
        {"tool": "trim_video", "args": {"input": "clip.mp4", "output": "$out"}},
        {"tool": "resize_video", "args": {"inputs": ["$out"]}},
        {"tool": "done", "args": {}},
    ]
    assert route(plan, tmp_path / "w", "trim then resize clip.mp4", tmp_path) == plan


# ── the agent applies it only for a skill that opts in ─────────────────────────────────────

_CODE = """
from knaif.skill_base import Skill
from knaif.tool import Handler

SEEN = []


class Make(Handler):
    name = "make"

    def handle(self, args, ctx):
        SEEN.append(dict(args, work_dir=ctx.work_dir))
        return {"ok": True}
"""

_TOOLS = 'make:\n  effects: []\n  description: "Make a file."\n  optional_args: [src, output]\n  safety_category: safe\n'


def _agent(tmp_path: Path, opt_in: bool) -> tuple[CommandAgent, list]:
    import sys
    import textwrap

    skill_dir = tmp_path / "repo" / "skills" / "routed"
    (skill_dir / "python").mkdir(parents=True)
    # The opt-in is declarative, in skill.yaml, so both runtimes read the same contract.
    manifest = 'name: routed\ndescription: "Demo."\n'
    if opt_in:
        manifest += "scratch_intermediates: true\n"
    (skill_dir / "skill.yaml").write_text(manifest, encoding="utf-8")
    (skill_dir / "tools.yaml").write_text(_TOOLS, encoding="utf-8")
    (skill_dir / "python" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "python" / "handlers.py").write_text(textwrap.dedent(_CODE), encoding="utf-8")
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(skill_dir, sandbox=sandbox)
    return agent, sys.modules[type(agent.tool_map["make"]).__module__].SEEN


_CHAIN = {
    "plan": [
        {"tool": "make", "args": {"src": "a.txt", "output": "mid.txt"}},
        {"tool": "make", "args": {"src": "mid.txt", "output": "final.txt"}},
    ]
}


def test_a_skill_that_opts_in_gets_its_intermediates_routed(tmp_path: Path) -> None:
    import copy

    agent, seen = _agent(tmp_path, opt_in=True)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance="make a.txt twice", dry_run=True)
    work = Path(seen[0]["work_dir"])
    assert seen[0]["output"] == str(work / "mid.txt")
    assert seen[1]["src"] == str(work / "mid.txt")
    assert seen[1]["output"] == "final.txt"


def test_a_skill_that_does_not_opt_in_is_untouched(tmp_path: Path) -> None:
    import copy

    agent, seen = _agent(tmp_path, opt_in=False)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance="make a.txt twice", dry_run=True)
    assert seen[0]["output"] == "mid.txt" and seen[1]["src"] == "mid.txt"


def test_the_model_cannot_name_the_scratch_folder(tmp_path: Path, monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("KNAIF_SCRATCH_ROOT", str(tmp_path / "scratch"))
    agent, _ = _agent(tmp_path, opt_in=True)
    sneaky = str(tmp_path / "scratch" / "run-1-abc" / "secret.txt")
    plan = {"plan": [{"tool": "make", "args": {"src": sneaky, "output": "x.txt"}}]}
    with pytest.raises(ValueError, match="outside sandbox"):
        agent.execute_plan(plan, utterance="make x.txt", dry_run=False, confirmed=True)


def test_an_intermediate_keeps_its_folder_and_spelling(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = _trim_then_resize("videos/Clip_Trimmed.MP4", "videos/Clip_Trimmed.MP4")
    got = route(plan, work, "trim clip.mp4, then resize it", tmp_path)
    assert got[0]["args"]["output"] == str(work / "videos" / "Clip_Trimmed.MP4")


def test_the_class_attribute_opt_in_is_gone() -> None:
    """One contract both runtimes read: skill.yaml. No second, Python-only spelling."""
    from knaif.skill_base import Skill

    assert not hasattr(Skill, "scratch_intermediates")


def test_ffmpeg_opts_in_through_its_manifest() -> None:
    import yaml

    manifest = yaml.safe_load(Path("skills/ffmpeg/skill.yaml").read_text(encoding="utf-8"))
    assert manifest.get("scratch_intermediates") is True


def test_concat_declares_that_it_joins_its_inputs() -> None:
    """concat writes ONE file from several inputs, so its output is a routable intermediate
    (ffmpeg_243#0); a batch tool writes one file per input and is not."""
    from knaif.registry import load_registry

    registry = load_registry(Path("skills/ffmpeg/tools.yaml"))
    assert registry["concat_video"].joins_inputs is True
    assert registry["extract_audio"].joins_inputs is False


# ── the keep rule: what a step writes, declared per tool (`produces:`) ────────────────────────


def test_a_tool_declares_the_kind_it_writes() -> None:
    """A step that names no output still writes a known kind: core reads it from tools.yaml,
    never from a tool name, so the keep rule works for any skill."""
    from knaif.registry import load_registry

    ffmpeg = load_registry(Path("skills/ffmpeg/tools.yaml"))
    assert ffmpeg["extract_audio"].produces == "audio"
    assert ffmpeg["create_thumbnail"].produces == "image"
    assert ffmpeg["compress_video"].produces is None  # same kind as its input
    documents = load_registry(Path("skills/documents/tools.yaml"))
    assert documents["extract_text"].produces == "text"
    assert documents["convert_document"].produces == {"arg": "to_format"}


def test_a_produces_declaration_must_be_a_kind_or_an_arg(tmp_path: Path) -> None:
    from knaif.registry import load_registry

    bad = tmp_path / "tools.yaml"
    bad.write_text(
        "t:\n  description: x\n  effects: []\n  produces: {argument: fmt}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_registry(bad)


def test_documents_tells_text_from_documents() -> None:
    """ "Compress all the PDFs and extract their text" keeps both (owner, 2026-10-13): a .txt
    must be another kind than a .pdf."""
    import yaml

    kinds = yaml.safe_load(Path("skills/documents/skill.yaml").read_text(encoding="utf-8"))[
        "file_kinds"
    ]
    assert "pdf" in kinds["document"]
    assert "txt" in kinds["text"] and "md" in kinds["text"]


def test_a_tools_declared_kind_beats_the_extension_the_model_wrote() -> None:
    """A fixed `produces:` is the skill's word; the output extension is the model's. When they
    disagree (extract_audio writing `clip_audio.mp4`), the declaration decides what the step
    writes (owner, 2026-10-15), so a wrong extension cannot delete a second result."""
    from knaif.agent import _written_kind

    kinds = {"mp4": "video", "mp3": "audio", "txt": "text", "pdf": "document"}
    produces = {"extract_audio": "audio", "convert_document": {"arg": "to_format"}}
    wrong = {"tool": "extract_audio", "args": {"inputs": ["clip.mp4"], "output": "clip_audio.mp4"}}
    assert _written_kind(wrong, kinds, produces) == "audio"
    by_arg = {
        "tool": "convert_document",
        "args": {"input": "r.pdf", "to_format": "txt", "output": "r.pdf"},
    }
    assert _written_kind(by_arg, kinds, produces) == "text"
    # A tool that declares nothing still follows the extension it writes.
    free = {"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "output": "clip.mp3"}}
    assert _written_kind(free, kinds, produces) == "audio"
    # An arg-declared tool with no usable value falls back to the extension.
    no_value = {"tool": "convert_document", "args": {"input": "r.pdf", "output": "r.txt"}}
    assert _written_kind(no_value, kinds, produces) == "text"


def test_the_agent_routes_with_the_skills_kinds(tmp_path: Path) -> None:
    """End to end through `CommandAgent`: convert then extract audio keeps the mp4 (a second
    result); convert then speed up moves it to the scratch (refined)."""
    from knaif.agent import CommandAgent

    agent = CommandAgent.from_skill("skills/ffmpeg", sandbox=tmp_path)
    produces = {n: td.produces for n, td in agent.registry.items() if td.produces is not None}
    keep = CommandAgent._route_intermediates_to_scratch(
        [
            {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "clip.mp4"}},
            {"tool": "extract_audio", "args": {"inputs": ["clip.mp4"]}},
        ],
        tmp_path / "work",
        "convert clip.mov to mp4 then extract its audio",
        tmp_path,
        file_kinds=agent.file_kinds,
        produces=produces,
    )
    assert keep[0]["args"]["output"] == "clip.mp4"
    assert agent.route_kinds()[:2] == (agent.file_kinds, produces)


# ── the keep rule: one rule, two deterministic signals (owner, 2026-10-15) ────────────────────

KINDS = {
    "mp4": "video",
    "mov": "video",
    "gif": "video",
    "mp3": "audio",
    "wav": "audio",
    "jpg": "image",
    "pdf": "document",
    "txt": "text",
}
PRODUCES = {"extract_audio": "audio", "convert_video": {"arg": "container"}}
DERIVES = {"trim_video", "split_pdf", "concat_video", "merge_pdfs"}


def _route(plan: list, utterance: str, tmp_path: Path) -> list:
    (tmp_path / "sandbox").mkdir(exist_ok=True)
    return route(
        plan,
        tmp_path / "work",
        utterance,
        tmp_path / "sandbox",
        {"concat_video", "merge_pdfs"},
        file_kinds=KINDS,
        produces=PRODUCES,
        derives=DERIVES,
    )


def test_a_format_the_user_named_is_a_result_they_want(tmp_path: Path) -> None:
    """ "convert clip.mov to mp4 and make a gif preview": a format the user bothered to name is a
    result (owner, 2026-10-15); the gif does not write mp4 again, so the mp4 stays."""
    plan = [
        {
            "tool": "convert_video",
            "args": {"inputs": ["clip.mov"], "container": "mp4", "output": "clip.mp4"},
        },
        {
            "tool": "convert_video",
            "args": {"inputs": ["clip.mp4"], "container": "gif", "output": "clip.gif"},
        },
    ]
    routed = _route(plan, "convert clip.mov to mp4 and make a gif preview", tmp_path)
    assert routed[0]["args"]["output"] == "clip.mp4"
    assert routed[1]["args"]["inputs"] == ["clip.mp4"]


def test_a_named_format_written_again_is_refined(tmp_path: Path) -> None:
    """ "convert to mp4 and make it smaller": the smaller file is mp4 again, so the first mp4
    was a step on the way (owner, 2026-10-14)."""
    plan = [
        {
            "tool": "convert_video",
            "args": {"inputs": ["clip.mov"], "container": "mp4", "output": "clip.mp4"},
        },
        {"tool": "compress_video", "args": {"inputs": ["clip.mp4"], "output": "clip_small.mp4"}},
    ]
    routed = _route(plan, "convert clip.mov to mp4 and make it smaller", tmp_path)
    assert routed[0]["args"]["output"] != "clip.mp4"


def test_the_sources_own_extension_is_not_a_named_format(tmp_path: Path) -> None:
    """ "cut the mp4 at 3 seconds and make a gif": mp4 names the source, not a wanted result."""
    plan = [
        {
            "tool": "trim_video",
            "args": {"input": "clip.mp4", "end": "00:00:03", "output": "clip_part.mp4"},
        },
        {
            "tool": "convert_video",
            "args": {"inputs": ["clip_part.mp4"], "container": "gif", "output": "clip.gif"},
        },
    ]
    routed = _route(plan, "cut the mp4 at 3 seconds and make a gif of the first part", tmp_path)
    assert routed[0]["args"]["output"] != "clip_part.mp4"


def test_a_tool_that_derives_keeps_its_source(tmp_path: Path) -> None:
    """ "compress report.pdf and extract pages 1-3": a page range is a new thing made from the
    compressed file, not the compressed file improved, so both stay (owner, 2026-10-15)."""
    plan = [
        {"tool": "compress_pdf", "args": {"input": "report.pdf", "output": "report_small.pdf"}},
        {
            "tool": "split_pdf",
            "args": {"input": "report_small.pdf", "ranges": "1-3", "output": "report_1-3.pdf"},
        },
    ]
    routed = _route(plan, "compress report.pdf and extract pages 1-3", tmp_path)
    assert routed[0]["args"]["output"] == "report_small.pdf"


def test_pieces_made_for_a_join_go_to_the_scratch(tmp_path: Path) -> None:
    """Owner, 2026-10-20: an intermediate whose only readers are joins is on the way, so the
    merged file is what is left (was 2026-10-15: "a join derives, so its pieces stay")."""
    plan = [
        {
            "tool": "convert_document",
            "args": {"input": "a.docx", "to_format": "pdf", "output": "a.pdf"},
        },
        {"tool": "merge_pdfs", "args": {"inputs": ["a.pdf"], "output": "one.pdf"}},
    ]
    routed = _route(plan, "convert the docx files to pdf and merge them into one.pdf", tmp_path)
    moved = Path(routed[0]["args"]["output"])
    assert moved == tmp_path / "work" / "a.pdf"
    assert routed[1]["args"]["inputs"] == [str(moved)]
    assert routed[1]["args"]["output"] == "one.pdf"


def test_named_formats_are_bare_words_in_any_language() -> None:
    """The request signal reads extensions as bare words: "to mp4", ".gif", Chinese without
    spaces; never the extension inside a filename."""
    from knaif.agent import _named_formats

    kinds = {"mp4": "video", "gif": "video", "mov": "video", "mp3": "audio"}
    assert _named_formats("convert clip.mov to mp4 and make a gif preview", kinds) == {"mp4", "gif"}
    assert _named_formats("trim clip.mp4 to 5 seconds", kinds) == set()
    assert _named_formats("save it as .gif", kinds) == {"gif"}
    assert _named_formats("将clip.mov转换为mp4并制作gif预览", kinds) == {"mp4", "gif"}
    assert _named_formats("конвертирай видео.mov в MP4", kinds) == {"mp4"}
    assert _named_formats("an mp4-like file and a mp3_x", kinds) == set()


def test_derives_is_declared_in_the_skills_yaml() -> None:
    """A piece, a page range or a join is a new thing: the skill says so in tools.yaml, core
    never names a tool. The convert tools follow their format argument."""
    from knaif.registry import load_registry

    ffmpeg = load_registry(Path("skills/ffmpeg/tools.yaml"))
    assert ffmpeg["trim_video"].derives and ffmpeg["concat_video"].derives
    assert not ffmpeg["compress_video"].derives and not ffmpeg["resize_video"].derives
    assert ffmpeg["convert_video"].produces == {"arg": "container"}
    documents = load_registry(Path("skills/documents/tools.yaml"))
    assert documents["split_pdf"].derives and documents["merge_pdfs"].derives
    assert not documents["compress_pdf"].derives


def test_the_agent_passes_its_deriving_tools(tmp_path: Path) -> None:
    from knaif.agent import CommandAgent

    agent = CommandAgent.from_skill("skills/ffmpeg", sandbox=tmp_path)
    kinds, produces, derives = agent.route_kinds()
    assert kinds == agent.file_kinds
    assert produces["extract_audio"] == "audio"
    assert derives == {"trim_video", "concat_video", "split_video", "extract_frames"}
