"""knaif – local command agent library."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .agent import CommandAgent
from .orchestrator import InferenceOrchestrator
from .registry import ArgSchema, ToolDef, load_registry

if TYPE_CHECKING:
    pass

__all__ = [
    "ArgSchema",
    "CommandAgent",
    "InferenceOrchestrator",
    "ToolDef",
    "load_registry",
    "create_agent",
    "list_skills",
]


def _discover_skills_root() -> Path:
    """Locate the repo-local skills directory in the development layout.

    Skills are not bundled in the wheel; when installed that way callers must pass an
    explicit ``skills_root`` to :func:`create_agent` or use
    :meth:`CommandAgent.from_skill` with an absolute path.

    In a checkout the skills live at the repo-root ``skills/`` bundle directory
    (post-migration) or, during the monorepo migration, still at ``src/skills/``. We
    honor ``$KNAIF_SKILLS_ROOT`` first, then walk up from this package looking for the
    first directory that actually contains skills (a child with a ``skill.yaml``). This
    resolver therefore works both before and after the skill bundles move to root
    ``skills/`` — see docs/plans/2026-06-17-monorepo-dual-runtime.md (Phases 2–3).
    """
    env = os.environ.get("KNAIF_SKILLS_ROOT")
    if env:
        return Path(env)
    pkg_dir = Path(__file__).resolve().parent
    for ancestor in (pkg_dir, *pkg_dir.parents):
        for candidate in (ancestor / "skills", ancestor / "src" / "skills"):
            if candidate.is_dir() and any(
                (child / "skill.yaml").exists() for child in candidate.iterdir() if child.is_dir()
            ):
                return candidate
    # Fall back to the historical co-located default (absent in a bare wheel install).
    return pkg_dir.parent / "skills"


_DEFAULT_SKILLS_ROOT = _discover_skills_root()


def list_skills(skills_root: Path | str | None = None, include_stale: bool = False) -> list[str]:
    """Return names of all available skills, sorted alphabetically.

    Skills whose ``skill.yaml`` declares ``status: stale`` are excluded by
    default (they are partial/under rebuild and must not be offered to users or
    swept by the all-skills eval). Pass ``include_stale=True`` to list them too.
    A skill with no ``status`` field is treated as ``active``.
    """
    import yaml

    root = Path(skills_root) if skills_root else _DEFAULT_SKILLS_ROOT
    if not root.exists():
        return []

    names: list[str] = []
    for d in sorted(root.iterdir()):
        manifest_path = d / "skill.yaml"
        if not d.is_dir() or not manifest_path.exists():
            continue
        if not include_stale:
            with manifest_path.open(encoding="utf-8") as fh:
                manifest = yaml.safe_load(fh) or {}
            if manifest.get("status") == "stale":
                continue
        names.append(d.name)
    return names


def create_agent(
    skill: str,
    sandbox: Path | str | None = None,
    root: Path | str | None = None,
    orchestrator: InferenceOrchestrator | None = None,
    confirmer: Any | None = None,
    skills_root: Path | str | None = None,
    show_plan: bool = False,
    require_approval: bool = False,
    plan_display: Any | None = None,
    plan_confirmer: Any | None = None,
) -> CommandAgent:
    """Return a CommandAgent configured for the named skill.

    Parameters
    ----------
    skill:
        Name of the skill directory (e.g. ``"io"``, ``"ffmpeg"``).
    sandbox:
        Optional root directory that file operations are confined to.
        When ``None`` (the default), paths are resolved relative to *root* (cwd)
        and no sandbox boundary is enforced. Pass an explicit path for evalsuite
        runs and notebook sandboxes.
    root:
        Workspace root for resolving relative paths. Defaults to cwd.
    orchestrator:
        Optional :class:`InferenceOrchestrator` for real inference.
    confirmer:
        Optional mid-workflow confirmer ``(prompt, preview) -> bool`` used by
        ``wait_for_confirmation`` steps inside an expanded workflow.
    skills_root:
        Directory that contains skill subdirectories. Defaults to the
        ``skills/`` directory next to the ``knaif`` package.
    show_plan:
        If True, render a human-readable summary of the intent plan before
        expansion. See :meth:`CommandAgent.execute_plan`.
    require_approval:
        If True, ask ``plan_confirmer`` to approve the plan before expansion.
        Implies ``show_plan``.
    plan_display:
        Optional callback ``(summary: str) -> None`` invoked when the summary
        is ready. Defaults to ``print`` when not provided.
    plan_confirmer:
        Optional plan-level confirmer ``(summary: str) -> bool``. Distinct
        from ``confirmer`` (which gates mid-workflow previews). When not
        provided and ``require_approval`` is True, the ``confirmed`` flag
        passed to ``execute_plan`` decides.
    """
    root_dir = Path(skills_root) if skills_root else _DEFAULT_SKILLS_ROOT
    skill_dir = root_dir / skill
    if not skill_dir.exists() or not (skill_dir / "skill.yaml").exists():
        # Include stale skills in the hint: they are hidden from discovery but
        # still loadable explicitly, so they are valid targets for create_agent.
        available = list_skills(root_dir, include_stale=True)
        if not available:
            # The wheel ships only `knaif*` — skill bundles are repo assets loaded by
            # path. Without this branch a pip user sees an empty list and a directory
            # that does not exist, with no indication that it is expected.
            raise ValueError(
                f"Skill {skill!r} not found: no skills directory at {root_dir}.\n"
                "Skill bundles are not packaged in the knaif wheel; the published "
                "package is the engine plus the knaif.cli SDK.\n"
                "Pass your own bundle directory, e.g. "
                'create_agent("my_skill", skills_root="/path/to/skills"), or set '
                "$KNAIF_SKILLS_ROOT.\n"
                "Reference skills and the authoring guide: "
                "https://github.com/blackdeep-tech/knaif"
            )
        raise ValueError(f"Skill {skill!r} not found in {root_dir}. Available skills: {available}")
    return CommandAgent.from_skill(
        skill_dir=skill_dir,
        sandbox=sandbox,
        root=root,
        orchestrator=orchestrator,
        confirmer=confirmer,
        show_plan=show_plan,
        require_approval=require_approval,
        plan_display=plan_display,
        plan_confirmer=plan_confirmer,
    )
