"""Tests for knaif.nl_clarify_gate — unit table from the plan."""

from __future__ import annotations

from knaif.nl_clarify_gate import nl_clarify_gate
from knaif.registry import ToolDef


def _plan(tool: str, **args) -> list[dict]:
    return [{"tool": tool, "args": args}]


def _question(result: list[dict]) -> str:
    return result[0]["args"]["question"]


def _grounded_registry(tool: str, grounded: list[str]) -> dict[str, ToolDef]:
    return {
        tool: ToolDef(
            name=tool,
            description="x",
            required_args=("input", *grounded),
            grounded_args=tuple(grounded),
        )
    }


# ── grounded_args: secret/unguessable args must appear in the utterance ───────


# NOTE: these isolate the grounded-arg behaviour from the file-input check
# (whose recognition uses ffmpeg MEDIA_EXTS and doesn't see .pdf — the
# project_input_refs_vocab_in_core leak), so the plans carry no file input.


def test_grounded_arg_present_passes_through():
    plan = _plan("protect_pdf", password="hunter2")
    reg = _grounded_registry("protect_pdf", ["password"])
    assert nl_clarify_gate("protect report.pdf with hunter2", plan, registry=reg) is plan


def test_grounded_arg_absent_clarifies():
    plan = _plan("protect_pdf", password="your_password")
    reg = _grounded_registry("protect_pdf", ["password"])
    result = nl_clarify_gate("password protect report.pdf", plan, registry=reg)
    assert result[0]["tool"] == "clarify"
    assert "password" in _question(result).lower()


def test_grounded_arg_clarify_precedes_file_check():
    # The grounded check runs before file-grounding, so the dangerous
    # missing-password case clarifies on the password even when a file input
    # is present (and unrecognized by the file check).
    plan = _plan("protect_pdf", input="report.pdf", password="your_password")
    reg = _grounded_registry("protect_pdf", ["password"])
    result = nl_clarify_gate("password protect report.pdf", plan, registry=reg)
    assert result[0]["tool"] == "clarify"
    assert "password" in _question(result).lower()


def test_named_document_files_pass_through():
    # Document files (.pdf/.docx) named inline must not trigger a file clarify.
    plan = _plan("merge_pdfs", inputs=["a.pdf", "b.pdf"], output="out.pdf")
    assert nl_clarify_gate("merge a.pdf and b.pdf into out.pdf", plan) is plan


def test_grounded_args_skipped_without_registry():
    # Backward-compatible: no registry → no grounding enforcement.
    plan = _plan("protect_pdf", password="your_password")
    assert nl_clarify_gate("password protect report.pdf", plan) is plan


# ── utterance names a file → plan passes through ─────────────────────────────


def test_named_file_passes_through():
    plan = _plan("resize_video", inputs=["clip_4k.mp4"])
    assert nl_clarify_gate("resize clip_4k.mp4 to 1080p", plan) is plan


def test_named_file_in_list_passes_through():
    plan = _plan("convert_video", inputs=["audio.mp3"])
    assert nl_clarify_gate("convert audio.mp3 to flac", plan) is plan


# ── stem in utterance → plan passes through (stem resolver ran first) ─────────


def test_stem_token_passes_through():
    # Token is already a stem (stem resolver hasn't run yet or token is stem form).
    plan = _plan("resize_video", inputs=["clip_4k"])
    assert nl_clarify_gate("downscale clip_4k to 1080p", plan) is plan


def test_resolved_stem_in_utterance_passes_through():
    # Stem resolver ran and substituted clip_4k → clip_4k.mp4; the stem
    # "clip_4k" still appears in the utterance.
    plan = _plan("resize_video", inputs=["clip_4k.mp4"])
    assert nl_clarify_gate("downscale clip_4k to 1080p", plan) is plan


# ── guessed stem not in utterance → clarify ──────────────────────────────────


def test_guessed_stem_not_in_utterance_clarifies():
    # The core PRIZE case: model emits a stem "clip_4k" but utterance says
    # "the 4K video" — "clip_4k" does not appear in the utterance.
    plan = _plan("resize_video", inputs=["clip_4k"])
    result = nl_clarify_gate("resize the 4K video to 1080p", plan)
    assert result[0]["tool"] == "clarify"


# ── bare descriptor, injection OFF → clarify ─────────────────────────────────


def test_descriptor_injection_off_clarifies():
    plan = _plan("resize_video", inputs=["the 4K video"])
    result = nl_clarify_gate("resize the 4K video to 1080p", plan)
    assert result[0]["tool"] == "clarify"


def test_guessed_filename_not_in_utterance_clarifies():
    # Model guessed clip_4k.mp4 but utterance only says "the 4K video".
    plan = _plan("resize_video", inputs=["clip_4k.mp4"])
    result = nl_clarify_gate("resize the 4K video to 1080p", plan)
    assert result[0]["tool"] == "clarify"


def test_descriptor_audio_injection_off_clarifies():
    plan = _plan("extract_audio", inputs=["the audio file"])
    result = nl_clarify_gate("convert the audio file to flac", plan)
    assert result[0]["tool"] == "clarify"


# ── batch / glob utterance → no gate ─────────────────────────────────────────


def test_batch_all_passes_through():
    plan = _plan("convert_video", inputs=["*.mp4"])
    result = nl_clarify_gate("batch convert all videos to mp4", plan)
    assert result is plan


def test_batch_every_passes_through():
    plan = _plan("convert_video", inputs=["*.mp4"])
    result = nl_clarify_gate("convert every video to webm", plan)
    assert result is plan


def test_batch_each_passes_through():
    plan = _plan("compress_video", inputs=["*.mp4"])
    result = nl_clarify_gate("compress each video for email", plan)
    assert result is plan


def test_batch_folder_passes_through():
    plan = _plan("convert_video", inputs=["*.mp4"])
    result = nl_clarify_gate("convert all videos in this folder to mp4", plan)
    assert result is plan


def test_nameless_batch_all_videos_passes_through():
    # "grab a frame from all videos" — nameless batch is valid.
    plan = _plan("extract_frame", inputs=["*.mp4"])
    result = nl_clarify_gate("grab a frame from all videos at the first frame", plan)
    assert result is plan


def test_glob_token_passes_through():
    plan = _plan("convert_video", inputs=["*.mp4"])
    result = nl_clarify_gate("convert some videos", plan)
    assert result is plan


# ── concat ────────────────────────────────────────────────────────────────────


def test_concat_with_named_files_passes_through():
    plan = _plan("concat_video", inputs=["clip.mov", "clip_4k.mp4"])
    result = nl_clarify_gate("join clip.mov and clip_4k.mp4 together", plan)
    assert result is plan


def test_nameless_concat_clarifies():
    plan = _plan("concat_video", inputs=["clip1.mp4", "clip2.mp4"])
    result = nl_clarify_gate("concatenate the two clips together", plan)
    assert result[0]["tool"] == "clarify"


# ── output-only filename in utterance, no named input → clarify ──────────────


def test_output_only_filename_clarifies():
    # "save as output.mp4" has a filename, but it's the output, not the input.
    plan = _plan("convert_video", inputs=["the video"])
    result = nl_clarify_gate("convert the video to mp4 and save as output.mp4", plan)
    assert result[0]["tool"] == "clarify"


def test_guessed_filename_with_output_in_utterance_clarifies():
    # Model guessed an input filename; utterance only has the output filename.
    plan = _plan("resize_video", inputs=["clip_4k.mp4"])
    result = nl_clarify_gate("resize the video and save as output.mp4", plan)
    assert result[0]["tool"] == "clarify"


# ── injection ON ──────────────────────────────────────────────────────────────


def test_injection_on_descriptor_unique_match_plans():
    plan = _plan("resize_video", inputs=["the 4K video"])
    result = nl_clarify_gate(
        "resize the 4K video to 1080p",
        plan,
        injected_files={"clip_4k.mp4"},
    )
    assert result is plan


def test_injection_on_guessed_filename_in_injected_plans():
    # Model emitted the concrete filename that's in the injected listing.
    plan = _plan("resize_video", inputs=["clip_4k.mp4"])
    result = nl_clarify_gate(
        "resize the 4K video to 1080p",
        plan,
        injected_files={"clip_4k.mp4"},
    )
    assert result is plan


def test_injection_on_ambiguous_clarifies():
    plan = _plan("resize_video", inputs=["the 4K video"])
    result = nl_clarify_gate(
        "resize the 4K video to 1080p",
        plan,
        injected_files={"clip_4k.mp4", "clip_4k_backup.mp4"},
    )
    assert result[0]["tool"] == "clarify"


def test_injection_on_no_match_clarifies():
    plan = _plan("resize_video", inputs=["the 4K video"])
    result = nl_clarify_gate(
        "resize the 4K video to 1080p",
        plan,
        injected_files={"plain.mp4"},
    )
    assert result[0]["tool"] == "clarify"


def test_injection_on_descriptor_zero_injected_clarifies():
    plan = _plan("resize_video", inputs=["the 4K video"])
    result = nl_clarify_gate(
        "resize the 4K video to 1080p",
        plan,
        injected_files=set(),
    )
    assert result[0]["tool"] == "clarify"


# ── mixed inputs: one stem, one bare descriptor → clarify ────────────────────


def test_mixed_inputs_stem_and_descriptor_clarifies():
    # First input is a stem (resolved), second is a bare descriptor → should clarify.
    plan = _plan("concat_video", inputs=["clip_4k.mp4", "the promo clip"])
    result = nl_clarify_gate(
        "join clip_4k and the promo clip",
        plan,
    )
    assert result[0]["tool"] == "clarify"


# ── clarify question quality ──────────────────────────────────────────────────


def test_clarify_question_uses_descriptor_phrase():
    plan = _plan("resize_video", inputs=["the 4K video"])
    result = nl_clarify_gate("resize the 4K video to 1080p", plan)
    q = _question(result)
    assert "4K video" in q


def test_clarify_question_is_a_question():
    plan = _plan("resize_video", inputs=["the 4K video"])
    result = nl_clarify_gate("resize the 4K video to 1080p", plan)
    assert _question(result).endswith("?")


# ── plan with no file-bearing args passes through ────────────────────────────


def test_no_file_args_passes_through():
    plan = [{"tool": "done", "args": {}}]
    assert nl_clarify_gate("do something", plan) is plan


def test_clarify_step_passes_through():
    # A pre-existing clarify step is not double-wrapped.
    plan = [{"tool": "clarify", "args": {"question": "Which file?"}}]
    assert nl_clarify_gate("the thing", plan) is plan


# ── intermediate output tracking ─────────────────────────────────────────────


def test_multistep_intermediate_output_not_gated():
    """Step 2 input that is step 1 output must not trigger the gate."""
    plan = [
        {
            "tool": "trim_video",
            "args": {"input": "clip.mp4", "start": "0", "end": "5", "output": "clip_trimmed.mp4"},
        },
        {"tool": "resize_video", "args": {"inputs": ["clip_trimmed.mp4"], "height": 720}},
    ]
    result = nl_clarify_gate("trim clip.mp4 to 5 seconds then resize to 720p", plan)
    assert result is plan, "Gate must not fire on intermediate plan output clip_trimmed.mp4"


def test_multistep_only_first_step_input_checked():
    """When step 1 is well-specified, step 2's intermediate input is exempt."""
    plan = [
        {
            "tool": "convert_video",
            "args": {"inputs": ["clip.mp4"], "container": "mkv", "output": "clip.mkv"},
        },
        {"tool": "compress_video", "args": {"inputs": ["clip.mkv"], "crf": 28}},
    ]
    result = nl_clarify_gate("convert clip.mp4 to mkv and then compress it", plan)
    assert result is plan


def test_multistep_still_gates_unspecified_first_step():
    """Even in multi-step, gate fires if the FIRST step's input is unspecified."""
    plan = [
        {
            "tool": "resize_video",
            "args": {"inputs": ["clip_4k.mp4"], "height": 1080, "output": "clip_resized.mp4"},
        },
        {"tool": "compress_video", "args": {"inputs": ["clip_resized.mp4"], "crf": 28}},
    ]
    result = nl_clarify_gate("resize the 4K video to 1080p and compress it", plan)
    assert result is not plan, "Gate must fire when first step's input is unspecified"
