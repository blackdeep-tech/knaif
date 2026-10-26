"""ffmpeg skill — steps module (see handlers.py / SPEC.md)."""

from __future__ import annotations

import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from knaif.handler_api import HandlerContext
from knaif.tool import Handler

from . import _deps
from ._engine import (
    _CRF_RE,
    DEFAULT_NAME,
    PER_FILE_NAME,
    _build_one_recipe,
    _crf_to_profile_name,
    _dummy_probe,
    _load_yaml,
    _needs_video,
    _normalize_platform,
    _preview_output_for,
    _profiles_root,
    _render_command,
    _summarise_probe,
    _trim_past_end,
    disambiguate_outputs,
)
from ._pieces import plan_pieces
from ._reporting import ffmpeg_failure


def require_streams(output: str | Path | None, ctx: HandlerContext) -> None:
    """Fail when ffmpeg exited 0 but wrote a file with no audio and no video in it.

    ffmpeg's exit code is not evidence of output: a trim past the end exits 0 with a 185-byte
    container. Left alone, the chain carries on and the next step fails under that file's
    name. A probe that errors for another reason is not this check's business.
    """
    if not output or not Path(output).is_file():
        return
    try:
        probe = _deps.run_ffprobe(Path(output), ctx)
    except _deps.FFmpegNotAvailable:
        raise
    except Exception:  # noqa: BLE001 — unprobeable is a different question
        return
    kinds = {s.get("codec_type") for s in probe.get("streams") or []}
    if not kinds & {"audio", "video"}:
        raise ValueError(
            f"ffmpeg finished but {Path(output).name} has no audio or video in it — "
            "the step produced nothing to work with."
        )


_FFMPEG_CONTEXT = re.compile(r"^\[[^\]]* @ (?:0x)?[0-9a-fA-F]+\]\s*")


def _probe_reason(error: str, path: Path) -> str:
    """ffprobe's first line of complaint, without the path (it names the user's folders) or
    ffmpeg's ``[demuxer @ address]`` prefix: ``moov atom not found``."""
    text = error.removeprefix(f"ffprobe failed for {path}: ")
    for line in text.splitlines():
        line = _FFMPEG_CONTEXT.sub("", line.strip()).removeprefix(f"{path}: ")
        if line:
            return line
    return error


def _finished(ctx: Any, file: str, failure: str | None) -> None:
    """Tell core how one file of a batch ended (``ctx.file_finished``), when it is listening."""
    report = getattr(ctx, "file_finished", None)
    if report is not None:
        report(file, failure)


def run_and_commit(command: list[str], output: str | None, ctx: HandlerContext) -> dict[str, Any]:
    """Run one ffmpeg command so that only a verified output reaches the user's folder.

    The command is rendered with the final path as its last argument (what a dry run shows and
    the eval replays). For real it writes to a staged path in the run scratch instead
    (`stage_output`), is checked (`require_streams`, then `commit_output`: not empty), and only
    then moves into place. A non-zero exit or a failed check leaves the user's folder as it
    was; the staged file goes with the scratch when the run ends.

    An output that is itself in the scratch (a routed chain intermediate) is written directly:
    it is already out of the user's view. So is any output when there is no scratch (a bare
    caller), which keeps the old direct write.
    """
    staged: str | None = None
    work_dir = getattr(ctx, "work_dir", None)
    if output and command and command[-1] == output and work_dir is not None:
        if Path(output).resolve().is_relative_to(Path(work_dir).resolve()):
            ctx.call_tool("ensure_parent", {"path": output})
        else:
            staged = ctx.call_tool("stage_output", {"path": output})["staged"]
            command = [*command[:-1], staged]
    elif output:
        # A destination directory (`videos_hevc/clip.mkv`) need not exist yet, and ffmpeg
        # does not create one. `ensure_parent` makes the RESOLVED parent: the spelling
        # `../escaped/../sb/out.mp4` passes containment, but walking it would create
        # `../escaped` on POSIX first.
        ctx.call_tool("ensure_parent", {"path": output})
    res = _deps.run_ffmpeg(command, ctx)
    if res["returncode"] == 0:
        require_streams(staged or output, ctx)
        if staged:
            ctx.call_tool("commit_output", {"staged": staged, "path": output})
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Step handlers.
# ─────────────────────────────────────────────────────────────────────────────


class InspectMediaStep(Handler):
    name = "inspect_media"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        files = args["files"]
        if isinstance(files, dict) and "files" in files:
            files = files["files"]
        if not isinstance(files, list):
            raise ValueError("inspect_media: 'files' must be a list of paths.")
        # In preview modes (dry_run or skip_execution) an earlier intent's output may
        # not be written yet, so a missing/unprobeable file is stubbed rather than
        # erroring. This is what lets a multi-intent chain preview every command.
        preview = ctx.dry_run or ctx.skip_execution
        probes: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for f in files:
            path = Path(f)
            if not path.exists():
                if preview:
                    probes.append(_dummy_probe(path))
                else:
                    errors.append({"file": str(path), "error": "not found"})
                continue
            try:
                raw = _deps.run_ffprobe(path, ctx)
            except _deps.FFmpegNotAvailable:
                raise
            except Exception as exc:  # noqa: BLE001
                if preview:
                    probes.append(_dummy_probe(path))
                else:
                    errors.append({"file": str(path), "error": str(exc)})
                continue
            probes.append(_summarise_probe(path, raw))
        # A file that cannot be read is one failed file of the batch; core counts it, and
        # ignores the report when it is the only file (its error then takes the usual path).
        for e in errors:
            _finished(ctx, e["file"], _probe_reason(e["error"], Path(e["file"])))
        return {"count": len(probes), "probes": probes, "errors": errors}


class LoadPlatformProfileStep(Handler):
    name = "load_platform_profile"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        platform = _normalize_platform(args["platform"])
        path = _profiles_root(ctx) / "platforms" / f"{platform}.yaml"
        if not path.exists():
            raise ValueError(f"Unknown platform profile: {platform!r}")
        return _load_yaml(path)


class LoadQualityProfileStep(Handler):
    name = "load_quality_profile"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        quality = args["quality"]
        crf_match = _CRF_RE.match(quality) if isinstance(quality, str) else None
        if crf_match:
            crf = int(crf_match.group(1))
            profile = _load_yaml(
                _profiles_root(ctx) / "quality" / f"{_crf_to_profile_name(crf)}.yaml"
            )
            profile["video_crf"] = (
                crf  # honor exact user CRF; inherit audio/preset from nearest profile
            )
            return profile
        path = _profiles_root(ctx) / "quality" / f"{quality}.yaml"
        if not path.exists():
            raise ValueError(f"Unknown quality profile: {quality!r}")
        return _load_yaml(path)


def _piece_recipes(
    probes: list[dict[str, Any]],
    quality_profile: dict[str, Any] | None,
    options: dict[str, Any],
    ctx: HandlerContext,
) -> dict[str, Any]:
    """One recipe per piece of a tool that writes many files from one (`_pieces`): the same
    pieces `FFmpegSkill.planned_outputs` promised core before the run."""
    base = Path(ctx.sandbox if ctx.sandbox is not None else ctx.root)
    recipes = []
    for probe in probes:
        pieces = plan_pieces(
            options["tool"], options["args"], float(probe.get("duration") or 0.0), probe.get("fps")
        )
        for out, piece in (pieces.items if pieces else []):
            piece = dict(piece)
            path = Path(out)
            piece["output_path"] = str(path if path.is_absolute() else base / path)
            recipes.append(
                _build_one_recipe(
                    probe,
                    None,
                    quality_profile,
                    piece,
                    sandbox=ctx.sandbox,
                    work_dir=getattr(ctx, "work_dir", None),
                    home=ctx.sandbox if ctx.sandbox is not None else getattr(ctx, "root", None),
                )
            )
            # A piece's name is the one core was promised before the run; it never moves.
            recipes[-1].pop(DEFAULT_NAME, None)
    return {"count": len(recipes), "recipes": recipes}


class BuildRecipesStep(Handler):
    name = "build_recipes"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        probes_arg = args["probes"]
        if isinstance(probes_arg, dict) and "probes" in probes_arg:
            probes = probes_arg["probes"]
        else:
            probes = probes_arg
        platform_profile = args.get("platform_profile") or None
        quality_profile = args.get("quality_profile") or None
        options = args.get("options") or {}

        # ffmpeg cannot extract audio from a file that has none: it exits with "Output file
        # does not contain any stream", and one silent video in a folder of ten takes the whole
        # batch down. `inspect_media` already probed for this, so building the command anyway
        # discards a fact we hold rather than lacking one.
        #
        # Scoped to `extract_audio` on measurement, not on principle: `adjust_volume` and
        # `strip_audio` both succeed on a real silent file (ffmpeg treats them as no-ops), so
        # skipping there would drop work that currently completes.
        skipped: list[str] = []
        if options.get("mode") == "pieces":
            return _piece_recipes(probes, quality_profile, options, ctx)

        if options.get("mode") == "extract_audio":
            kept = []
            for p in probes:
                if p.get("has_audio") is False:
                    skipped.append(str(p.get("file", "")))
                else:
                    kept.append(p)
            if kept:
                for f in skipped:
                    _finished(ctx, f, "has no audio track to extract")
            else:
                names = ", ".join(Path(f).name for f in skipped) or "the input"
                raise ValueError(
                    f"No audio to extract: {names} has no audio track."
                    if len(skipped) == 1
                    else f"No audio to extract — none of these files has an audio track: {names}."
                )
            probes = kept

        # A trim that starts at or past the end has no answer: ffmpeg would exit 0 with an
        # empty file and the NEXT step would fail under another file's name. Real probes only.
        if options.get("mode") == "trim" and not (ctx.dry_run or ctx.skip_execution):
            for p in probes:
                reason = _trim_past_end(options, p)
                if reason:
                    raise ValueError(reason)

        # A picture operation on a sound exits 0 having done nothing. A dry run's placeholder
        # probe decides audio vs video from the extension, which is what this reads, so it
        # holds there too.
        for p in probes:
            reason = _needs_video(options, p)
            if reason:
                raise ValueError(reason)

        # More than one file: a literal output name names none of them.
        per_file = {**options, PER_FILE_NAME: True} if len(probes) > 1 else options
        recipes = [
            _build_one_recipe(
                p,
                platform_profile,
                quality_profile,
                per_file,
                sandbox=ctx.sandbox,
                work_dir=getattr(ctx, "work_dir", None),
                home=ctx.sandbox if ctx.sandbox is not None else getattr(ctx, "root", None),
            )
            for p in probes
        ]
        # Only the batch knows whether two inputs land on one output path, and every command
        # carries -y, so an unresolved clash is a silent overwrite rather than an error.
        recipes = disambiguate_outputs(recipes)
        out = {"count": len(recipes), "recipes": recipes}
        # Returning fewer files than asked for without saying why reads as a tool that lost a
        # file. Only present when something was actually dropped.
        if skipped:
            out["skipped"] = skipped
        return out


class RenderPreviewCommandStep(Handler):
    name = "render_preview_command"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        recipes_arg = args["recipes"]
        if isinstance(recipes_arg, dict) and "recipes" in recipes_arg:
            recipes = recipes_arg["recipes"]
        else:
            recipes = recipes_arg
        if not recipes:
            raise ValueError("render_preview_command: no recipes available.")
        recipe = deepcopy(recipes[0])
        sample_seconds = int(args.get("sample_seconds") or 10)
        sample_position = args.get("sample_position", "middle")
        start = 0
        # The spec's "middle" heuristic requires knowing duration, which isn't
        # passed here. v1 picks a fixed start; callers can pass sample_position
        # later if needed.
        if sample_position == "start":
            start = 0
        preview_info = {
            "start": start,
            "duration": sample_seconds,
            "output_override": _preview_output_for(recipe, getattr(ctx, "work_dir", None)),
        }
        command = _render_command(recipe, preview=preview_info)
        return {
            "command": command,
            "preview_output": preview_info["output_override"],
            "recipe": recipe,
            "sample_seconds": sample_seconds,
        }


class RunPreviewStep(Handler):
    name = "run_preview"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        cmd_arg = args["command"]
        if isinstance(cmd_arg, dict) and "command" in cmd_arg:
            command = cmd_arg["command"]
            preview_output = cmd_arg.get("preview_output")
        else:
            command = cmd_arg
            preview_output = None
        if not isinstance(command, list):
            raise ValueError("run_preview: 'command' must be a list of args.")

        if ctx.dry_run or ctx.skip_execution:
            return {"mode": "dry_run", "command": command, "preview_output": preview_output}

        # A clip only makes sense if someone looks at it (owner, 2026-10-10). Without an
        # interactive confirmer the run acts without asking, so none is rendered and the
        # confirmation that follows takes `confirmed`. Native has never rendered one here.
        if getattr(ctx, "confirmer", None) is None:
            return {"mode": "skipped", "command": command, "preview_output": None}

        if preview_output:
            ctx.call_tool("ensure_parent", {"path": preview_output})
        result = _deps.run_ffmpeg(command, ctx)
        return {
            "mode": "execute",
            "command": command,
            "preview_output": preview_output,
            "returncode": result["returncode"],
            "stderr_tail": result["stderr"][-2000:] if result["stderr"] else "",
        }


class VerifyPreviewStep(Handler):
    name = "verify_preview"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        output_arg = args["preview_output"]
        if isinstance(output_arg, dict) and output_arg.get("mode") == "skipped":
            return {"verified": True, "skipped": True, "reason": "no one is asked, no clip made"}
        if isinstance(output_arg, dict):
            path_str = output_arg.get("preview_output") or output_arg.get("output")
        else:
            path_str = output_arg
        if not path_str:
            return {"verified": False, "reason": "no preview output path"}
        path = Path(path_str)
        if ctx.dry_run or ctx.skip_execution or not path.exists():
            return {
                "verified": ctx.dry_run or ctx.skip_execution,
                "preview_output": str(path),
                "skipped": True,
            }

        try:
            probe = _deps.run_ffprobe(path, ctx)
        except _deps.FFmpegNotAvailable:
            raise
        except Exception as exc:  # noqa: BLE001
            return {"verified": False, "preview_output": str(path), "reason": str(exc)}

        summary = _summarise_probe(path, probe)
        expected = args.get("expected") or {}
        issues: list[str] = []
        for key, want in expected.items():
            if summary.get(key) != want:
                issues.append(f"{key}={summary.get(key)!r} (expected {want!r})")
        return {
            "verified": not issues,
            "preview_output": str(path),
            "summary": summary,
            "issues": issues,
        }


class RenderBatchCommandsStep(Handler):
    name = "render_batch_commands"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        recipes_arg = args["recipes"]
        if isinstance(recipes_arg, dict) and "recipes" in recipes_arg:
            recipes = recipes_arg["recipes"]
        else:
            recipes = recipes_arg
        commands = [
            {"input": r["input"], "output": r["output"], "command": _render_command(r)}
            for r in recipes
        ]
        return {"count": len(commands), "commands": commands}


class RunBatchStep(Handler):
    name = "run_batch"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        commands_arg = args["commands"]
        if isinstance(commands_arg, dict) and "commands" in commands_arg:
            commands = commands_arg["commands"]
        else:
            commands = commands_arg
        outputs: list[dict[str, Any]] = []
        if ctx.dry_run or ctx.skip_execution:
            for c in commands:
                outputs.append(
                    {
                        "mode": "dry_run",
                        "input": c.get("input"),
                        "output": c.get("output"),
                        "command": c.get("command"),
                    }
                )
            return {"mode": "dry_run", "count": len(outputs), "outputs": outputs}

        # Over many files, one that fails does not stop the others: each file's end goes to
        # core (`ctx.file_finished`), which prints a failure as it happens and counts the batch
        # One command alone keeps its error, as before.
        many = len(commands) > 1
        for c in commands:
            out = c.get("output")
            started = time.perf_counter()
            try:
                res = run_and_commit(c["command"], out, ctx)
                failure = (
                    None
                    if res["returncode"] == 0
                    else ffmpeg_failure(res["returncode"], res["stderr"])
                )
            except _deps.FFmpegNotAvailable:
                raise
            except Exception as exc:  # noqa: BLE001 - one file's failure, reported below
                if not many:
                    raise
                res = {"returncode": 1, "stderr": str(exc)}
                failure = str(exc)
            outputs.append(
                {
                    "mode": "execute",
                    "input": c.get("input"),
                    "output": c.get("output"),
                    "returncode": res["returncode"],
                    "stderr_tail": res["stderr"][-2000:] if res["stderr"] else "",
                    # What ran and how long: the CLI shows both, as native does.
                    "command": c.get("command"),
                    "seconds": time.perf_counter() - started,
                }
            )
            _finished(ctx, str(c.get("input") or out or ""), failure)
        return {"mode": "execute", "count": len(outputs), "outputs": outputs}


class VerifyOutputsStep(Handler):
    name = "verify_outputs"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        outputs_arg = args["outputs"]
        if isinstance(outputs_arg, dict) and "outputs" in outputs_arg:
            items = outputs_arg["outputs"]
        else:
            items = outputs_arg
        verifications: list[dict[str, Any]] = []
        for item in items:
            path_str = item.get("output") if isinstance(item, dict) else item
            if not path_str:
                verifications.append({"verified": False, "reason": "no output path"})
                continue
            path = Path(path_str)
            if ctx.dry_run or ctx.skip_execution or not path.exists():
                verifications.append(
                    {
                        "verified": ctx.dry_run or ctx.skip_execution,
                        "output": str(path),
                        "skipped": True,
                    }
                )
                continue
            try:
                probe = _deps.run_ffprobe(path, ctx)
            except _deps.FFmpegNotAvailable:
                raise
            except Exception as exc:  # noqa: BLE001
                verifications.append({"verified": False, "output": str(path), "reason": str(exc)})
                continue
            summary = _summarise_probe(path, probe)
            verifications.append({"verified": True, "output": str(path), "summary": summary})
        return {"count": len(verifications), "verifications": verifications}


class GenerateReportStep(Handler):
    name = "generate_report"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        outputs_arg = args["outputs"]
        if isinstance(outputs_arg, dict):
            verifications = outputs_arg.get("verifications") or outputs_arg.get("outputs") or []
        else:
            verifications = outputs_arg
        lines: list[str] = []
        files: list[str] = []
        for i, v in enumerate(verifications, 1):
            out_path = v.get("output") if isinstance(v, dict) else None
            if out_path and v.get("verified"):
                files.append(out_path)
            summary = (v.get("summary") if isinstance(v, dict) else None) or {}
            size_bytes = summary.get("size_bytes")
            size_mb = round(size_bytes / 1_048_576, 2) if size_bytes else None
            res = f"{summary.get('width')}x{summary.get('height')}" if summary.get("width") else "?"
            lines.append(
                f"{i}. {out_path}  ({res}, {summary.get('container')}, {size_mb} MB)"
                if out_path
                else f"{i}. (no output)"
            )
        return {
            "count": len(verifications),
            "summary": "\n".join(lines) or "(empty)",
            "files": files,
        }
