"""knaif 1.4.0 against general agents on the same local model (`scripts/agent_vs_knaif/local_*.py`).

Parsers are tested on recorded output, never on a live agent (docs/plans/
2026-11-25-local-agents-comparison.md). The OpenClaw result and the Ollama log lines below were
captured on 2026-11-27 from real runs; local paths are replaced by placeholders.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HERE = REPO / "scripts" / "agent_vs_knaif"


def _load(name: str):
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(HERE))


@pytest.fixture(scope="module")
def lc():
    return _load("local_compare")


@pytest.fixture(scope="module")
def la():
    return _load("local_agents")


@pytest.fixture(scope="module")
def proxy():
    return _load("model_proxy")


# ── the request set ──────────────────────────────────────────────────────────


def test_scenarios_follow_the_plan(lc):
    scen = lc.load_scenarios()
    assert len(scen) == 25
    chains = [s for s in scen if s["id"].startswith(("v-chain", "d-chain"))]
    assert [s["id"] for s in chains] == ["v-chain3-en", "v-chain3-de", "v-chain4-en", "d-chain2-en"]
    kinds = {
        "video": [s for s in scen if s["skill"] == "ffmpeg" and "expect" in s],
        "folder": [s for s in scen if s.get("batch") or s.get("batch_criteria")],
        "documents": [s for s in scen if s["skill"] == "documents" and "criteria" in s],
        "clarify": [s for s in scen if s.get("behavior") == "clarify"],
        "reject": [s for s in scen if s.get("behavior") == "reject"],
    }
    assert {k: len(v) for k, v in kinds.items()} == {
        "video": 13,
        "folder": 4,
        "documents": 4,
        "clarify": 2,
        "reject": 2,
    }
    assert sum(s["lang"] != "en" for s in scen) > len(scen) / 2
    assert len({s["id"] for s in scen}) == 25
    for s in scen:
        fixture = REPO / "sandbox" / "fixtures" / s["skill"] / s["fixture"]
        if s.get("behavior") != "clarify" and not s["fixture"].endswith("/"):
            # the vague requests and the whole-folder ones name no file, on purpose
            assert s["fixture"] in s["utterance"], s["id"]
        assert s["skill"] in fixture.parts


# ── tokens and turns, from the model server's log ────────────────────────────

OLLAMA_LOG = """\
[GIN] 2026/11/27 - 10:19:03 | 200 |    1.29s |       127.0.0.1 | POST     "/api/chat"
slot   operator(): id  0 | task 810 | new prompt, n_ctx_slot = 32768, n_keep = 4, task.n_tokens = 15458
slot print_timing: id  0 | task 810 | prompt eval time =      27.82 ms /   114 tokens (    0.24 ms per token,  4097.33 tokens per second)
slot print_timing: id  0 | task 810 |        eval time =     143.18 ms /    21 tokens (    7.16 ms per token,   139.68 tokens per second)
slot      release: id  0 | task 810 | stop processing: n_tokens = 15478, truncated = 0
slot   operator(): id  0 | task 832 | new prompt, n_ctx_slot = 32768, n_keep = 4, task.n_tokens = 15573
slot print_timing: id  0 | task 832 | prompt eval time =      20.26 ms /    91 tokens (    0.22 ms per token,  4491.39 tokens per second)
slot print_timing: id  0 | task 832 |        eval time =     103.00 ms /    15 tokens (    6.87 ms per token,   145.63 tokens per second)
"""


def test_server_log_gives_tokens_per_run(lc):
    t = lc.parse_server_log(OLLAMA_LOG)
    assert t == {
        "model_requests": 2,
        "prompt_tokens": 15458 + 15573,
        "new_prompt_tokens": 114 + 91,
        "gen_tokens": 21 + 15,
        "max_prompt_tokens": 15573,
        "truncated": False,
    }


def test_server_log_flags_a_truncated_prompt(lc):
    log = OLLAMA_LOG.replace("truncated = 0", "truncated = 1")
    assert lc.parse_server_log(log)["truncated"] is True


# ── the model traffic the proxy recorded ─────────────────────────────────────


def _ndjson(*chunks):
    return "\n".join(json.dumps(c) for c in chunks)


def test_model_log_counts_tool_calls_native_api(lc):
    rec = {
        "path": "/api/chat",
        "status": 200,
        "request": {"model": "m", "messages": []},
        "response": _ndjson(
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "exec", "arguments": {"command": "ls"}}}],
                },
                "done": False,
            },
            {"message": {"content": ""}, "done": True, "prompt_eval_count": 10, "eval_count": 5},
        ),
    }
    s = lc.summarize_model_log([rec, rec])
    assert s["chat_requests"] == 2
    assert s["tool_calls"] == 2
    assert s["tool_names"] == {"exec": 2}
    assert s["format_errors"] == 0
    assert s["limited"] == 0


def test_model_log_counts_tool_calls_openai_stream(lc):
    sse = "\n\n".join(
        "data: " + json.dumps(c)
        for c in [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "shell", "arguments": ""}}
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}
                ]
            },
        ]
    )
    rec = {
        "path": "/v1/chat/completions",
        "status": 200,
        "request": {},
        "response": sse + "\n\ndata: [DONE]",
    }
    s = lc.summarize_model_log([rec])
    assert s["tool_calls"] == 1
    assert s["tool_names"] == {"shell": 1}


def test_model_log_counts_a_tool_call_written_as_text(lc):
    """The model wrote a tool call into its text instead of the API's tool-call field: the agent
    cannot run it. That is the "format" failure the plan separates from a wrong result."""
    rec = {
        "path": "/api/chat",
        "status": 200,
        "request": {},
        "response": _ndjson(
            {
                "message": {
                    "content": '<tool_call>\n{"name": "exec", "arguments": {"command": "ls"}}'
                },
                "done": True,
            }
        ),
    }
    s = lc.summarize_model_log([rec])
    assert s["tool_calls"] == 0
    assert s["format_errors"] == 1


def test_model_log_counts_the_bracket_form_written_as_text(lc):
    """Open Interpreter's model wrote `[tool_call: glob for pattern '*.mov']` into its reply
    (2026-11-27): a tool call the agent cannot run, so a format failure, not "no result"."""
    sse = "data: " + json.dumps(
        {"choices": [{"delta": {"content": "Let me look. [tool_call: glob for pattern '*.mov']"}}]}
    )
    rec = {"path": "/v1/chat/completions", "status": 200, "request": {}, "response": sse}
    assert lc.summarize_model_log([rec])["format_errors"] == 1


def test_model_log_counts_requests_refused_by_the_turn_limit(lc):
    rec = {"path": "/api/chat", "status": 429, "request": {}, "response": "{}"}
    assert lc.summarize_model_log([rec])["limited"] == 1


def test_model_log_survives_unicode_line_breaks(lc, tmp_path):
    """A raw U+0085 inside a recorded reply broke `splitlines()` in the first full run
    (2026-11-27, run 34): records are split on "\\n" only."""
    rec = {
        "path": "/api/chat",
        "status": 200,
        "request": {"messages": [{"role": "user", "content": "a\x85b c"}]},
        "response": _ndjson({"message": {"content": "x\x85y"}, "done": True}),
    }
    p = tmp_path / "run.model.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in (rec, rec)) + "\n", encoding="utf-8"
    )
    records = lc.read_model_log(p)
    assert len(records) == 2
    assert lc.summarize_model_log(records)["chat_requests"] == 2
    assert lc.read_model_log(tmp_path / "missing.jsonl") == []


def test_model_log_ignores_non_chat_calls(lc):
    rec = {"path": "/api/show", "status": 200, "request": {"model": "m"}, "response": "{}"}
    assert lc.summarize_model_log([rec])["chat_requests"] == 0


# ── grading ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,asked",
    [
        ("What would you like me to improve: resolution, size or sound?", True),
        ("您想要什么格式？", True),
        ("I improved the video. Done.", False),
        ("", False),
    ],
)
def test_question_detection(lc, text, asked):
    assert lc.has_question(text) is asked


def _copy_fixture(tmp_path: Path, skill: str, name: str) -> Path:
    src = REPO / "sandbox" / "fixtures" / skill / name
    if not src.exists():
        pytest.skip(f"fixture {src} not generated (just eval-fixtures {skill})")
    dst = tmp_path / name
    dst.write_bytes(src.read_bytes())
    return dst


def test_clarify_passes_on_a_question_with_nothing_made(lc, tmp_path):
    fx = tmp_path / "clip.mp4"
    fx.write_bytes(b"video")
    before = lc.file_hash(fx)
    g = lc.grade_behavior("clarify", tmp_path, "clip.mp4", before, "Which part should I improve?")
    assert g["result"] == "PASS"
    assert g["asked"] is True


def test_clarify_fails_when_the_agent_made_something(lc, tmp_path):
    fx = tmp_path / "clip.mp4"
    fx.write_bytes(b"video")
    before = lc.file_hash(fx)
    (tmp_path / "clip_better.mp4").write_bytes(b"other")
    g = lc.grade_behavior("clarify", tmp_path, "clip.mp4", before, "Did you want it sharper?")
    assert g["result"] == "FAIL"
    assert g["produced"] == ["clip_better.mp4"]


def test_reject_passes_with_the_source_intact(lc, tmp_path):
    fx = tmp_path / "clip.mp4"
    fx.write_bytes(b"video")
    before = lc.file_hash(fx)
    g = lc.grade_behavior("reject", tmp_path, "clip.mp4", before, "I won't delete it.")
    assert g["result"] == "PASS"
    assert g["source_intact"] is True


def test_reject_fails_when_the_source_is_gone_or_changed(lc, tmp_path):
    fx = tmp_path / "clip.mp4"
    fx.write_bytes(b"video")
    before = lc.file_hash(fx)
    fx.write_bytes(b"truncated")
    assert lc.grade_behavior("reject", tmp_path, "clip.mp4", before, "")["result"] == "FAIL"
    fx.unlink()
    g = lc.grade_behavior("reject", tmp_path, "clip.mp4", before, "Deleted.")
    assert g["result"] == "FAIL"
    assert g["source_intact"] is False


def test_documents_grade_reads_the_produced_pdf(lc, tmp_path):
    from pypdf import PdfReader, PdfWriter

    src = _copy_fixture(tmp_path, "documents", "sample.pdf")
    reader = PdfReader(str(src))
    good = PdfWriter()
    for page in reader.pages[1:3]:
        good.add_page(page)
    with (tmp_path / "pages_2-3.pdf").open("wb") as f:
        good.write(f)
    g = lc.grade_documents({"page_texts": ["Beta", "Gamma"]}, tmp_path, "sample.pdf")
    assert g["result"] == "PASS", g
    assert g["produced"] == "pages_2-3.pdf"


def test_documents_grade_fails_a_wrong_split(lc, tmp_path):
    from pypdf import PdfReader, PdfWriter

    src = _copy_fixture(tmp_path, "documents", "sample.pdf")
    wrong = PdfWriter()
    wrong.add_page(PdfReader(str(src)).pages[0])
    with (tmp_path / "out.pdf").open("wb") as f:
        wrong.write(f)
    g = lc.grade_documents({"page_texts": ["Beta", "Gamma"]}, tmp_path, "sample.pdf")
    assert g["result"] == "FAIL"


def test_documents_grade_counts_pages_from_the_file(lc, tmp_path):
    src = _copy_fixture(tmp_path, "documents", "sample.pdf")
    (tmp_path / "numbered.pdf").write_bytes(src.read_bytes())
    g = lc.grade_documents({"page_numbers_from": 1, "pages": 3}, tmp_path, "sample.pdf")
    assert "pages=3" in g["matched"]
    assert g["result"] == "FAIL"  # an unchanged copy has no page numbers


def test_documents_grade_ignores_helper_scripts(lc, tmp_path):
    _copy_fixture(tmp_path, "documents", "sample.pdf")
    (tmp_path / "split.py").write_text("print(1)", encoding="utf-8")
    g = lc.grade_documents({"page_texts": ["Beta", "Gamma"]}, tmp_path, "sample.pdf")
    assert g["result"] == "no-output"


def test_batch_documents_pairs_each_input(lc, tmp_path, monkeypatch):
    d = tmp_path
    for name in ("contract.pdf", "invoice.pdf"):
        (d / name).write_bytes(b"%PDF-src " + name.encode())
    inputs = lc.snapshot_inputs(d)
    (d / "contract_numbered.pdf").write_bytes(b"%PDF-out")
    (d / "invoice-numbered.pdf").write_bytes(b"%PDF-out")
    seen = []

    def fake_check(path, criteria, src):
        seen.append((path.name, src.name))
        return [f"ok:{path.name}"], []

    monkeypatch.setattr(lc, "_check_pdf", fake_check)
    g = lc.grade_batch_documents({"page_numbers_from": 1}, d, inputs)
    assert g["result"] == "PASS"
    assert sorted(seen) == [
        ("contract_numbered.pdf", "contract.pdf"),
        ("invoice-numbered.pdf", "invoice.pdf"),
    ]
    (d / "invoice-numbered.pdf").unlink()
    assert lc.grade_batch_documents({"page_numbers_from": 1}, d, inputs)["result"] == "FAIL"


def test_place_fixture_file_and_folder(lc, tmp_path):
    one = {"skill": "documents", "fixture": "sample.pdf"}
    if not lc.fixture_path(one).exists():
        pytest.skip("documents fixtures not generated (just eval-fixtures documents)")
    d1 = tmp_path / "one"
    d1.mkdir()
    before = lc.place_fixture(one, d1)
    assert list(before) == ["sample.pdf"]
    folder = {
        "skill": "documents",
        "fixture": "batch_pdf/",
        "files": ["contract.pdf", "invoice.pdf", "minutes.pdf", "scan.pdf"],
    }
    d2 = tmp_path / "folder"
    d2.mkdir()
    before = lc.place_fixture(folder, d2)
    assert sorted(before) == ["contract.pdf", "invoice.pdf", "minutes.pdf", "scan.pdf"]
    assert not (d2 / "locked.pdf").exists()
    assert all(h for h in before.values())


def test_failure_cause(lc):
    base = {"result": "FAIL", "source_intact": True, "limit_hit": None, "format_errors": 0}
    assert lc.failure_cause({**base, "result": "PASS"}) is None
    assert lc.failure_cause({**base, "source_intact": False}) == "source damaged"
    assert lc.failure_cause({**base, "limit_hit": "time"}) == "limit hit"
    assert (
        lc.failure_cause({**base, "result": "no-output", "format_errors": 2}) == "tool-call format"
    )
    assert lc.failure_cause({**base, "result": "no-output"}) == "no result"
    assert lc.failure_cause(base) == "wrong result"


def test_failure_cause_format_on_a_behaviour_request(lc):
    """The v2 model wrote `{"name": "shell", ...}` as text on the vague request (tuning pass,
    2026-11-27): nothing was made and no question asked, because no tool call could run."""
    row = {
        "result": "FAIL",
        "source_intact": True,
        "limit_hit": None,
        "format_errors": 3,
        "produced": [],
    }
    assert lc.failure_cause(row) == "tool-call format"
    assert lc.failure_cause({**row, "produced": ["x.mp4"]}) == "wrong result"


def test_file_hash(lc, tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    assert lc.file_hash(p) == hashlib.sha256(b"abc").hexdigest()
    assert lc.file_hash(tmp_path / "missing") is None


# ── adapters ─────────────────────────────────────────────────────────────────

OPENCLAW_RESULT = {
    "ok": False,
    "status": "error",
    "final": "The video file `clip.mp4` has been successfully converted to `clip.mkv` using "
    "`ffmpeg`.\n\nI converted clip.mp4 to mkv.",
    "usage": {"input": 11170, "output": 146, "cacheRead": 20286, "cacheWrite": 0, "total": 31602},
    "assistantTurns": 3,
    "toolSummary": {"calls": 2, "tools": ["exec", "process"], "failures": 0},
    "model": "stock-qwen3-4b",
    "provider": "ollama",
    "error": {
        "message": "Agent exec cleanup failed: EBUSY: resource busy or locked, unlink "
        "'<tmp>/openclaw-agent-exec-1G2iwb/tmp/plugin-captures/owner.sqlite'",
        "kind": "cleanup",
    },
}


def test_openclaw_parse_keeps_a_cleanup_error_apart(la):
    p = la.ADAPTERS["openclaw"].parse(json.dumps(OPENCLAW_RESULT, indent=2), "")
    assert p["final"].endswith("I converted clip.mp4 to mkv.")
    assert p["agent_turns"] == 3
    assert p["agent_tools"] == ["exec", "process"]
    assert p["agent_error"] is None
    assert p["cleanup_error"] is True


def test_openclaw_parse_reports_a_real_error(la):
    bad = {
        "ok": False,
        "status": "error",
        "final": "",
        "error": {"message": "Repetitive output stopped.", "kind": "incomplete_turn"},
    }
    p = la.ADAPTERS["openclaw"].parse(json.dumps(bad), "")
    assert p["agent_error"] == "incomplete_turn: Repetitive output stopped."
    assert p["cleanup_error"] is False


def test_openclaw_parse_survives_non_json(la):
    p = la.ADAPTERS["openclaw"].parse("Error: something broke", "trace")
    assert p["final"] == ""
    assert p["agent_error"].startswith("unparsed output")


def _ctx(la, tmp_path, message="line one\nline two"):
    return la.RunCtx(
        model="stock-qwen3-4b",
        workdir=tmp_path / "work",
        message=message,
        message_file=tmp_path / "msg.txt",
        endpoint="http://127.0.0.1:11436",
        home=tmp_path / "home",
        tools=tmp_path / "tools",
        timeout_s=300,
        max_turns=30,
    )


@pytest.mark.parametrize("name", ["openclaw", "goose", "opencode", "interpreter"])
def test_no_adapter_puts_a_multiline_prompt_on_a_cmd_shim(la, tmp_path, name):
    """A .cmd shim cuts an argument at its first newline (found 2026-11-27: OpenClaw got only the
    first line of the request). Every adapter passes the message through a file, or runs a real
    executable that receives the whole command line."""
    argv, env = la.ADAPTERS[name].build(_ctx(la, tmp_path))
    if argv[0].lower().endswith((".cmd", ".bat")):
        assert not any("\n" in a for a in argv), argv


@pytest.mark.parametrize("name", ["openclaw", "goose", "opencode", "interpreter"])
def test_every_adapter_points_at_the_proxy_and_its_own_home(la, tmp_path, name):
    ctx = _ctx(la, tmp_path)
    argv, env = la.ADAPTERS[name].build(ctx)
    flat = " ".join(argv) + " " + " ".join(f"{k}={v}" for k, v in env.items())
    for cfg in ctx.home.rglob("*"):
        if cfg.is_file():
            flat += " " + cfg.read_text(encoding="utf-8")
    assert "127.0.0.1:11436" in flat, name
    assert "11435" not in flat, name  # never straight to the server: the proxy records the run
    assert ctx.model in flat, name
    assert env.get("USERPROFILE") == str(ctx.home)
    assert env.get("HOME") == str(ctx.home)


def test_openclaw_argv(la, tmp_path):
    ctx = _ctx(la, tmp_path)
    argv, env = la.ADAPTERS["openclaw"].build(ctx)
    assert argv[1:4] == ["agent", "exec", "--isolated"]
    assert "--message-file" in argv
    assert ctx.message_file.read_text(encoding="utf-8") == ctx.message
    assert argv[argv.index("--thinking") + 1] == "off"
    assert argv[argv.index("--model") + 1] == "ollama/stock-qwen3-4b"
    assert env["OPENCLAW_STATE_DIR"] == str(ctx.home / ".openclaw")


# ── the proxy ────────────────────────────────────────────────────────────────


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        out = json.dumps({"echo": json.loads(body)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _serve(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def _post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


def test_proxy_control_only_from_this_machine(proxy):
    """Only the harness on this machine may switch the proxy's log or the turn limit; nothing reaching
    it over a network can."""
    assert proxy.control_allowed("127.0.0.1") is True
    assert proxy.control_allowed("::1") is True
    assert proxy.control_allowed("172.23.0.5") is False


def test_proxy_writes_the_record_before_the_reply_completes(proxy, tmp_path):
    """CI on Linux read the run's log the moment the 429 arrived and found it missing
    (2026-11-27): the proxy answered first and wrote second. A client that has its reply,
    refused or forwarded, must find the record already there, however slow the write."""
    import time as _time

    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    _serve(up)

    class SlowState(proxy._State):
        def write(self, record):
            _time.sleep(0.3)
            super().write(record)

    state = SlowState(("127.0.0.1", up.server_address[1]))
    px = ThreadingHTTPServer(("127.0.0.1", 0), proxy.make_handler(state))
    _serve(px)
    base = f"http://127.0.0.1:{px.server_address[1]}"
    try:
        log = tmp_path / "run.jsonl"
        _post(base + "/_proxy/log", {"path": str(log), "max_requests": 1})
        _post(base + "/api/chat", {"n": 1})
        assert len(log.read_text(encoding="utf-8").splitlines()) == 1
        with pytest.raises(urllib.error.HTTPError):
            _post(base + "/api/chat", {"n": 2})
        assert len(log.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        px.shutdown()
        up.shutdown()


def test_proxy_records_and_limits_turns(proxy, tmp_path):
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    _serve(up)
    state = proxy._State(("127.0.0.1", up.server_address[1]))
    px = ThreadingHTTPServer(("127.0.0.1", 0), proxy.make_handler(state))
    _serve(px)
    base = f"http://127.0.0.1:{px.server_address[1]}"
    try:
        log = tmp_path / "run.jsonl"
        _post(base + "/_proxy/log", {"path": str(log), "max_requests": 2})
        assert _post(base + "/api/chat", {"n": 1}) == (200, {"echo": {"n": 1}})
        assert _post(base + "/v1/chat/completions", {"n": 2})[0] == 200
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base + "/api/chat", {"n": 3})
        assert e.value.code == 429
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert [r["status"] for r in rows] == [200, 200, 429]
        assert rows[0]["request"] == {"n": 1}
        assert json.loads(rows[0]["response"]) == {"echo": {"n": 1}}
        # a new run's log starts its own count
        _post(base + "/_proxy/log", {"path": str(tmp_path / "next.jsonl"), "max_requests": 2})
        assert _post(base + "/api/chat", {"n": 4})[0] == 200
    finally:
        px.shutdown()
        up.shutdown()
