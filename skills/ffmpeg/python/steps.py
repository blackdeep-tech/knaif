"""ffmpeg skill — steps module (see handlers.py / SPEC.md)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from knaif.handler_api import HandlerContext
from knaif.tool import Step

from . import _deps
from ._engine import (
    _CRF_RE,
    _build_one_recipe,
    _crf_to_profile_name,
    _dummy_probe,
    _load_yaml,
    _normalize_platform,
    _preview_output_for,
    _profiles_root,
    _render_command,
    _summarise_probe,
)

# ─────────────────────────────────────────────────────────────────────────────
# Step handlers.
# ─────────────────────────────────────────────────────────────────────────────


class InspectMediaStep(Step):
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
                raw = _deps.run_ffprobe(path)
            except _deps.FFmpegNotAvailable:
                raise
            except Exception as exc:  # noqa: BLE001
                if preview:
                    probes.append(_dummy_probe(path))
                else:
                    errors.append({"file": str(path), "error": str(exc)})
                continue
            probes.append(_summarise_probe(path, raw))
        return {"count": len(probes), "probes": probes, "errors": errors}


class LoadPlatformProfileStep(Step):
    name = "load_platform_profile"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        platform = _normalize_platform(args["platform"])
        path = _profiles_root(ctx) / "platforms" / f"{platform}.yaml"
        if not path.exists():
            raise ValueError(f"Unknown platform profile: {platform!r}")
        return _load_yaml(path)


class LoadQualityProfileStep(Step):
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


class BuildRecipesStep(Step):
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

        recipes = [
            _build_one_recipe(p, platform_profile, quality_profile, options, sandbox=ctx.sandbox)
            for p in probes
        ]
        return {"count": len(recipes), "recipes": recipes}


class RenderPreviewCommandStep(Step):
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
            "output_override": _preview_output_for(recipe),
        }
        command = _render_command(recipe, preview=preview_info)
        return {
            "command": command,
            "preview_output": preview_info["output_override"],
            "recipe": recipe,
            "sample_seconds": sample_seconds,
        }


class RunPreviewStep(Step):
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

        result = _deps.run_ffmpeg(command)
        return {
            "mode": "execute",
            "command": command,
            "preview_output": preview_output,
            "returncode": result["returncode"],
            "stderr_tail": result["stderr"][-2000:] if result["stderr"] else "",
        }


class VerifyPreviewStep(Step):
    name = "verify_preview"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        output_arg = args["preview_output"]
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
            probe = _deps.run_ffprobe(path)
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


class RenderBatchCommandsStep(Step):
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


class RunBatchStep(Step):
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

        for c in commands:
            res = _deps.run_ffmpeg(c["command"])
            outputs.append(
                {
                    "mode": "execute",
                    "input": c.get("input"),
                    "output": c.get("output"),
                    "returncode": res["returncode"],
                    "stderr_tail": res["stderr"][-2000:] if res["stderr"] else "",
                }
            )
        return {"mode": "execute", "count": len(outputs), "outputs": outputs}


class VerifyOutputsStep(Step):
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
                probe = _deps.run_ffprobe(path)
            except _deps.FFmpegNotAvailable:
                raise
            except Exception as exc:  # noqa: BLE001
                verifications.append({"verified": False, "output": str(path), "reason": str(exc)})
                continue
            summary = _summarise_probe(path, probe)
            verifications.append({"verified": True, "output": str(path), "summary": summary})
        return {"count": len(verifications), "verifications": verifications}


class GenerateReportStep(Step):
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
