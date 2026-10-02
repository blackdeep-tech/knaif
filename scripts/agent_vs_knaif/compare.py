"""The 2026-10 rerun: native knaif (CUDA) against five premium-agent arms, 3 rounds.

Plan and rules: docs/plans/2026-10-01-llm-comparison-rerun.md. Differences from `run.py` (the
2026-07-02 harness, kept as it was):

- knaif is the **native binary** (`target/release-cuda/knaif.exe` by default), not the Python CLI,
  with `KNAIF_TIMING=1`; its table time is inference + ffmpeg, the model load recorded apart;
- six arms (knaif + model/effort variants of the three CLIs), each request run by every arm in a
  fixed order, then the next request, then the next round;
- every request runs in a fresh folder under a run root **beside the checkout** (one subtree per
  arm), and a guard checks the checkout is unchanged after every agent call;
- raw results go to one JSONL file under `evals/runs/`, with local paths scrubbed.

Cost is not computed here: tokens are saved raw and priced afterwards from the run day's official
API rates, so a pricing mistake can be fixed without re-running anything.

    uv run python scripts/agent_vs_knaif/compare.py --smoke          # one request per arm
    uv run python scripts/agent_vs_knaif/compare.py --save evals/runs/<date>_agent-vs-knaif-native_ffprobe
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents import AGENTS  # noqa: E402
from run import _out_file, _result_text, check, ffprobe  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

# (arm id, adapter or "knaif", model, effort) — owner, 2026-10-01.
ARMS: list[tuple[str, str, str, str | None]] = [
    ("knaif", "knaif", "knaif-qwen3-4b-v2", None),
    ("claude-opus-5.5", "claude", "claude-opus-5-5", "medium"),
    ("claude-sonnet-5.5", "claude", "claude-sonnet-5-5", "medium"),
    ("codex-gpt-6-astra", "codex", "gpt-6-astra", "medium"),
    ("codex-gpt-6.1-sol", "codex", "gpt-6.1-sol", "medium"),
    ("copilot-gpt-5.6-terra", "copilot", "gpt-5.6-terra", "medium"),
]

# The 2026-07-02 prompt, unchanged, so the agents are asked exactly what they were asked then.
PROMPT = (
    "You must use ffmpeg via the shell to accomplish exactly this request and nothing else. "
    "Input file: {fixture} (current directory). Request: {utt}. "
    "Write any output into the current directory, then stop."
)

_TIMING = {
    "load_ms": re.compile(r"load_from_file = (\d+) ms"),
    "prompt_ms": re.compile(r"prompt_decode \(\d+ tokens\) = (\d+) ms"),
    "gen_ms": re.compile(r"generation \(\d+ tokens\) = (\d+) ms"),
    "prompt_tok": re.compile(r"prompt_decode \((\d+) tokens\)"),
    "gen_tok": re.compile(r"generation \((\d+) tokens\)"),
}


def parse_native(lines: list[tuple[float, str]], exit_code: int) -> dict:
    """Outcome, timings and tokens from a plain-view native run's timestamped lines.

    ffmpeg time is measured from each `running:` line to the `✓`/`✗` line that closes it, summed
    over a chain. The table time is inference (prompt + generation) + ffmpeg: no model load.
    """
    r: dict = dict.fromkeys(_TIMING)
    outcome = None
    ffmpeg_s = 0.0
    started: float | None = None
    for t, text in lines:
        for key, pat in _TIMING.items():
            m = pat.search(text)
            if m:
                r[key] = int(m.group(1))
        if text.startswith("running: "):
            started = t
        elif text.startswith(("✓ ", "✗ ")):
            if started is not None:
                ffmpeg_s += t - started
                started = None
            if text.startswith("✗ "):
                outcome = "error"
        elif text.startswith("clarify: "):
            outcome = outcome or "clarify"
        elif text.startswith("reject: "):
            outcome = outcome or "reject"
    if outcome is None:
        outcome = "plan" if exit_code == 0 else "error"
    r["outcome"] = outcome
    r["ffmpeg_s"] = round(ffmpeg_s, 3)
    infer = ((r["prompt_ms"] or 0) + (r["gen_ms"] or 0)) / 1000
    r["infer_s"] = round(infer, 3)
    r["table_s"] = round(infer + ffmpeg_s, 3)
    return r


def scrub(text: str, roots: list[Path]) -> str:
    """Replace local paths in `text`: the run root becomes `<run>`, a home folder `~`."""
    out = text
    for i, root in enumerate(sorted(roots, key=lambda p: len(str(p)), reverse=True)):
        label = "<run>" if i == 0 else "~"
        for form in {str(root), str(root).replace("\\", "/"), str(root).replace("/", "\\")}:
            out = re.sub(re.escape(form), label, out, flags=re.IGNORECASE)
    # Anything still naming the user without its path goes too: the home folder's own name
    # (the username on Windows and Linux) and the account the run is under.
    names = {r.name for r in roots[1:]} | {
        os.environ.get("USERNAME", ""),
        os.environ.get("USER", ""),
    }
    for user in sorted(n for n in names if len(n) > 2):
        out = re.sub(rf"\b{re.escape(user)}\b", "<user>", out, flags=re.IGNORECASE)
    return out


def snapshot(repo: Path) -> dict:
    """What the guard compares: git status, and the ignored folders git cannot see."""
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    snap = {"status": status}
    for folder in ("dist", "models", "target"):
        d = repo / folder
        snap[folder] = (
            {p.name: p.stat().st_size for p in d.iterdir() if p.is_file()} if d.is_dir() else {}
        )
    return snap


def guard_diff(before: dict, after: dict) -> list[str]:
    problems = []
    for key in before:
        if before[key] == after.get(key):
            continue
        if key == "status":
            lines = set(before[key].splitlines()) ^ set(after.get(key, "").splitlines())
            problems.append(f"status changed: {sorted(lines)[:5]}")
        else:
            problems.append(f"{key} changed")
    return problems


GPU_BUSY_PCT = 10  # utilization above this, outside knaif's own runs, means someone else's work


def parse_gpu(text: str) -> tuple[int, int] | None:
    """`utilization %, memory MiB` from nvidia-smi's csv,noheader,nounits line."""
    try:
        util, mem = (int(x.strip()) for x in text.strip().splitlines()[0].split(","))
        return util, mem
    except (ValueError, IndexError):
        return None


def gpu_busy(samples: list[tuple[int, int]]) -> bool:
    """Busy if any sample's utilization is above the threshold. Memory is not used: the
    desktop and idle apps hold VRAM without loading the GPU."""
    return any(util > GPU_BUSY_PCT for util, _ in samples)


def gpu_samples(n: int = 3, gap_s: float = 0.5) -> list[tuple[int, int]]:
    out = []
    for i in range(n):
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
        )
        reading = parse_gpu(r.stdout)
        if reading:
            out.append(reading)
        if i < n - 1:
            time.sleep(gap_s)
    return out


def wait_for_idle_gpu(max_wait_s: int = 1800) -> list[tuple[int, int]]:
    """Block until the GPU is idle; stop the run after `max_wait_s` (knaif's timing would be
    wrong on a GPU someone else is using — owner, 2026-10-01)."""
    waited = 0.0
    while True:
        samples = gpu_samples()
        if not gpu_busy(samples):
            return samples
        if waited >= max_wait_s:
            print(f"GPU still busy after {max_wait_s} s ({samples}); stopping the run", flush=True)
            sys.exit(3)
        print(f"GPU busy {samples}; waiting", flush=True)
        time.sleep(30)
        waited += 30


def _timestamped(argv: list[str], cwd: Path, env: dict, timeout: int):
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
    lines: list[tuple[float, str]] = []

    def pump(stream):
        for line in stream:
            lines.append((time.perf_counter() - t0, line.rstrip("\n")))

    pumps = [threading.Thread(target=pump, args=(s,), daemon=True) for s in (p.stdout, p.stderr)]
    for th in pumps:
        th.start()
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    wall = time.perf_counter() - t0
    for th in pumps:
        th.join(5)
    lines.sort(key=lambda x: x[0])
    return p.returncode, wall, lines


def run_native(exe: Path, utt: str, fixture: str, fixdir: Path, d: Path) -> dict:
    d.mkdir(parents=True)
    shutil.copy(fixdir / fixture, d / fixture)
    env = {**os.environ, "KNAIF_TIMING": "1", "KNAIF_SKILLS_ROOT": str(REPO / "skills")}
    code, wall, lines = _timestamped(
        [str(exe), "run", "ffmpeg", utt, "--yes", "--model", "knaif-qwen3-4b-v2"], d, env, 300
    )
    r = parse_native(lines, code)
    r["wall_s"] = round(wall, 3)
    r["exit"] = code
    r["text"] = "\n".join(t for _, t in lines if not t.startswith("[knaif-timing]"))[-600:]
    return r


def run_agent(adapter: str, model: str, effort: str | None, utt: str, fixture: str, fixdir, d):
    agent = AGENTS[adapter]
    d.mkdir(parents=True)
    shutil.copy(fixdir / fixture, d / fixture)
    argv = agent.build_argv(PROMPT.format(fixture=fixture, utt=utt), model, effort)
    code, wall, lines = _timestamped(argv, d, dict(os.environ), 300)
    combined = "\n".join(t for _, t in lines)
    try:
        m = agent.parse(combined, wall)
    except Exception as e:  # a parse failure is recorded, never retried
        m = {"parse_error": str(e)}
    m["wall_s"] = round(wall, 3)
    m["exit"] = code
    m["text"] = " ".join((_result_text(adapter, combined) or "").split())[:400]
    return m


def versions(exe: Path) -> dict:
    def out(argv):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            return (r.stdout or r.stderr).strip().splitlines()[0]
        except Exception as e:
            return f"unavailable: {e.__class__.__name__}"

    from agents import _codex_bin  # noqa: PLC0415

    home = Path.home()
    return {
        "knaif_exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "knaif_build": exe.parent.name,
        "claude": out(["claude", "--version"]),
        "codex": out([_codex_bin(), "--version"]),
        "copilot": out(["copilot", "--version"]),
        "gpu_driver": out(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"]
        ),
        # Present or absent only, never their content (plan: user-level instructions).
        "user_instructions": {
            "~/.claude/CLAUDE.md": (home / ".claude" / "CLAUDE.md").exists(),
            "~/.codex/AGENTS.md": (home / ".codex" / "AGENTS.md").exists(),
            "~/.copilot/copilot-instructions.md": (
                home / ".copilot" / "copilot-instructions.md"
            ).exists(),
        },
    }


def grade(s: dict, d: Path, fixdir: Path) -> dict:
    """The 07-02 grading: ffprobe checks on the newest output, or the produced/deleted facts."""
    fixture = s["fixture"]
    of = _out_file(d, fixture)
    probe = ffprobe(of)
    g = {
        "produced": of.name if of else None,
        "input_deleted": not (d / fixture).exists(),
        "probe": probe,
    }
    if not s.get("behavior"):
        g["result"] = check(s.get("expect", {}), probe, (fixdir / fixture).stat().st_size)
    return g


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-root", default=str(REPO.parent / "knaif-llm-compare"))
    ap.add_argument("--knaif-exe", default=str(REPO / "target/release-cuda/knaif.exe"))
    ap.add_argument("--save", default=None, help="evals/runs/<dir>; default: no file, smoke only")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--smoke", action="store_true", help="first request only, one round")
    ap.add_argument("--arms", default="", help="comma-separated arm ids (default: all)")
    args = ap.parse_args()

    exe = Path(args.knaif_exe)
    fixdir = REPO / "sandbox/fixtures/ffmpeg"
    scen = yaml.safe_load((HERE / "scenarios.yaml").read_text(encoding="utf-8"))["scenarios"]
    arms = [a for a in ARMS if not args.arms or a[0] in args.arms.split(",")]
    rounds = 1 if args.smoke else args.repeats
    if args.smoke:
        scen = scen[:1]
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.run_root) / stamp
    if REPO in root.resolve().parents:
        sys.exit("the run root must be outside the checkout (plan: Safety of the run)")
    # The native binary looks for contracts/ in its working folder's parents.
    shutil.copytree(REPO / "contracts", root / "knaif" / "contracts")
    scrub_roots = [root, Path.home()]
    save = Path(args.save) if args.save else None
    if save:
        save.mkdir(parents=True, exist_ok=True)
        meta = {
            "started": stamp,
            "arms": [{"arm": a, "cli": c, "model": m, "effort": e} for a, c, m, e in arms],
            "rounds": rounds,
            "scenarios": [s["label"] for s in scen],
            "versions": versions(exe),
            "prompt": PROMPT,
        }
        (save / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    total = rounds * len(scen) * len(arms)
    n = 0
    for rnd in range(1, rounds + 1):
        for si, s in enumerate(scen, 1):
            for arm, cli, model, effort in arms:
                n += 1
                d = root / arm / f"r{rnd}-s{si:02d}"
                if cli == "knaif":
                    gpu = wait_for_idle_gpu()
                    r = run_native(exe, s["utterance"], s["fixture"], fixdir, d)
                    r["gpu_before"] = gpu
                else:
                    before = snapshot(REPO)
                    r = run_agent(cli, model, effort, s["utterance"], s["fixture"], fixdir, d)
                    problems = guard_diff(before, snapshot(REPO))
                    if problems:
                        print(f"GUARD: the checkout changed during {arm}: {problems}", flush=True)
                        sys.exit(2)
                r.update(grade(s, d, fixdir))
                row = {"round": rnd, "scenario": s["label"], "arm": arm, "model": model, **r}
                row["text"] = scrub(row.get("text") or "", scrub_roots)
                outcome = row.get("result") or row.get("outcome") or "-"
                secs = row.get("table_s") if cli == "knaif" else row.get("wall_s")
                print(
                    f"[{n}/{total}] r{rnd} {s['label']:<28} {arm:<22} {outcome:<8} {secs}s",
                    flush=True,
                )
                if save:
                    with (save / "raw.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
