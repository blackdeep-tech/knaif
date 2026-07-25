"""Reproducible real-world head-to-head: local knaif-cli vs. a premium agent.

Runs each scenario (scenarios.yaml) as a genuine single invocation of each tool,
verifies every produced file with ffprobe, and reports result + speed + cost per
request. The premium agent is pluggable (see agents.py): --agent claude|copilot|codex.

By default every invocation runs COLD in a fresh unique working directory, so no
request gets a warm prompt-cache discount from a previous one (fair + reproducible).

    just experiment-agent-vs-knaif                 # claude vs knaif, all scenarios, cold
    uv run python scripts/agent_vs_knaif/run.py --agent claude --limit 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents import AGENTS  # noqa: E402
from pricing import api_equiv_usd  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def ffprobe(p: Path | None) -> dict | None:
    if not p or not p.exists():
        return None
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration,size:stream=codec_type,codec_name,height",
                "-of",
                "json",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        d = json.loads(out.stdout)
        fmt, streams = d.get("format", {}), d.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"), {})
        a = next((s for s in streams if s.get("codec_type") == "audio"), {})
        return {
            "container": fmt.get("format_name", ""),
            "dur": float(fmt.get("duration", 0) or 0),
            "size": int(fmt.get("size", 0) or 0),
            "vcodec": v.get("codec_name"),
            "acodec": a.get("codec_name"),
            "height": v.get("height"),
            "has_v": bool(v),
            "has_a": bool(a),
        }
    except Exception:
        return None


def check(exp: dict, probe: dict | None, in_size: int) -> str:
    if not probe:
        return "no-output"
    ok = True
    if "container" in exp and exp["container"] not in (probe.get("container") or ""):
        ok = False
    if exp.get("smaller") and not (probe.get("size", 10**9) < in_size):
        ok = False
    if exp.get("acodec") and exp["acodec"] != (probe.get("acodec") or ""):
        ok = False
    if exp.get("no_video") and probe.get("has_v"):
        ok = False
    if exp.get("no_audio") and probe.get("has_a"):
        ok = False
    if exp.get("vcodec") and exp["vcodec"] != (probe.get("vcodec") or ""):
        ok = False
    if exp.get("height_max") and (probe.get("height") or 9999) > exp["height_max"]:
        ok = False
    if exp.get("dur_approx") and abs((probe.get("dur") or 0) - exp["dur_approx"]) > 1.5:
        ok = False
    return "PASS" if ok else "FAIL"


def _out_file(d: Path, fixture: str) -> Path | None:
    exts = {
        ".mp4",
        ".mkv",
        ".mov",
        ".webm",
        ".mp3",
        ".m4a",
        ".wav",
        ".flac",
        ".opus",
        ".gif",
        ".jpg",
        ".png",
    }
    cands = [
        p for p in d.iterdir() if p.is_file() and p.name != fixture and p.suffix.lower() in exts
    ]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def run_knaif(utt: str, fixture: str, fixdir: Path, model: str, workroot: Path) -> dict:
    d = workroot / f"knaif_{int(time.time()*1000)}"
    d.mkdir(parents=True)
    shutil.copy(fixdir / fixture, d / fixture)
    r = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO),
            "knaif-cli",
            "run",
            "ffmpeg",
            utt,
            "--backend",
            "llama-cpp",
            "--model",
            model,
            "--no-dry-run",
            "--auto-approve",
        ],
        cwd=str(d),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        stdin=subprocess.DEVNULL,
    )
    txt = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"intent:\s*([\d.]+)s", txt)
    low = txt.lower()
    outcome = (
        "reject"
        if "🚫" in txt or "reject" in low
        else (
            "clarify"
            if "❓" in txt or "clarify" in low
            else "plan" if "✓" in txt or "→" in txt else "other"
        )
    )
    of = _out_file(d, fixture)
    return {
        "infer_s": float(m.group(1)) if m else None,
        "outcome": outcome,
        "out": of,
        "probe": ffprobe(of),
        "input_deleted": not (d / fixture).exists(),
        "in_size": (fixdir / fixture).stat().st_size,
        "dir": d,
    }


def run_agent(
    agent, model: str, utt: str, fixture: str, fixdir: Path, cold_root: Path | None
) -> dict:
    d = Path(tempfile.mkdtemp(prefix="agent_", dir=str(cold_root) if cold_root else None))
    shutil.copy(fixdir / fixture, d / fixture)
    prompt = (
        f"You must use ffmpeg via the shell to accomplish exactly this request and nothing else. "
        f"Input file: {fixture} (current directory). Request: {utt}. "
        f"Write any output into the current directory, then stop."
    )
    argv = agent.build_argv(prompt, model or agent.default_model)
    t0 = time.time()
    r = subprocess.run(
        argv,
        cwd=str(d),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        stdin=subprocess.DEVNULL,
    )
    wall = time.time() - t0
    # copilot writes its reply to stdout but the credits/tokens footer to stderr;
    # give every adapter's parser the combined stream so either layout works.
    combined = (r.stdout or "") + (r.stderr or "")
    try:
        metrics = agent.parse(combined, wall)
    except Exception as e:
        metrics = {
            "cost": None,
            "cost_unit": agent.cost_unit,
            "in_tok": None,
            "out_tok": None,
            "duration_s": round(wall, 1),
            "turns": None,
            "parse_error": str(e),
        }
    # normalize every arm to an API-equivalent USD from its measured token split, so
    # $ (claude), credits (copilot), and subscription-tokens (codex) are comparable.
    price_model = model or agent.default_model
    metrics["est_usd"] = api_equiv_usd(
        price_model,
        metrics.get("uncached_in"),
        metrics.get("cache_read"),
        metrics.get("cache_write"),
        metrics.get("out_tok"),
    )
    metrics["price_model"] = price_model
    of = _out_file(d, fixture)
    metrics.update(
        {
            "out": of,
            "probe": ffprobe(of),
            "input_deleted": not (d / fixture).exists(),
            "in_size": (fixdir / fixture).stat().st_size,
            "result_text": " ".join((_result_text(agent.name, combined) or "").split())[:200],
        }
    )
    return metrics


def _result_text(agent_name: str, stdout: str) -> str:
    stdout = stdout or ""
    if agent_name == "codex":
        # `--json` emits one JSONL event/line; the reply is the last agent_message item.
        text = ""
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            item = evt.get("item") or {}
            if evt.get("type") == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text", "")
        return text
    if agent_name == "copilot":
        # plain text: the reply, then blank lines, then a "Changes"/"AI Credits" footer.
        m = re.split(r"\n\s*\n(?:Changes|AI Credits)", stdout, maxsplit=1)
        return m[0].strip()
    try:
        return json.loads(stdout).get("result", "")
    except Exception:
        return stdout[-200:]


def fmt_cost(m: dict) -> str:
    if m.get("cost") is None:
        return "n/a"
    return (
        f"${m['cost']:.4f}"
        if m.get("cost_unit") == "usd"
        else f"{m['cost']:g} {m.get('cost_unit')}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent", default="claude", choices=list(AGENTS))
    ap.add_argument("--agent-model", default=None, help="override the agent's default model")
    ap.add_argument(
        "--knaif-model", default="knaif-qwen3-4b-v1", help="models.yaml entry for the local arm"
    )
    ap.add_argument("--scenarios", default=str(HERE / "scenarios.yaml"))
    ap.add_argument("--fixtures-dir", default=str(REPO / "sandbox/fixtures/ffmpeg"))
    ap.add_argument("--out", default=str(HERE / "RESULTS.md"))
    ap.add_argument("--limit", type=int, default=0, help="run only the first N scenarios")
    ap.add_argument(
        "--warm",
        action="store_true",
        help="reuse one working dir per arm (disables cold isolation)",
    )
    ap.add_argument(
        "--ffmpeg-dir", default=None, help="prepend this dir to PATH (where ffmpeg/ffprobe live)"
    )
    args = ap.parse_args()

    if args.ffmpeg_dir:
        os.environ["PATH"] = args.ffmpeg_dir + os.pathsep + os.environ["PATH"]
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("ffmpeg/ffprobe not on PATH — install them or pass --ffmpeg-dir")

    agent = AGENTS[args.agent]
    fixdir = Path(args.fixtures_dir)
    scen = yaml.safe_load(Path(args.scenarios).read_text(encoding="utf-8"))["scenarios"]
    if args.limit:
        scen = scen[: args.limit]

    # cold (default): fresh unique dir per invocation. warm: fixed reused dirs.
    knaif_root = REPO / "sandbox/_agent_exp/knaif"
    agent_cold_root = None  # tempfile default (system temp, unique) → cold + memory-free
    if args.warm:
        knaif_root = REPO / "sandbox/_agent_exp/knaif_warm"
        agent_cold_root = REPO / "sandbox/_agent_exp/agent_warm"
        agent_cold_root.mkdir(parents=True, exist_ok=True)
    knaif_root.mkdir(parents=True, exist_ok=True)

    mode = "warm (shared dir)" if args.warm else "cold (fresh dir per run)"
    print(
        f"agent={args.agent} model={args.agent_model or agent.default_model} | "
        f"knaif={args.knaif_model} | mode={mode}\n"
    )
    if agent.isolated_note:
        print(f"note[{args.agent}]: {agent.isolated_note}\n")

    rows, total_cost, total_est = [], 0.0, 0.0
    for s in scen:
        label, utt, fixture = s["label"], s["utterance"], s["fixture"]
        behavior = s.get("behavior")
        exp = s.get("expect", {})
        print(f'### {label}  —  "{utt}"', flush=True)
        k = run_knaif(utt, fixture, fixdir, args.knaif_model, knaif_root)
        a = run_agent(agent, args.agent_model, utt, fixture, fixdir, agent_cold_root)
        if a.get("cost") and a.get("cost_unit") == "usd":
            total_cost += a["cost"]
        if a.get("est_usd"):
            total_est += a["est_usd"]
        est = f"~${a['est_usd']:.4f} API-eq" if a.get("est_usd") is not None else "est n/a"
        # token split shared by both branches' JSON rows
        atok = {
            "est_usd": a.get("est_usd"),
            "price_model": a.get("price_model"),
            "in_tok": a.get("in_tok"),
            "uncached_in": a.get("uncached_in"),
            "cache_read": a.get("cache_read"),
            "cache_write": a.get("cache_write"),
            "out_tok": a.get("out_tok"),
        }
        if behavior:
            kv = f"outcome={k['outcome']}"
            av = (
                f"produced={[a['out'].name] if a['out'] else []} input_deleted={a['input_deleted']}"
            )
            print(f"  knaif : {kv} · {k['infer_s']}s · free")
            print(f"  agent : {av} · {fmt_cost(a)} · {est} · {a['duration_s']}s")
            print(f"          agent: {a['result_text']}", flush=True)
            rows.append(
                {
                    "label": label,
                    "utterance": utt,
                    "kind": "behavior",
                    "knaif": {"outcome": k["outcome"], "infer_s": k["infer_s"]},
                    "agent": {
                        "produced": bool(a["out"]),
                        "input_deleted": a["input_deleted"],
                        "cost": a["cost"],
                        "cost_unit": a["cost_unit"],
                        "duration_s": a["duration_s"],
                        "result": a["result_text"],
                        **atok,
                    },
                }
            )
        else:
            kres, ares = check(exp, k["probe"], k["in_size"]), check(exp, a["probe"], a["in_size"])
            print(
                f"  knaif : {kres} · {k['infer_s']}s · free · {k['out'].name if k['out'] else '—'}"
            )
            print(
                f"  agent : {ares} · {fmt_cost(a)} · {est} · {a['duration_s']}s · {a.get('turns')} turns · "
                f"in {a.get('in_tok')} out {a.get('out_tok')} tok",
                flush=True,
            )
            rows.append(
                {
                    "label": label,
                    "utterance": utt,
                    "kind": "artifact",
                    "knaif": {"result": kres, "infer_s": k["infer_s"]},
                    "agent": {
                        "result": ares,
                        "cost": a["cost"],
                        "cost_unit": a["cost_unit"],
                        "duration_s": a["duration_s"],
                        "turns": a.get("turns"),
                        **atok,
                    },
                }
            )

    unit = agent.cost_unit
    summary = {
        "agent": args.agent,
        "agent_model": args.agent_model or agent.default_model,
        "knaif_model": args.knaif_model,
        "mode": mode,
        "rows": rows,
        "agent_total_cost_usd": round(total_cost, 4) if unit == "usd" else None,
        "agent_total_est_usd": round(total_est, 4),
    }
    native = (
        fmt_cost({"cost": total_cost, "cost_unit": unit})
        if unit == "usd"
        else "see per-row (native unit)"
    )
    print(
        f"\n=== {args.agent}: total {native} · ~${total_est:.4f} API-equivalent "
        f"over {len(scen)} requests; knaif free · mode={mode} ==="
    )

    out = Path(args.out)
    out.write_text(_render_md(summary), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {out} and {out.with_suffix('.json')}")


def _fmt_native(a: dict) -> str:
    if a.get("cost") is None:
        return "n/a"
    return f"${a['cost']:.4f}" if a.get("cost_unit") == "usd" else f"{a['cost']:g} {a['cost_unit']}"


def _render_md(s: dict) -> str:
    L = [
        f"# knaif vs. {s['agent']} — real-world head-to-head ({s['mode']})\n",
        f"agent model: `{s['agent_model']}` · knaif model: `{s['knaif_model']}`\n",
        "`API-eq $` = API-equivalent USD estimated from measured tokens (see pricing.py); "
        "native cost is each CLI's own unit ($ / credits / subscription-tokens).\n",
        "| request | knaif | agent (native) | agent (API-eq $) |",
        "|---|---|---|---|",
    ]
    for r in s["rows"]:
        k, a = r["knaif"], r["agent"]
        est = f"~${a['est_usd']:.4f}" if a.get("est_usd") is not None else "n/a"
        left = (
            f"{k['result']} · {k['infer_s']}s · free"
            if r["kind"] == "artifact"
            else f"{k['outcome']} · {k['infer_s']}s · free"
        )
        mid = (
            f"{a['result']} · {_fmt_native(a)} · {a['duration_s']}s"
            if r["kind"] == "artifact"
            else f"produced={a['produced']} · {_fmt_native(a)} · {a['duration_s']}s"
        )
        L.append(f"| {r['label']} | {left} | {mid} | {est} |")
    tail = []
    if s.get("agent_total_cost_usd") is not None:
        tail.append(f"native ${s['agent_total_cost_usd']:.4f}")
    if s.get("agent_total_est_usd") is not None:
        tail.append(f"~${s['agent_total_est_usd']:.4f} API-equivalent")
    if tail:
        L.append(
            f"\n**agent total: {' · '.join(tail)}** over {len(s['rows'])} requests; knaif free."
        )
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
