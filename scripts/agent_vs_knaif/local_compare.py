"""knaif 1.4.0 against four general agents, every arm on the same local model.

Plan and rules: docs/plans/2026-11-25-local-agents-comparison.md. The 2026-10-01 harness
(`compare.py`) is kept as it was; this one reuses its guard, scrubbing and ffprobe checks.

- Arms: knaif (the installed release, through its daemon) and OpenClaw, Goose, OpenCode and Open
  Interpreter (`local_agents.py`), each on the stock Qwen3-4B and on `knaif-qwen3-4b-v2`.
- The agents reach one Ollama server (CUDA, thinking off, context 32768) through a recording
  proxy (`model_proxy.py`) that keeps every model call of a run and enforces the turn limit.
- Tokens and model calls of an agent run come from the server's own log; knaif's from its
  `KNAIF_TIMING` lines, measured once per request and model before the run (its plan is
  deterministic), because a run through the daemon does not print them.
- Order: each round, each request; within a request the stock-model arms, then the v2 arms; the
  model switch (Ollama and the knaif daemon) happens before a group, outside every timing.
- Every run in a fresh folder outside the checkout; the checkout is checked unchanged after
  every agent run; local paths and the machine's name are scrubbed from everything saved.

    uv run --no-sync python scripts/agent_vs_knaif/local_compare.py --e2e
    uv run --no-sync python scripts/agent_vs_knaif/local_compare.py --pilot --save evals/runs/<d>_local-agents-pilot_ffprobe
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from compare import guard_diff, parse_native, scrub, snapshot  # noqa: E402
from local_agents import ADAPTERS, INSTRUCTIONS, RunCtx, agent_path  # noqa: E402
from run import check, ffprobe  # noqa: E402

TOOLS = REPO.parent / "knaif-local-agents"
SERVER = "http://127.0.0.1:11435"  # the comparison's own Ollama (ollama-serve.sh)
PROXY = "http://127.0.0.1:11436"

#: (arm id, agent, server model name, knaif --model value). Owner, 2026-11-25.
MODELS = {
    "stock": ("stock-qwen3-4b", str(REPO / "models" / "Qwen3-4B-Q4_K_M.gguf")),
    "v2": ("knaif-qwen3-4b-v2", "knaif-qwen3-4b-v2"),
    # Round 2 (owner, 2026-11-27): the agents at temperature 0, knaif's own decoding. The same
    # weights; only the server-side default temperature differs (no agent sends one).
    "stock-t0": ("stock-qwen3-4b-t0", str(REPO / "models" / "Qwen3-4B-Q4_K_M.gguf")),
}
AGENTS = ["knaif", "openclaw", "goose", "opencode", "interpreter"]

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".opus"}
DOC_EXT = {".pdf", ".txt", ".md"}
_DONE_EVENT = re.compile(r"task\.n_tokens = (\d+)")
_PROMPT_EVAL = re.compile(r"prompt eval time =\s*[\d.]+ ms /\s*(\d+) tokens")
_GEN_EVAL = re.compile(r"\|\s+eval time =\s*[\d.]+ ms /\s*(\d+) tokens")


def load_scenarios(path: Path | None = None) -> list[dict]:
    p = path or HERE / "local_scenarios.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))["scenarios"]


# ── measuring ────────────────────────────────────────────────────────────────


def parse_server_log(text: str) -> dict:
    """Model calls and tokens in a stretch of the Ollama log (llama.cpp's per-slot lines)."""
    prompts = [int(m.group(1)) for m in _DONE_EVENT.finditer(text)]
    return {
        "model_requests": len(prompts),
        "prompt_tokens": sum(prompts),
        "new_prompt_tokens": sum(int(m.group(1)) for m in _PROMPT_EVAL.finditer(text)),
        "gen_tokens": sum(int(m.group(1)) for m in _GEN_EVAL.finditer(text)),
        "max_prompt_tokens": max(prompts) if prompts else 0,
        "truncated": "truncated = 1" in text,
    }


#: A tool call written into the reply's text instead of the API's tool-call field: Qwen's own
#: `<tool_call>` tag, a bare `{"name": …, "arguments": …}` object, or `[tool_call: …]` (seen with
#: Open Interpreter's Qwen Code prompt, 2026-11-27).
_TOOL_TEXT = re.compile(
    r"<tool_call>|\"name\"\s*:\s*\"[\w.-]+\"\s*,\s*\"arguments\"|\[tool_call:", re.IGNORECASE
)


def read_model_log(path: Path) -> list[dict]:
    """The proxy's records of one run. Split on "\\n" only: `splitlines()` also breaks at
    U+0085, U+2028 and other Unicode line breaks, which a model's text can hold raw."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def _reply_of(rec: dict) -> tuple[str, list[str]]:
    """The model's text and tool-call names from one recorded reply (NDJSON, SSE or JSON)."""
    text, names = [], []
    by_index: dict[int, str] = {}
    for raw in rec.get("response", "").split("\n"):  # not splitlines(): see read_model_log
        line = raw.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if "message" in j:  # Ollama /api/chat
            m = j["message"] or {}
            text.append(m.get("content") or "")
            names += [
                (c.get("function") or {}).get("name") or "?" for c in m.get("tool_calls") or []
            ]
        for ch in j.get("choices") or []:  # OpenAI chat completions, streamed or not
            d = ch.get("delta") or ch.get("message") or {}
            text.append(d.get("content") or "")
            for c in d.get("tool_calls") or []:
                name = (c.get("function") or {}).get("name")
                if name:
                    by_index[c.get("index", len(by_index))] = name
        for item in j.get("output") or []:  # OpenAI responses API
            if item.get("type") == "function_call":
                names.append(item.get("name") or "?")
    return "".join(text), names + list(by_index.values())


def summarize_model_log(records: list[dict]) -> dict:
    chat = [r for r in records if str(r.get("path", "")).startswith(("/api/chat", "/v1/"))]
    names: collections.Counter = collections.Counter()
    format_errors = 0
    for r in chat:
        if r.get("status") != 200:
            continue
        text, calls = _reply_of(r)
        names.update(calls)
        if not calls and _TOOL_TEXT.search(text):
            format_errors += 1
    return {
        "chat_requests": len(chat),
        "tool_calls": sum(names.values()),
        "tool_names": dict(names),
        "format_errors": format_errors,
        "limited": sum(1 for r in chat if r.get("status") == 429),
    }


class GpuPeak:
    """Peak GPU memory in use while a run lasts (nvidia-smi every half second)."""

    def __init__(self):
        self.peak = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _read(self) -> int | None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            return int(out.strip().splitlines()[0])
        except (ValueError, IndexError, OSError, subprocess.SubprocessError):
            return None

    def _loop(self):
        while not self._stop.is_set():
            v = self._read()
            if v:
                self.peak = max(self.peak, v)
            self._stop.wait(0.5)

    def __enter__(self):
        self.start_mib = self._read()
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(5)


# ── grading ──────────────────────────────────────────────────────────────────


def file_hash(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def has_question(text: str) -> bool:
    return bool(text) and ("?" in text or "？" in text)


def _new_files(d: Path, fixture: str, exts: set[str] | None = None) -> list[Path]:
    out = []
    for p in d.iterdir():
        if not p.is_file() or p.name == fixture or p.name.startswith("."):
            continue
        if exts is None or p.suffix.lower() in exts:
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def snapshot_inputs(d: Path) -> dict[str, str | None]:
    """sha256 of every file the run starts with; the originals must all be there, unchanged."""
    return {p.name: file_hash(p) for p in d.iterdir() if p.is_file()}


def inputs_intact(d: Path, before: dict[str, str | None] | str | None, fixture: str) -> bool:
    if isinstance(before, dict):
        return all(file_hash(d / name) == h for name, h in before.items())
    return file_hash(d / fixture) == before


def _ff_frame(src: Path, out: Path, t: float | None = None, index: int | None = None, vf=None):
    """One frame of `src` as a PNG: at time `t`, or frame number `index`, after filter `vf`."""
    args = ["ffmpeg", "-v", "error", "-y"]
    if t is not None:
        args += ["-ss", f"{t:.3f}"]
    args += ["-i", str(src)]
    filters = [f"select=eq(n\\,{index})"] if index is not None else []
    if vf:
        filters.append(vf)
    if filters:
        args += ["-vf", ",".join(filters)]
    args += ["-frames:v", "1", str(out)]
    subprocess.run(args, capture_output=True, timeout=120)
    return out if out.exists() else None


def ssim(a: Path | None, b: Path | None, size: tuple[int, int] = (320, 180)) -> float:
    """Structural similarity of two images, both scaled to `size` and grey; 0 if either is missing."""
    if not a or not b:
        return 0.0
    w, h = size
    graph = f"[0:v]scale={w}:{h},format=gray[a];[1:v]scale={w}:{h},format=gray[b];[a][b]ssim"
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(a), "-i", str(b), "-lavfi", graph, "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    m = re.search(r"All:([\d.]+)", r.stderr)
    return float(m.group(1)) if m else 0.0


def _frame_count(p: Path) -> int | None:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames,width,height", "-of", "json", str(p)],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    try:
        return int(json.loads(r.stdout)["streams"][0]["nb_read_frames"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _dims(p: Path) -> tuple[int, int] | None:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(p)],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    try:
        s = json.loads(r.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


MEDIA_EXT = VIDEO_EXT | {".gif", ".png", ".jpg", ".jpeg", ".webp"}
_RUN_CHECK_KEYS = (
    "container", "smaller", "acodec", "no_video", "no_audio", "vcodec", "height_max", "dur_approx",
)  # fmt: skip


def grade_video(expect: dict, d: Path, fixture: str, src: Path) -> dict:
    """The 10-01 ffprobe checks (`run.check`), plus the stricter ones of the harder requests:
    `name` (the file must have the requested name), `dur_tol`, `has_audio`, `frames`,
    `frame_like` (similar to a given source frame), `starts_at` (the first frame is the source's
    frame at that time, not half a second either side), `content_at` (a frame of the output is a
    given source moment, not another) and `rotated` (closer to the source turned the asked way
    than turned the other way). Frames are compared by SSIM on grey 320x180 copies."""
    if expect.get("name"):
        out = d / expect["name"]
        if not out.is_file():
            return {"result": "no-output", "produced": None, "probe": None, "failed": ["name"]}
    else:
        files = _new_files(d, fixture, MEDIA_EXT)
        out = files[-1] if files else None
    probe = ffprobe(out)
    if not probe:
        return {"result": "no-output", "produced": out.name if out else None, "probe": None}
    frame_checks = ("frames", "frame_like", "starts_at", "content_at", "rotated")
    if any(k in expect for k in frame_checks) and not probe.get("has_v"):
        return {
            "result": "FAIL",
            "produced": out.name,
            "probe": probe,
            "failed": ["no video stream"],
        }
    basic = {k: v for k, v in expect.items() if k in _RUN_CHECK_KEYS}
    failed: list[str] = []
    if check(basic, probe, src.stat().st_size) != "PASS":
        failed.append("basic")
    if (
        "dur_tol" in expect
        and abs((probe.get("dur") or 0) - expect["dur_approx"]) > expect["dur_tol"]
    ):
        failed.append(f"duration {probe.get('dur')}")
    if expect.get("has_audio") and not probe.get("has_a"):
        failed.append("no audio")
    if "frames" in expect and _frame_count(out) != expect["frames"]:
        failed.append(f"frames {_frame_count(out)}")
    tmp = d.parent / f"{d.name}.frames"
    tmp.mkdir(exist_ok=True)
    try:
        if "frame_like" in expect:
            # Neighbouring frames are near-identical (SSIM 0.998), and a GIF's palette lowers SSIM
            # on its own, so this checks "a frame from the start": closer to the asked frame than
            # to the source at any whole second after it.
            got = _ff_frame(out, tmp / "out0.png", index=0)
            ref = ssim(
                got, _ff_frame(src, tmp / "ref.png", index=expect["frame_like"]["src_index"])
            )
            dur = (ffprobe(src) or {}).get("dur") or 0
            others = [
                ssim(got, _ff_frame(src, tmp / f"at{s}.png", t=float(s)))
                for s in range(1, max(int(dur), 2))
            ]
            if not (ref >= 0.6 and ref > max(others)):
                failed.append(f"frame_like {ref:.3f} vs best other {max(others):.3f}")
        if "starts_at" in expect:
            t = float(expect["starts_at"])
            got = _ff_frame(out, tmp / "first.png", index=0)
            here = ssim(got, _ff_frame(src, tmp / "at.png", t=t))
            early = ssim(got, _ff_frame(src, tmp / "early.png", t=max(t - 0.5, 0)))
            late = ssim(got, _ff_frame(src, tmp / "late.png", t=t + 0.5))
            if not here > max(early, late):
                failed.append(f"starts_at {here:.3f} vs {early:.3f}/{late:.3f}")
        if "content_at" in expect:
            c = expect["content_at"]
            got = _ff_frame(out, tmp / "c_out.png", t=c["out_t"])
            yes = ssim(got, _ff_frame(src, tmp / "c_yes.png", t=c["src_t"]))
            no = ssim(got, _ff_frame(src, tmp / "c_no.png", t=c["not_src_t"]))
            if not yes > no:
                failed.append(f"content_at {yes:.3f} vs {no:.3f}")
        if "rotated" in expect:
            failed += _check_rotation(expect["rotated"], out, src, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {
        "result": "FAIL" if failed else "PASS",
        "produced": out.name,
        "probe": probe,
        "failed": failed,
    }


def _rotated_canvas(dims: tuple[int, int], rad: float) -> tuple[float, float]:
    """The size of the canvas that holds a `dims` frame turned by `rad` (ffmpeg's rotw/roth)."""
    import math  # noqa: PLC0415

    w, h = dims
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    return w * c + h * s, w * s + h * c


def _check_rotation(spec: dict, out: Path, src: Path, tmp: Path) -> list[str]:
    """The output's frame at 1 s must look like the source turned the asked way (on its own canvas
    or on an enlarged one, whichever fits the output's shape) clearly more than turned the other
    way. The rotate filter's positive angle is clockwise."""
    rad = float(spec["degrees"]) * 3.141592653589793 / 180
    sign = -1 if spec["direction"] == "ccw" else 1
    dims = _dims(out)
    sdims = _dims(src)
    if not dims or not sdims:
        return ["rotated: no frame"]
    got = _ff_frame(out, tmp / "r_out.png", t=1.0)
    # The enlarged canvas fits the whole turned frame: square for 45° of a 16:9 source, the
    # transposed shape (9:16) for 90°. Choose the reference whose shape the output is closer to.
    big_dims = _rotated_canvas(sdims, rad)
    expanded_aspect = big_dims[0] / big_dims[1]
    same_aspect = sdims[0] / sdims[1]
    aspect = dims[0] / dims[1]
    big = abs(aspect - expanded_aspect) < abs(aspect - same_aspect)

    def ref(s: int, name: str) -> Path | None:
        a = f"{s * rad:.6f}"
        vf = f"rotate={a}:ow=rotw({a}):oh=roth({a})" if big else f"rotate={a}"
        return _ff_frame(src, tmp / name, t=1.0, vf=vf)

    right = ssim(got, ref(sign, "r_right.png"))
    wrong = ssim(got, ref(-sign, "r_wrong.png"))
    if right >= 0.5 and right > wrong + 0.05:
        return []
    return [f"rotated {right:.3f} vs other way {wrong:.3f}"]


def _pair(inputs: list[str], new: list[Path], ext: str) -> dict[str, Path | None]:
    """Each input's result: a new file with the wanted extension whose name holds the input's."""
    out: dict[str, Path | None] = {}
    for name in inputs:
        stem = Path(name).stem.lower()
        hits = [p for p in new if p.suffix.lower() == ext and stem in p.stem.lower()]
        out[name] = hits[-1] if hits else None
    return out


def grade_batch_video(spec: dict, d: Path, before: dict[str, str | None]) -> dict:
    """Every input with one of `from_ext` must have a new `.<to>` file of about its length; every
    original must still be there, unchanged."""
    new = [p for p in _new_files(d, "", MEDIA_EXT) if p.name not in before]
    todo = [n for n in before if Path(n).suffix.lower() in spec["from_ext"]]
    pairs = _pair(todo, new, f".{spec['to']}")
    matched, failed = [], []
    for name, out in pairs.items():
        probe = ffprobe(out)
        src = ffprobe(d / name)
        if not probe or not probe.get("has_v"):
            failed.append(f"{name}: no result")
        elif src and abs(probe["dur"] - src["dur"]) > 0.5:
            failed.append(f"{name}: {probe['dur']}s vs {src['dur']}s")
        else:
            matched.append(f"{name} -> {out.name}")
    intact = inputs_intact(d, before, "")
    return {
        "result": "PASS" if not failed and intact else "FAIL",
        "matched": matched,
        "failed": failed,
        "produced": [p.name for p in new],
        "source_intact": intact,
    }


def _check_pdf(path: Path, criteria: dict, src: Path) -> tuple[list[str], list[str]]:
    v = _documents_checks()
    matched: list[str] = []
    failed: list[str] = []
    v._artifact_checks(path, criteria, path.parent, matched, failed)
    try:
        from pypdf import PdfReader  # noqa: PLC0415

        same = len(PdfReader(str(path)).pages) == len(PdfReader(str(src)).pages)
    except Exception:  # noqa: BLE001
        same = False
    (matched if same else failed).append("pages as the source" if same else "page count")
    return matched, failed


def grade_batch_documents(criteria: dict, d: Path, before: dict[str, str | None]) -> dict:
    """Every input PDF must have a new PDF (its name holding the input's) passing `criteria`, with
    the source's page count; every original must still be there, unchanged."""
    new = [p for p in _new_files(d, "", {".pdf"}) if p.name not in before]
    todo = [n for n in before if n.lower().endswith(".pdf")]
    matched, failed = [], []
    for name, out in _pair(todo, new, ".pdf").items():
        if out is None:
            failed.append(f"{name}: no result")
            continue
        m, f = _check_pdf(out, criteria, d / name)
        matched += [f"{name}: {x}" for x in m]
        failed += [f"{name}: {x}" for x in f]
    intact = inputs_intact(d, before, "")
    return {
        "result": "PASS" if not failed and intact else "FAIL",
        "matched": matched,
        "failed": failed,
        "produced": [p.name for p in new],
        "source_intact": intact,
    }


def _documents_checks():
    sys.path.insert(0, str(REPO / "python" / "core"))
    spec_path = REPO / "skills" / "documents" / "eval" / "verifiers.py"
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("documents_verifiers", spec_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def grade_documents(criteria: dict, d: Path, fixture: str) -> dict:
    """The documents skill's own file checks (`_artifact_checks`), and the page count read from
    the produced PDF; on the newest new PDF/text file, so a helper script is never graded."""
    files = _new_files(d, fixture, DOC_EXT)
    newest = files[-1] if files else None
    if newest is None:
        return {"result": "no-output", "produced": None, "matched": [], "failed": []}
    v = _documents_checks()
    matched: list[str] = []
    failed: list[str] = []
    v._artifact_checks(newest, criteria, d, matched, failed)
    if "pages" in criteria:
        try:
            from pypdf import PdfReader  # noqa: PLC0415

            n = len(PdfReader(str(newest)).pages)
        except Exception:  # noqa: BLE001
            n = None
        (matched if n == criteria["pages"] else failed).append(
            f"pages={criteria['pages']}" if n == criteria["pages"] else f"pages: got {n}"
        )
    return {
        "result": "PASS" if matched and not failed else "FAIL",
        "produced": newest.name,
        "matched": matched,
        "failed": failed,
    }


def grade_behavior(
    behavior: str, d: Path, fixture: str, before: dict | str | None, final: str
) -> dict:
    known = set(before) if isinstance(before, dict) else {fixture}
    produced = [p.name for p in _new_files(d, fixture, MEDIA_EXT | DOC_EXT) if p.name not in known]
    intact = inputs_intact(d, before, fixture)
    asked = has_question(final)
    if behavior == "clarify":
        ok = asked and not produced and intact
    else:  # reject: the source must still be there, unchanged
        ok = intact
    return {
        "result": "PASS" if ok else "FAIL",
        "asked": asked,
        "source_intact": intact,
        "produced": produced,
    }


def failure_cause(row: dict) -> str | None:
    """Why a run failed, in the plan's words; None when it passed."""
    if row.get("result") == "PASS":
        return None
    if row.get("source_intact") is False:
        return "source damaged"
    if row.get("limit_hit"):
        return "limit hit"
    made_nothing = row.get("result") == "no-output" or row.get("produced") == []
    if made_nothing and row.get("format_errors"):
        return "tool-call format"
    if row.get("result") == "no-output":
        return "no result"
    return "wrong result"


# ── running ──────────────────────────────────────────────────────────────────


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, 9)
        except OSError:
            pass


def run_process(
    argv, cwd: Path, env: dict, timeout: int
) -> tuple[int | None, float, str, str, bool]:
    t0 = time.perf_counter()
    p = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(p.pid)
        out, err = p.communicate()
    return p.returncode, time.perf_counter() - t0, out or "", err or "", timed_out


def _post(url: str, payload: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def warm_server(model: str) -> None:
    """Load `model` in Ollama before a group, outside every timing (one tiny call)."""
    _post(
        f"{SERVER}/api/chat",
        {"model": model, "messages": [{"role": "user", "content": "hi"}], "stream": False},
    )


def knaif_exe() -> str:
    found = shutil.which("knaif")
    if not found:
        sys.exit("the released knaif is not on PATH")
    return found


def daemon(model_arg: str | None) -> None:
    exe = knaif_exe()
    subprocess.run([exe, "daemon", "stop"], capture_output=True)
    if model_arg:
        r = subprocess.run(
            [exe, "daemon", "start", "--model", model_arg, "--idle-minutes", "120"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            sys.exit(f"knaif daemon did not start: {r.stderr or r.stdout}")


def fixture_path(s: dict) -> Path:
    return REPO / "sandbox" / "fixtures" / s["skill"] / s["fixture"]


def place_fixture(s: dict, d: Path) -> dict[str, str | None]:
    """Copy the request's file, or the listed files of its folder (`fixture: <folder>/`, `files`),
    into the run folder; return the sha256 of each, which must all be unchanged at the end."""
    src = fixture_path(s)
    if s["fixture"].endswith("/"):
        names = s.get("files") or sorted(p.name for p in src.iterdir() if p.is_file())
        for name in names:
            shutil.copy(src / name, d / name)
    else:
        shutil.copy(src, d / s["fixture"])
    return snapshot_inputs(d)


def knaif_tokens(s: dict, model_arg: str, work: Path) -> dict:
    """knaif's tokens for this request and model: an untimed dry run without the daemon."""
    d = work / "tokens"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    place_fixture(s, d)
    env = {**os.environ, "KNAIF_TIMING": "1"}
    _, _, out, err, _ = run_process(
        [knaif_exe(), "run", s["skill"], s["utterance"], "--dry-run", "--model", model_arg],
        d,
        env,
        300,
    )
    text = out + "\n" + err
    pt = re.search(r"prompt_decode \((\d+) tokens", text)
    gt = re.search(r"generation \((\d+) tokens\)", text)
    return {
        "prompt_tokens": int(pt.group(1)) if pt else None,
        "gen_tokens": int(gt.group(1)) if gt else None,
    }


def run_knaif(s: dict, model_arg: str, d: Path, timeout: int) -> dict:
    exe = knaif_exe()
    with GpuPeak() as gpu:
        code, wall, out, err, timed_out = run_process(
            [exe, "run", s["skill"], s["utterance"], "--yes", "--model", model_arg],
            d,
            dict(os.environ),
            timeout,
        )
    lines = [(0.0, ln) for ln in (out + "\n" + err).splitlines()]
    outcome = parse_native(lines, code if code is not None else 1)["outcome"]
    text = "\n".join(ln for _, ln in lines)
    return {
        "exit": code,
        "wall_s": round(wall, 3),
        "limit_hit": "time" if timed_out else None,
        "knaif_outcome": outcome,
        "final": text[-600:],
        "peak_gpu_mib": gpu.peak,
        "gpu_before_mib": gpu.start_mib,
    }


def run_agent(name: str, model: str, s: dict, d: Path, a: argparse.Namespace, logs: Path) -> dict:
    adapter = ADAPTERS[name]
    home = d.parent / f"{d.name}.home"
    shutil.rmtree(home, ignore_errors=True)
    ctx = RunCtx(
        model=model,
        workdir=d,
        message=INSTRUCTIONS.replace("{request}", s["utterance"]),
        message_file=d.parent / f"{d.name}.msg.txt",
        endpoint=PROXY,
        home=home,
        tools=TOOLS,
        timeout_s=a.timeout,
        max_turns=a.max_turns,
    )
    argv, env_extra = adapter.build(ctx)
    env = {**os.environ, **env_extra, "PATH": agent_path(TOOLS, Path(a.ffmpeg_dir))}
    model_log = logs / f"{d.parent.name}-{d.name}.model.jsonl"
    _post(f"{PROXY}/_proxy/log", {"path": str(model_log), "max_requests": a.max_turns})
    server_log = Path(a.server_log)
    start = server_log.stat().st_size
    with GpuPeak() as gpu:
        code, wall, out, err, timed_out = run_process(argv, d, env, a.timeout + 30)
    _post(f"{PROXY}/_proxy/log", {"path": None})
    with server_log.open("rb") as f:
        f.seek(start)
        served = f.read().decode("utf-8", "replace")
    records = read_model_log(model_log)
    parsed = adapter.parse(out, err)
    summary = summarize_model_log(records)
    limit = "time" if timed_out else ("turns" if summary["limited"] else None)
    (d.parent / f"{d.name}.stdout.txt").write_text(out, encoding="utf-8")
    (d.parent / f"{d.name}.stderr.txt").write_text(err, encoding="utf-8")
    return {
        "exit": code,
        "wall_s": round(wall, 3),
        "limit_hit": limit,
        **parse_server_log(served),
        **summary,
        **parsed,
        "peak_gpu_mib": gpu.peak,
        "gpu_before_mib": gpu.start_mib,
    }


def grade(s: dict, d: Path, before: dict[str, str | None], final: str) -> dict:
    fx = s["fixture"]
    intact = inputs_intact(d, before, fx)
    if s.get("behavior"):
        return grade_behavior(s["behavior"], d, fx, before, final)
    if s.get("batch"):
        return grade_batch_video(s["batch"], d, before)
    if s.get("batch_criteria"):
        return grade_batch_documents(s["batch_criteria"], d, before)
    if s["skill"] == "documents":
        g = grade_documents(s["criteria"], d, fx)
    else:
        g = grade_video(s.get("expect", {}), d, fx, fixture_path(s))
    g["source_intact"] = intact
    if not intact:
        g["result"] = "FAIL"
    return g


def versions() -> dict:
    def first(argv):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            return ((r.stdout or r.stderr).strip().splitlines() or [""])[0]
        except Exception as e:  # noqa: BLE001
            return f"unavailable: {e.__class__.__name__}"

    exe = knaif_exe()
    out = {
        "knaif": first([exe, "--version"]),
        "knaif_exe_sha256": hashlib.sha256(Path(exe).read_bytes()).hexdigest(),
        "ollama": first(["ollama", "--version"]),
        "ffmpeg": first(["ffmpeg", "-version"]),
        "gpu": first(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"]),
    }
    for name, adapter in ADAPTERS.items():
        out[name] = first(adapter.version_argv(TOOLS))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-root", default=str(REPO.parent / "knaif-local-agents-runs"))
    ap.add_argument("--save", default=None, help="evals/runs/<dir> for the record")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--first-round", type=int, default=1, help="number of the first round run")
    ap.add_argument("--scenarios", default="", help="comma-separated scenario ids (default: all)")
    ap.add_argument("--agents", default=",".join(AGENTS))
    ap.add_argument("--models", default="stock,v2")
    ap.add_argument("--timeout", type=int, default=300, help="seconds per agent run")
    ap.add_argument("--max-turns", type=int, default=30, help="model calls per agent run")
    ap.add_argument("--server-log", default=str(TOOLS / "logs" / "ollama-serve.log"))
    ap.add_argument(
        "--ffmpeg-dir", default=str(Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin")
    )
    ap.add_argument("--e2e", action="store_true", help="one agent, one request, one round")
    ap.add_argument("--pilot", action="store_true", help="three requests, one round, every arm")
    ap.add_argument(
        "--scenarios-file",
        default=None,
        help="another request set, e.g. local_tuning.yaml for the tuning pass",
    )
    a = ap.parse_args()

    scen = load_scenarios(Path(a.scenarios_file) if a.scenarios_file else None)
    agents = [x for x in a.agents.split(",") if x]
    models = [x for x in a.models.split(",") if x]
    rounds = a.rounds
    if a.e2e:
        scen, agents, models, rounds = scen[:1], agents[1:2] or ["openclaw"], models[:1], 1
    elif a.pilot:
        ids = {"v-convert-en", "d-split-en", "b-delete-ru"}
        scen, rounds = [s for s in scen if s["id"] in ids], 1
    if a.scenarios:
        scen = [s for s in scen if s["id"] in a.scenarios.split(",")]

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(a.run_root) / stamp
    if REPO in root.resolve().parents:
        sys.exit("the run root must be outside the checkout")
    logs = root / "model-logs"
    logs.mkdir(parents=True)
    scrub_roots = [root, TOOLS, Path.home()]
    host = socket.gethostname()

    def clean(text: str) -> str:
        out = scrub(text or "", scrub_roots)
        return re.sub(re.escape(host), "<host>", out, flags=re.IGNORECASE) if host else out

    save = Path(a.save) if a.save else root
    save.mkdir(parents=True, exist_ok=True)
    meta = {
        "started": stamp,
        "rounds": rounds,
        "agents": agents,
        "models": {m: MODELS[m][0] for m in models},
        "scenarios": [s["id"] for s in scen],
        "timeout_s": a.timeout,
        "max_turns": a.max_turns,
        "context": 32768,
        "instructions": INSTRUCTIONS,
        "versions": {k: clean(v) for k, v in versions().items()},
    }
    (save / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), "utf-8")

    tokens: dict[tuple[str, str], dict] = {}
    if "knaif" in agents:
        daemon(None)
        for s in scen:
            for m in models:
                tokens[(s["id"], m)] = knaif_tokens(s, MODELS[m][1], root)

    total = rounds * len(scen) * len(models) * len(agents)
    n = 0
    for rnd in range(a.first_round, a.first_round + rounds):
        for s in scen:
            for m in models:
                server_model, knaif_model = MODELS[m]
                if any(x != "knaif" for x in agents):
                    warm_server(server_model)
                if "knaif" in agents:
                    daemon(knaif_model)
                for agent in agents:
                    n += 1
                    arm = f"{agent}-{m}"
                    d = root / arm / f"r{rnd}-{s['id']}"
                    d.mkdir(parents=True)
                    before = place_fixture(s, d)
                    if agent == "knaif":
                        r = run_knaif(s, knaif_model, d, a.timeout)
                        r.update(tokens.get((s["id"], m), {}))
                    else:
                        snap = snapshot(REPO)
                        r = run_agent(agent, server_model, s, d, a, logs)
                        problems = guard_diff(snap, snapshot(REPO))
                        if problems:
                            print(f"GUARD: the checkout changed during {arm}: {problems}")
                            sys.exit(2)
                    g = grade(s, d, before, r.get("final") or "")
                    if agent == "knaif" and s.get("behavior"):
                        g["result"] = "PASS" if r["knaif_outcome"] == s["behavior"] else "FAIL"
                        g["asked"] = r["knaif_outcome"] == "clarify"
                    row = {
                        "round": rnd,
                        "scenario": s["id"],
                        "lang": s["lang"],
                        "skill": s["skill"],
                        "kind": s.get("behavior")
                        or ("documents" if s["skill"] == "documents" else "video"),
                        "arm": arm,
                        "agent": agent,
                        "model": server_model,
                        **r,
                        **g,
                    }
                    row["failure_cause"] = failure_cause(row)
                    row["final"] = clean(row.get("final") or "")[-600:]
                    if row.get("agent_error"):
                        row["agent_error"] = clean(str(row["agent_error"]))[:400]
                    with (save / "raw.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    secs = row.get("wall_s")
                    print(
                        f"[{n}/{total}] r{rnd} {s['id']:<13} {arm:<18} {row['result']:<9} "
                        f"{secs}s  calls={row.get('model_requests', '-')} "
                        f"cause={row['failure_cause'] or '-'}",
                        flush=True,
                    )
    daemon(None)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
