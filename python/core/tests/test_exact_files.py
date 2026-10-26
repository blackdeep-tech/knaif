"""The exact-files check: after a run, the user's folder holds its inputs plus the deliverables.

A deliverable is an output the run produced successfully that no later step consumed, or one
the user named in the request. Everything else left in the folder (a chain intermediate, a
preview clip, a failed step's partial file) is a leftover.
"""

from __future__ import annotations

from pathlib import Path

from knaif.evalsuite.exact_files import deliverables, leftovers


def _batch(*items: tuple[str, str, int | None]) -> dict:
    outputs = [
        {"input": src, "output": dst, **({} if rc is None else {"returncode": rc})}
        for src, dst, rc in items
    ]
    return {"tool": "run_batch", "result": {"mode": "execute", "outputs": outputs}}


def test_a_chain_delivers_only_its_last_output(tmp_path: Path) -> None:
    a, b, c = (str(tmp_path / n) for n in ("clip.mp4", "clip_trimmed.mp4", "clip_resized.mp4"))
    results = [_batch((a, b, 0)), _batch((b, c, 0))]
    assert deliverables(results, "trim clip.mp4 then resize it") == {Path(c)}


def test_a_failed_output_is_not_a_deliverable(tmp_path: Path) -> None:
    a, b = str(tmp_path / "clip.mp4"), str(tmp_path / "clip_converted.mp4")
    assert deliverables([_batch((a, b, 1))], "convert clip.mp4") == set()


def test_an_intermediate_the_user_named_is_a_deliverable(tmp_path: Path) -> None:
    a, b, c = (str(tmp_path / n) for n in ("clip.mp4", "short.mp4", "short_resized.mp4"))
    results = [_batch((a, b, 0)), _batch((b, c, 0))]
    got = deliverables(results, "trim clip.mp4 into short.mp4, then resize short.mp4")
    assert got == {Path(b), Path(c)}


def test_a_fan_out_delivers_every_branch(tmp_path: Path) -> None:
    a, b, c = (str(tmp_path / n) for n in ("clip.mp4", "clip_thumb.jpg", "clip_compressed.mp4"))
    results = [_batch((a, b, 0)), _batch((a, c, 0))]
    assert deliverables(results, "thumbnail and compress clip.mp4") == {Path(b), Path(c)}


def test_a_list_input_counts_as_consumed(tmp_path: Path) -> None:
    a, b, c = (str(tmp_path / n) for n in ("a.mp4", "b.mp4", "joined.mp4"))
    d = str(tmp_path / "joined_small.mp4")
    concat = {
        "tool": "run_concat",
        "result": {"outputs": [{"input": [a, b], "output": c, "returncode": 0}], "returncode": 0},
    }
    results = [concat, _batch((c, d, 0))]
    assert deliverables(results, "join a.mp4 and b.mp4 then compress") == {Path(d)}


def test_a_preview_clip_is_never_a_deliverable(tmp_path: Path) -> None:
    a, b = str(tmp_path / "clip.mp4"), str(tmp_path / "clip_reversed.mp4")
    preview = {
        "tool": "run_preview",
        "result": {"mode": "execute", "preview_output": str(tmp_path / "clip_preview.mp4")},
    }
    assert deliverables([preview, _batch((a, b, 0))], "reverse clip.mp4") == {Path(b)}


def test_leftovers_are_files_that_are_neither_inputs_nor_deliverables(tmp_path: Path) -> None:
    for name in ("clip.mp4", "sub/clip2.mp4", "clip_trimmed.mp4", "clip_preview.mp4", "out.mp4"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    got = leftovers(
        tmp_path,
        inputs={"clip.mp4", "sub/clip2.mp4"},
        delivered={tmp_path / "out.mp4"},
    )
    assert got == ["clip_preview.mp4", "clip_trimmed.mp4"]


def test_a_clean_folder_has_no_leftovers(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "out.mp4").write_bytes(b"x")
    assert leftovers(tmp_path, inputs={"clip.mp4"}, delivered={tmp_path / "out.mp4"}) == []


# ── which recorded rows the corpus-wide check replays ──────────────────────────────────────


def _row(rid: str, outcome: str, steps: list[dict], idx: int = 0) -> dict:
    return {"id": rid, "utterance_idx": idx, "actual_outcome": outcome, "plan": {"plan": steps}}


def test_selection_takes_chains_and_previews_by_default() -> None:
    from knaif.evalsuite.exact_files import select_rows

    chain = _row("c", "plan", [{"tool": "trim_video"}, {"tool": "resize_video"}])
    preview = _row("p", "plan", [{"tool": "compress_video", "args": {"preview": True}}])
    default_preview = _row("r", "plan", [{"tool": "reverse_video", "args": {}}])
    single = _row("s", "plan", [{"tool": "convert_video", "args": {}}])
    clarify = _row("q", "clarify", [{"tool": "clarify", "args": {}}])
    failed_chain = _row("f", "error", [{"tool": "trim_video"}, {"tool": "rotate_video"}])
    rows = [chain, preview, default_preview, single, clarify, failed_chain]

    picked = select_rows(rows, preview_tools={"reverse_video"})
    assert [r["id"] for r in picked] == ["c", "p", "r", "f"]
    # --all: every row that executed something, whatever its shape.
    every = select_rows(rows, preview_tools={"reverse_video"}, everything=True)
    assert [r["id"] for r in every] == ["c", "p", "r", "s", "f"]


# ── the native runtime: the same rule, read from the binary's output ───────────────────────


def test_native_output_becomes_the_same_results_shape(tmp_path: Path) -> None:
    """`KNAIF_DUMP_PLAN` prints each rendered argv; the plain view prints `✓ <output>` for each
    command that succeeded. Together they say what was read, written, and kept."""
    import json

    from knaif.evalsuite.exact_files import deliverables, native_results

    first = ["ffmpeg", "-y", "-i", "clip.mp4", "-t", "2", "clip_trimmed.mp4"]
    second = ["ffmpeg", "-y", "-i", "clip_trimmed.mp4", "-vf", "scale=-2:240", "clip_small.mp4"]
    stderr = "\n".join(
        [
            "===KNAIF-PLAN===" + json.dumps({"plan": []}),
            "===KNAIF-ARGV===" + json.dumps(first),
            "running: ffmpeg ...",
            "===KNAIF-ARGV===" + json.dumps(second),
        ]
    )
    stdout = "step 1 of 2:\n✓ clip_trimmed.mp4\nstep 2 of 2:\n✓ clip_small.mp4\n"
    results = native_results(stdout, stderr, tmp_path)
    assert deliverables(results, "trim clip.mp4 then resize it") == {
        (tmp_path / "clip_small.mp4").resolve()
    }


def test_a_native_command_without_a_tick_did_not_deliver(tmp_path: Path) -> None:
    import json

    from knaif.evalsuite.exact_files import deliverables, native_results

    argv = ["ffmpeg", "-y", "-i", "clip.mp4", "clip_converted.mkv"]
    stderr = "===KNAIF-ARGV===" + json.dumps(argv)
    stdout = "✗ clip_converted.mkv (ffmpeg exited 1)\n"
    assert deliverables(native_results(stdout, stderr, tmp_path), "convert clip.mp4") == set()


# documents' shape: the step's inputs are in its args (`input`, `inputs`, `image`) and its result
# names what it wrote (`output`, or `outputs` for a split). No `returncode`: a failure raises.


def _doc(tool: str, args: dict, result: dict) -> dict:
    return {"tool": tool, "args": args, "result": result}


def test_a_documents_chain_delivers_only_its_last_output(tmp_path: Path) -> None:
    a, t, n = (
        str(tmp_path / x)
        for x in ("sample.pdf", "sample_trimmed.pdf", "sample_trimmed-numbered.pdf")
    )
    results = [
        _doc("remove_pages", {"input": a, "pages": "-1", "output": t}, {"output": t, "pages": 2}),
        _doc("add_page_numbers", {"input": t}, {"output": n}),
    ]
    got = deliverables(results, "remove the last page of sample.pdf, then add page numbers")
    assert got == {Path(n)}


def test_a_documents_split_delivers_every_file(tmp_path: Path) -> None:
    a = str(tmp_path / "sample.pdf")
    outs = [str(tmp_path / f"sample-pages-{i}.pdf") for i in (1, 2)]
    results = [_doc("split_pdf", {"input": a, "ranges": "1,2"}, {"outputs": outs, "count": 2})]
    assert deliverables(results, "split sample.pdf") == {Path(o) for o in outs}


def test_a_documents_merge_consumes_every_input(tmp_path: Path) -> None:
    a, b, c, d = (
        str(tmp_path / x) for x in ("a.pdf", "b.pdf", "combined.pdf", "combined-numbered.pdf")
    )
    results = [
        _doc("merge_pdfs", {"inputs": [a, b], "output": c}, {"output": c, "pages": 4}),
        _doc("add_page_numbers", {"input": c}, {"output": d}),
    ]
    assert deliverables(results, "merge a.pdf and b.pdf then number the pages") == {Path(d)}


def test_a_relative_input_is_read_against_the_run_folder(tmp_path: Path) -> None:
    """documents keeps the model's spelling in its args (`sample_trimmed.pdf`) while its result
    holds the resolved path. Resolved against the process's cwd, the two never matched, and the
    first documents check saw every intermediate as a deliverable (0 of 154 rows dirty)."""
    t, n = str(tmp_path / "sample_trimmed.pdf"), str(tmp_path / "sample_trimmed-numbered.pdf")
    results = [
        _doc(
            "remove_pages", {"input": "sample.pdf", "output": "sample_trimmed.pdf"}, {"output": t}
        ),
        _doc("add_page_numbers", {"input": "sample_trimmed.pdf"}, {"output": n}),
    ]
    got = deliverables(results, "remove the last page of sample.pdf, then number it", root=tmp_path)
    assert got == {Path(n)}


def test_native_documents_output_becomes_the_same_results_shape(tmp_path: Path) -> None:
    """documents renders no argv: what a step read is in the plan dump (its `input`/`inputs`) and
    what it wrote is each `✓ wrote <path>` under its `step N of M:` header. A routed
    intermediate prints as `<work>/…`, which is never in the row folder."""
    import json

    from knaif.evalsuite.exact_files import deliverables, native_results

    plan = {
        "plan": [
            {
                "tool": "remove_pages",
                "args": {"input": "sample.pdf", "pages": "-1", "output": "t.pdf"},
            },
            {"tool": "add_page_numbers", "args": {"input": "t.pdf"}},
        ]
    }
    stderr = "===KNAIF-PLAN===" + json.dumps(plan) + "\n"
    final = tmp_path / "t-numbered.pdf"
    unrouted = f"step 1 of 2:\nremove pages\n✓ wrote {tmp_path / 't.pdf'}\nstep 2 of 2:\nadd page numbers\n✓ wrote {final}\n"
    results = native_results(unrouted, stderr, tmp_path)
    assert deliverables(
        results, "remove the last page of sample.pdf, then number it", root=tmp_path
    ) == {final.resolve()}
    routed = unrouted.replace(str(tmp_path / "t.pdf"), "<work>/t.pdf")
    results = native_results(routed, stderr, tmp_path)
    got = deliverables(results, "remove the last page of sample.pdf, then number it", root=tmp_path)
    assert got == {final.resolve()}


def test_a_lifted_native_documents_plan_is_read_from_the_run_plan(tmp_path: Path) -> None:
    """A batch runs the model's one step once per file: the step headers count
    the lifted steps, which the binary dumps as the run plan (`===KNAIF-RUN-PLAN===`) after the
    model's plan. Every file a lifted step wrote is delivered, not only the first's."""
    import json

    from knaif.evalsuite.exact_files import deliverables, native_results

    model = {"plan": [{"tool": "add_page_numbers", "args": {"input": "*.pdf"}}]}
    run = {
        "plan": [
            {"tool": "add_page_numbers", "args": {"input": "a.pdf"}},
            {"tool": "add_page_numbers", "args": {"input": "b.pdf"}},
        ]
    }
    stderr = f"===KNAIF-PLAN==={json.dumps(model)}\n===KNAIF-RUN-PLAN==={json.dumps(run)}\n"
    a, b = tmp_path / "a-numbered.pdf", tmp_path / "b-numbered.pdf"
    stdout = f"step 1 of 2:\n✓ wrote {a}\nstep 2 of 2:\n✓ wrote {b}\n"
    results = native_results(stdout, stderr, tmp_path)
    assert deliverables(results, "number every pdf here", root=tmp_path) == {
        a.resolve(),
        b.resolve(),
    }


def test_a_single_native_documents_step_has_no_step_header(tmp_path: Path) -> None:
    import json

    from knaif.evalsuite.exact_files import deliverables, native_results

    plan = {"plan": [{"tool": "rotate_pages", "args": {"input": "a.pdf", "degrees": 90}}]}
    out = tmp_path / "a-rotated.pdf"
    results = native_results(
        f"rotate pages\n✓ wrote {out}\n", "===KNAIF-PLAN===" + json.dumps(plan), tmp_path
    )
    assert deliverables(results, "rotate a.pdf", root=tmp_path) == {out.resolve()}


def test_a_relative_row_folder_is_not_applied_twice(tmp_path: Path, monkeypatch) -> None:
    """The CLI hands `replay_row_native` a relative row folder. `native_results` already put it in
    front of each relative argv path, and `deliverables(root=row_dir)` put it in front again
    (`row/row/clip.mp4`): 162 of 165 ffmpeg rows read as dirty on 2026-10-11."""
    import json

    from knaif.evalsuite.exact_files import deliverables, native_results

    monkeypatch.chdir(tmp_path)
    row = Path("sandbox") / "row__0"
    argv = ["ffmpeg", "-y", "-i", "clip.mp4", "clip_small.mp4"]
    stderr = "===KNAIF-PLAN===" + json.dumps({"plan": []}) + "\n===KNAIF-ARGV===" + json.dumps(argv)
    results = native_results("✓ clip_small.mp4\n", stderr, row)
    assert deliverables(results, "shrink clip.mp4", root=row) == {
        (tmp_path / row / "clip_small.mp4").resolve()
    }


def test_a_replay_that_stops_before_running_is_an_error_not_clean(tmp_path: Path) -> None:
    """A replay that ends at a clarify, a reject or a declined step ran nothing, so it cannot
    vouch for the folder: found 2026-10-12, when URL rows replayed without download approval
    stopped at the approval question and every one of them reported `clean`."""
    from knaif.evalsuite.exact_files import replay_row

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "clip.mp4").write_bytes(b"x")

    class _Stops:
        def execute_plan(self, *_a, **_k):
            q = "Downloading https://h.test/a.mp4 needs your approval"
            return [{"tool": "clarify", "args": {"question": q}, "result": {"question": q}}]

    report = replay_row(
        lambda _folder: _Stops(),
        {"plan": [{"tool": "convert_video", "args": {"inputs": ["https://h.test/a.mp4"]}}]},
        "convert https://h.test/a.mp4",
        fixtures,
        tmp_path / "row",
    )
    assert report["error"] is not None and "needs your approval" in report["error"]


def test_only_a_url_row_passes_allow_download_to_the_binary():
    """An older binary rejects an unknown flag, which would fail every row before inference."""
    from knaif.evalsuite.native_lane import LaneConfig, build_argv

    lane = LaneConfig.__new__(LaneConfig)
    object.__setattr__(lane, "binary", Path("knaif"))
    object.__setattr__(lane, "model_path", Path("m.gguf"))
    assert "--allow-download" not in build_argv(lane, "ffmpeg", "convert clip.mp4 to mkv")
    assert "--allow-download" in build_argv(lane, "ffmpeg", "convert https://h.test/a.mov to mp4")


def test_a_native_error_excerpt_leaves_out_the_dumps() -> None:
    """The plan and argv dumps carry absolute run-scratch paths (a home folder) and are not the
    error: the excerpt is the binary's own last lines (an L3 run)."""
    from knaif.evalsuite.exact_files import native_error_excerpt

    stderr = (
        '===KNAIF-PLAN==={"plan": []}\n'
        '===KNAIF-RUN-PLAN==={"plan": [{"args": {"input": "C:/Users/someone/x.pdf"}}]}\n'
        '===KNAIF-ARGV==="ffmpeg"\n'
        "✗ scan.pdf: The step produced an empty file for scan.txt; nothing was written.\n"
        "3 done, 1 failed in 1.0s, 11.0 KB\n"
    )
    got = native_error_excerpt(1, stderr, "")
    assert got == (
        "exit 1: ✗ scan.pdf: The step produced an empty file for scan.txt; nothing was "
        "written.\n3 done, 1 failed in 1.0s, 11.0 KB"
    )


def test_with_routing_an_intermediate_left_in_the_folder_was_kept(tmp_path: Path) -> None:
    """A run that routes intermediates (`scratch_intermediates:`) moves every one it does not keep
    to its scratch, so one the results place in the folder is kept on purpose: the agent's keep
    rule (owner, 2026-10-15; a full eval run flagged 22 such rows, 2026-10-22)."""
    a, t, m = (str(tmp_path / x) for x in ("clip.mp4", "clip_trimmed.mp4", "clip_trimmed.mp3"))
    results = [_batch((a, t, 0)), _batch((t, m, 0))]
    utterance = "trim clip.mp4 and extract the audio"
    assert deliverables(results, utterance, root=tmp_path, routed=True) == {Path(t), Path(m)}
    assert deliverables(results, utterance, root=tmp_path) == {Path(m)}


def test_with_routing_an_intermediate_in_the_scratch_is_not_delivered(tmp_path: Path) -> None:
    row, work = tmp_path / "row", tmp_path / "work"
    a, t, s = str(row / "clip.mp4"), str(work / "clip_trimmed.mp4"), str(row / "clip_small.mp4")
    results = [_batch((a, t, 0)), _batch((t, s, 0))]
    got = deliverables(results, "trim clip.mp4 and compress it", root=row, routed=True)
    assert got == {Path(s)}


def test_a_documents_replay_leaves_what_the_keep_rule_keeps(tmp_path: Path) -> None:
    """documents_hard2_005: "merge … then keep only pages 1-2 of the merged file". A page range
    is derived from the merged whole, so the keep rule keeps it beside the range."""
    import pytest

    pikepdf = pytest.importorskip("pikepdf")
    from knaif import create_agent
    from knaif.evalsuite.exact_files import replay_row

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for name, pages in (("a.pdf", 1), ("b.pdf", 2)):
        pdf = pikepdf.Pdf.new()
        for _ in range(pages):
            pdf.add_blank_page()
        pdf.save(fixtures / name)
    plan = {
        "plan": [
            {"tool": "merge_pdfs", "args": {"inputs": ["a.pdf", "b.pdf"], "output": "merged.pdf"}},
            {"tool": "split_pdf", "args": {"input": "merged.pdf", "ranges": "1-2"}},
        ]
    }
    report = replay_row(
        lambda folder: create_agent("documents", root=folder),
        plan,
        "merge a.pdf and b.pdf, then keep only pages 1-2 of the merged file",
        fixtures,
        tmp_path / "row",
    )
    assert report["error"] is None
    assert report["leftovers"] == []
