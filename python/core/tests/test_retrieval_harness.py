"""Tests for the retrieval-quality harness (docs/plans/2026-07-02-retrieval-overhaul.md Phase 0)."""

from knaif.evalsuite.retrieval import evaluate_skill, format_report, language_slice


def test_language_slice_buckets():
    assert language_slice("compress clip.mp4") == "ascii"
    assert language_slice("comprimé la vidéo") == "latin"  # non-ascii Latin
    assert language_slice("压缩 clip.mp4") == "cjk"  # Chinese
    assert language_slice("clip を圧縮") == "cjk"  # Japanese
    assert language_slice("clip 압축") == "cjk"  # Korean


def test_evaluate_skill_structure():
    r = evaluate_skill("ffmpeg", top_k=5)
    assert r["skill"] == "ffmpeg" and r["top_k"] == 5
    o = r["overall"]
    assert o["n"] > 0
    assert 0.0 <= o["recall_at_k"] <= 1.0
    assert 0.0 <= o["mrr"] <= 1.0
    # per-slice buckets present; ffmpeg corpus has ascii/latin/cjk rows
    for sl in ("ascii", "latin", "cjk"):
        assert sl in r["by_slice"]
    assert r["by_slice"]["cjk"]["n"] > 0
    assert isinstance(r["misses"], list)


def test_cjk_recall_recovered_by_ngram_fix():
    """Phase-1 CJK n-gram tokenization must keep CJK recall well above the old
    whitespace-tokenizer floor (~0.43). Regression guard for the fix."""
    r = evaluate_skill("ffmpeg", top_k=5)
    assert r["by_slice"]["cjk"]["recall_at_k"] > 0.8


def test_format_report_smoke():
    out = format_report({"top_k": 5, "skills": {"ffmpeg": evaluate_skill("ffmpeg")}})
    assert "recall@5" in out and "ffmpeg" in out and "cjk" in out


def test_cjk_keyword_matches_by_ngram():
    """A Chinese utterance with a keyword embedded in a space-less run now surfaces
    the intended tool (compress_video owns 压缩; extract_audio owns 提取)."""
    from pathlib import Path

    from knaif.registry import load_registry, retrieve_tools

    reg = load_registry(Path("skills/ffmpeg/tools.yaml"))
    assert "compress_video" in retrieve_tools("将clip.mp4压缩", reg)
    assert "extract_audio" in retrieve_tools("从clip.mp4提取音频", reg)


def test_non_cjk_query_tokens_unchanged():
    """The n-gram path must not alter tokenization for space-delimited text."""
    from knaif.registry import _normalize, _query_tokens

    q = "compress clip.mp4 and extract the audio"
    assert _query_tokens(q) == set(_normalize(q).replace("_", " ").split())


def test_retrieval_no_regression_vs_baseline():
    """CI gate: current recall must not drop below the locked baseline (per skill/slice).
    Update evals/retrieval/2026-07-02_phase1.json intentionally when retrieval
    improves; this guards against silent regressions (keyword edits, tokenizer changes)."""
    import json
    from pathlib import Path

    from knaif import list_skills
    from knaif.evalsuite.retrieval import check_regression, evaluate

    baseline_path = Path("evals/retrieval/2026-07-02_phase1.json")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = evaluate(list_skills(), top_k=baseline["top_k"])
    regressions = check_regression(current, baseline, tol=0.02)
    assert not regressions, f"Retrieval regressed vs baseline: {regressions}"
