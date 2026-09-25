"""Cat B: auto-link undeclared chain intermediates to the producing step's output.

The model often emits a multi-step chain where a later step consumes an
intermediate filename (e.g. ``clip_resized.mp4``) but the earlier step that
should produce it omits its ``output``. Without repair the intermediate looks
like a hallucinated input and the chain is also not executable. The linker
assigns the intermediate as the nearest preceding output-less step's ``output``.
"""

from __future__ import annotations

from knaif.agent import CommandAgent

_UTT = "scale clip.mp4 to 480p and then strip the audio"


def test_link_sets_producer_output():
    plan = [
        {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "height": 480}},
        {"tool": "strip_audio", "args": {"inputs": ["clip_resized.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, _UTT)
    assert plan[0]["args"]["output"] == "clip_resized.mp4"


def test_hallucination_guard_passes_after_linking():
    plan = [
        {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "height": 480}},
        {"tool": "strip_audio", "args": {"inputs": ["clip_resized.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, _UTT)
    assert CommandAgent._hallucinated_filename(plan, _UTT) is None


def test_single_step_hallucination_not_linked():
    # No preceding step can produce the file → leave it for the hallucination guard.
    plan = [{"tool": "strip_audio", "args": {"inputs": ["ghost.mp4"]}}]
    CommandAgent._link_chain_intermediates(plan, "strip the audio")
    assert "output" not in plan[0]["args"]
    assert CommandAgent._hallucinated_filename(plan, "strip the audio") == "ghost.mp4"


def test_explicit_output_not_overwritten():
    # When the model already declared the producer's output, leave it alone.
    plan = [
        {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "output": "small.mp4"}},
        {"tool": "strip_audio", "args": {"inputs": ["small.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, _UTT)
    assert plan[0]["args"]["output"] == "small.mp4"


def test_input_named_in_utterance_not_linked():
    # A second input the user actually named is not an intermediate.
    utt = "join clip.mov and clip2.mp4"
    plan = [{"tool": "concat_video", "args": {"inputs": ["clip.mov", "clip2.mp4"]}}]
    CommandAgent._link_chain_intermediates(plan, utt)
    assert "output" not in plan[0]["args"]


def test_multi_input_producer_not_linked():
    # A producer step with two input files must not receive a single `output`:
    # one output name cannot name two batch deliverables without collision.
    utt = "convert a.mp4 and b.mp4 then strip the audio"
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["a.mp4", "b.mp4"]}},
        {"tool": "strip_audio", "args": {"inputs": ["a_converted.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt, {"convert_video", "strip_audio"})
    assert "output" not in plan[0]["args"]
    # The undeclared intermediate is left for the hallucination guard, not silently chained.
    assert CommandAgent._hallucinated_filename(plan, utt) == "a_converted.mp4"


def test_single_input_glob_producer_not_linked():
    # A glob producer also yields many deliverables — not safe to name one output.
    utt = "convert all clips then strip the audio"
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["*.mov"]}},
        {"tool": "strip_audio", "args": {"inputs": ["clip_converted.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt, {"convert_video", "strip_audio"})
    assert "output" not in plan[0]["args"]


# ── forward-threading a reused source to the producer's output ─────────────────
# The model emits a correct multi-step chain but points the later step at the
# ORIGINAL source filename instead of the producer's output (e.g. unlock_pdf
# then find_in_document on the still-locked original). The linker must thread the
# reused source forward onto the producer's output. (find_in_document is
# read-only / not output-capable, so it is never treated as a producer.)
_DOC_UTT = "unlock sample-protected.pdf with pass secret and check if it contains beta"


def test_forward_threads_reused_source_to_assigned_output():
    plan = [
        {"tool": "unlock_pdf", "args": {"input": "sample-protected.pdf", "password": "secret"}},
        {"tool": "find_in_document", "args": {"input": "sample-protected.pdf", "query": "beta"}},
    ]
    CommandAgent._link_chain_intermediates(plan, _DOC_UTT, {"unlock_pdf"})
    produced = plan[0]["args"].get("output")
    assert produced, "producer should receive an explicit intermediate output"
    assert produced.endswith(".pdf")  # extension preserved
    assert plan[1]["args"]["input"] == produced  # find reads the unlocked file
    assert plan[1]["args"]["input"] != "sample-protected.pdf"


def test_forward_threads_to_explicit_producer_output():
    # When the producer already declares an output, reuse it (don't invent one).
    plan = [
        {
            "tool": "unlock_pdf",
            "args": {"input": "s.pdf", "password": "x", "output": "clear.pdf"},
        },
        {"tool": "find_in_document", "args": {"input": "s.pdf", "query": "beta"}},
    ]
    CommandAgent._link_chain_intermediates(plan, _DOC_UTT, {"unlock_pdf"})
    assert plan[0]["args"]["output"] == "clear.pdf"
    assert plan[1]["args"]["input"] == "clear.pdf"


def test_forward_thread_matches_across_dot_slash_prefix():
    # Small models vary the path prefix between steps; match on the filename.
    plan = [
        {"tool": "unlock_pdf", "args": {"input": ".\\sample-protected.pdf", "password": "secret"}},
        {"tool": "find_in_document", "args": {"input": "sample-protected.pdf", "query": "beta"}},
    ]
    CommandAgent._link_chain_intermediates(plan, _DOC_UTT, {"unlock_pdf"})
    produced = plan[0]["args"]["output"]
    assert plan[1]["args"]["input"] == produced


def test_no_forward_thread_for_readonly_producer():
    # A read-only first step (not output-capable) does not transform the file,
    # so a later step reusing the same source must be left untouched.
    utt = "inspect a.pdf and find beta in a.pdf"
    plan = [
        {"tool": "inspect_document", "args": {"input": "a.pdf"}},
        {"tool": "find_in_document", "args": {"input": "a.pdf", "query": "beta"}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt, set())  # neither is output-capable
    assert "output" not in plan[0]["args"]
    assert plan[1]["args"]["input"] == "a.pdf"


def test_no_forward_thread_without_later_consumer():
    # A lone producer with no later reuse must not be mutated.
    plan = [{"tool": "unlock_pdf", "args": {"input": "s.pdf", "password": "x"}}]
    CommandAgent._link_chain_intermediates(plan, "unlock s.pdf", {"unlock_pdf"})
    assert "output" not in plan[0]["args"]


# ── the named-once rule (docs/plans/2026-09-23-chain-source-threading.md) ──────────
# Threading repairs a model that filled in a name the user said only once ("check if
# it contains beta"). When the user wrote the name again, that later step's input is
# their choice: a fan-out, several steps reading one file, must reach execution as-is.

_FANOUT_UTT = (
    "strip the audio from clip.mov and save it as silent.mp4, then trim silent.mp4 from 0 "
    "to 2 seconds as part1.mp4, then trim silent.mp4 from 2 to 4 seconds as part2.mp4, then "
    "reverse part2.mp4 as part2_rev.mp4, then trim silent.mp4 from 4 seconds to the end as "
    "part3.mp4, then concatenate part1.mp4, part2_rev.mp4 and part3.mp4 into result.mp4"
)


def _fanout_plan() -> list[dict]:
    return [
        {"tool": "strip_audio", "args": {"inputs": ["clip.mov"], "output": "silent.mp4"}},
        {
            "tool": "trim_video",
            "args": {"input": "silent.mp4", "start": "0", "end": "2", "output": "part1.mp4"},
        },
        {
            "tool": "trim_video",
            "args": {"input": "silent.mp4", "start": "2", "end": "4", "output": "part2.mp4"},
        },
        {"tool": "reverse_video", "args": {"inputs": ["part2.mp4"], "output": "part2_rev.mp4"}},
        {
            "tool": "trim_video",
            "args": {"input": "silent.mp4", "start": "4", "output": "part3.mp4"},
        },
        {
            "tool": "concat_video",
            "args": {"inputs": ["part1.mp4", "part2_rev.mp4", "part3.mp4"], "output": "result.mp4"},
        },
    ]


def test_fan_out_over_a_repeated_name_survives_unchanged():
    """The workbench bug: the threader cascaded silent → part1 → part2 down the plan."""
    plan = _fanout_plan()
    CommandAgent._link_chain_intermediates(plan, _FANOUT_UTT)
    assert plan == _fanout_plan()


def test_two_steps_over_a_file_named_twice_are_kept():
    utt = "make a thumbnail of clip.mp4 and compress clip.mp4"
    plan = [
        {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"], "output": "thumb.jpg"}},
        {"tool": "compress_video", "args": {"inputs": ["clip.mp4"], "output": "small.mp4"}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt)
    assert plan[1]["args"]["inputs"] == ["clip.mp4"]


def test_a_dot_slash_repeat_counts_as_the_same_name():
    utt = "unlock ./s.pdf with pass x, then search s.pdf for beta"
    plan = [
        {"tool": "unlock_pdf", "args": {"input": "./s.pdf", "password": "x"}},
        {"tool": "find_in_document", "args": {"input": "s.pdf", "query": "beta"}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt, {"unlock_pdf"})
    assert plan[1]["args"]["input"] == "s.pdf"
    assert "output" not in plan[0]["args"]


def test_a_longer_name_containing_the_source_is_not_a_mention():
    """`myclip.mp4` is not a second mention of `clip.mp4`, so "it" still threads."""
    utt = "trim clip.mp4 to 2 seconds and resize it, keep myclip.mp4 as is"
    plan = [
        {"tool": "trim_video", "args": {"input": "clip.mp4", "end": "2"}},
        {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "height": 480}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt)
    assert plan[1]["args"]["inputs"] == [plan[0]["args"]["output"]]


def test_a_model_invented_source_is_still_threaded():
    """Zero mentions is "at most once": the native-missing repair must keep working.

    "trim clip.mp4, compress it, and remove the audio": the model pointed the last step at
    the raw trim, a name the user never wrote, so the compression would be thrown away.
    """
    utt = "trim clip.mp4 to the first 4 seconds, compress it, and remove the audio"
    plan = [
        {"tool": "trim_video", "args": {"input": "clip.mp4", "output": "clip_trimmed.mp4"}},
        {"tool": "compress_video", "args": {"inputs": ["clip_trimmed.mp4"]}},
        {"tool": "strip_audio", "args": {"inputs": ["clip_trimmed.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt)
    assert plan[1]["args"]["output"] == "clip_trimmed-chained.mp4"
    assert plan[2]["args"]["inputs"] == ["clip_trimmed-chained.mp4"]


# ── the kind rule: never thread onto a file of a different kind ────────────────────
# Kinds come from the skill (`file_kinds:` in skill.yaml, as extension -> kind); core only
# compares them. An extension the skill does not list is unrestricted.
_KINDS = {"mp4": "video", "mkv": "video", "jpg": "image", "png": "image"}


def test_a_reference_is_not_threaded_onto_a_different_kind():
    """ "make a thumbnail of clip.mp4 and compress it" must not compress the thumbnail."""
    utt = "make a thumbnail of clip.mp4 and compress it"
    plan = [
        {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"], "output": "thumb.jpg"}},
        {"tool": "compress_video", "args": {"inputs": ["clip.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt, None, _KINDS)
    assert plan[1]["args"]["inputs"] == ["clip.mp4"]


def test_a_reference_is_threaded_onto_the_same_kind_in_another_container():
    """ "convert clip.mp4 to mkv then strip its audio" is the repair the rule must keep."""
    utt = "convert clip.mp4 to mkv then strip its audio"
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "output": "clip.mkv"}},
        {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt, None, _KINDS)
    assert plan[1]["args"]["inputs"] == ["clip.mkv"]


def test_an_unlisted_extension_is_unrestricted():
    utt = "make a thumbnail of clip.mp4 and compress it"
    plan = [
        {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"], "output": "thumb.heic"}},
        {"tool": "compress_video", "args": {"inputs": ["clip.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt, None, _KINDS)
    assert plan[1]["args"]["inputs"] == ["thumb.heic"]


def test_a_name_against_cjk_text_is_still_a_mention():
    """Unspaced CJK ("为clip.mp4生成") must not hide a repeated name from the count."""
    utt = "为clip.mp4生成缩略图，并压缩clip.mp4"
    plan = [
        {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"]}},
        {"tool": "compress_video", "args": {"inputs": ["clip.mp4"]}},
    ]
    CommandAgent._link_chain_intermediates(plan, utt)
    assert plan[1]["args"]["inputs"] == ["clip.mp4"]
    assert "output" not in plan[0]["args"]
