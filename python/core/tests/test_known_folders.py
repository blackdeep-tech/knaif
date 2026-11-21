"""knaif.known_folders: "save it in Downloads" means the user's real Downloads folder.

- `resolve(name)` finds the folder on each OS (Windows asks the shell, so a moved or OneDrive
  Documents is found; macOS ~/<Name>; Linux xdg-user-dirs, else ~/<Name>).
  `KNAIF_KNOWN_FOLDERS_ROOT=<dir>` maps every folder to <dir>/<Name>, for tests and evals.
- `rewrite_outputs(plan, ...)` turns an output arg whose first segment names a known folder into
  a path inside it. Only output args move; inputs keep today's resolution.
- A folder of the same name in the working folder is a question, not a guess: the rewrite
  returns a clarify naming both. One next to the input is not (outputs never go there).
- With a sandbox, a known folder outside it is refused like any other path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.known_folders import (
    DISPLAY_NAMES,
    KNOWN_FOLDERS,
    SameNameFolder,
    resolve,
    rewrite_outputs,
)
from knaif.registry import ArgSchema, ToolDef

# ── resolve ──────────────────────────────────────────────────────────────────


def test_every_folder_has_a_display_name():
    assert set(DISPLAY_NAMES) == set(KNOWN_FOLDERS)
    assert DISPLAY_NAMES["downloads"] == "Downloads"


@pytest.mark.parametrize("name", [n for n in KNOWN_FOLDERS if n != "home"])
def test_the_test_root_maps_every_folder(tmp_path, name):
    assert resolve(name, env={"KNAIF_KNOWN_FOLDERS_ROOT": str(tmp_path)}) == (
        tmp_path / DISPLAY_NAMES[name]
    )


def test_the_test_root_maps_home_to_itself(tmp_path):
    assert resolve("home", env={"KNAIF_KNOWN_FOLDERS_ROOT": str(tmp_path)}) == tmp_path


def test_macos_is_home_plus_the_name(tmp_path):
    assert resolve("music", env={}, platform="darwin", home=tmp_path) == tmp_path / "Music"


def test_linux_reads_xdg_user_dirs(tmp_path):
    cfg = tmp_path / ".config"
    cfg.mkdir()
    (cfg / "user-dirs.dirs").write_text(
        '# comment\nXDG_DOWNLOAD_DIR="$HOME/Téléchargements"\nXDG_MUSIC_DIR="/data/music"\n',
        encoding="utf-8",
    )
    assert resolve("downloads", env={}, platform="linux", home=tmp_path) == (
        tmp_path / "Téléchargements"
    )
    assert resolve("music", env={}, platform="linux", home=tmp_path) == Path("/data/music")
    assert resolve("videos", env={}, platform="linux", home=tmp_path) == tmp_path / "Videos"


def test_linux_honours_xdg_config_home(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Bureau"\n', encoding="utf-8")
    got = resolve("desktop", env={"XDG_CONFIG_HOME": str(cfg)}, platform="linux", home=tmp_path)
    assert got == tmp_path / "Bureau"


def test_windows_asks_the_shell(tmp_path):
    asked: list[str] = []

    def shell(name: str) -> Path:
        asked.append(name)
        return tmp_path / "OneDrive" / "Documents"

    got = resolve("documents", env={}, platform="win32", home=tmp_path, windows_shell=shell)
    assert got == tmp_path / "OneDrive" / "Documents"
    assert asked == ["documents"]


def test_windows_falls_back_to_home_when_the_shell_fails(tmp_path):
    def shell(name: str) -> Path:
        raise OSError("no shell")

    got = resolve("desktop", env={}, platform="win32", home=tmp_path, windows_shell=shell)
    assert got == tmp_path / "Desktop"


# ── rewrite_outputs ──────────────────────────────────────────────────────────


def _registry() -> dict[str, ToolDef]:
    return {
        "convert_video": ToolDef(
            name="convert_video",
            description="",
            required_args=("inputs",),
            optional_args=("container", "output"),
        ),
        "split_pdf": ToolDef(
            name="split_pdf",
            description="",
            required_args=("input",),
            optional_args=("output_dir",),
        ),
        "export_thing": ToolDef(
            name="export_thing",
            description="",
            required_args=("source", "target"),
            arg_schemas={"target": ArgSchema(path_role="output")},
        ),
    }


def _rw(plan, tmp_path, *, sandbox=None, cwd=None):
    root = tmp_path / "known"
    return rewrite_outputs(
        plan,
        _registry(),
        cwd=cwd or tmp_path / "work",
        sandbox=sandbox,
        env={"KNAIF_KNOWN_FOLDERS_ROOT": str(root)},
    )


def _work(tmp_path: Path) -> Path:
    w = tmp_path / "work"
    w.mkdir(exist_ok=True)
    return w


def test_an_output_into_a_known_folder_is_rewritten(tmp_path):
    _work(tmp_path)
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "Downloads/clip.mp4"}}
    ]
    out = _rw(plan, tmp_path)
    assert out[0]["args"]["output"] == (tmp_path / "known" / "Downloads" / "clip.mp4").as_posix()
    assert out[0]["args"]["inputs"] == ["clip.mov"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Downloads", "Downloads"),
        ("downloads/", "Downloads"),
        ("~/Desktop/x.jpg", "Desktop/x.jpg"),
        ("./Music/a.mp3", "Music/a.mp3"),
        ("~/clip.mp4", "clip.mp4"),
    ],
)
def test_the_forms_a_model_writes(tmp_path, value, expected):
    _work(tmp_path)
    plan = [{"tool": "convert_video", "args": {"inputs": ["c.mov"], "output": value}}]
    got = _rw(plan, tmp_path)[0]["args"]["output"]
    assert got == (tmp_path / "known" / expected).as_posix().rstrip("/")


def test_output_dir_and_output_roles_are_rewritten(tmp_path):
    _work(tmp_path)
    plan = [
        {"tool": "split_pdf", "args": {"input": "a.pdf", "output_dir": "Documents"}},
        {"tool": "export_thing", "args": {"source": "Downloads/a", "target": "Desktop/b"}},
    ]
    out = _rw(plan, tmp_path)
    assert out[0]["args"]["output_dir"] == (tmp_path / "known" / "Documents").as_posix()
    assert out[1]["args"]["target"] == (tmp_path / "known" / "Desktop" / "b").as_posix()
    assert out[1]["args"]["source"] == "Downloads/a"


def test_other_outputs_are_untouched(tmp_path):
    _work(tmp_path)
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["c.mov"], "output": "out/Downloads/c.mp4"}}
    ]
    assert _rw(plan, tmp_path) == plan


def test_the_plan_given_is_not_changed(tmp_path):
    _work(tmp_path)
    plan = [{"tool": "convert_video", "args": {"inputs": ["c.mov"], "output": "Downloads/c.mp4"}}]
    before = json.dumps(plan)
    _rw(plan, tmp_path)
    assert json.dumps(plan) == before


def test_a_same_named_folder_in_the_working_folder_is_a_question(tmp_path):
    (_work(tmp_path) / "Downloads").mkdir()
    plan = [{"tool": "convert_video", "args": {"inputs": ["c.mov"], "output": "Downloads/c.mp4"}}]
    with pytest.raises(SameNameFolder) as err:
        _rw(plan, tmp_path)
    q = str(err.value)
    assert "Downloads" in q and (tmp_path / "known" / "Downloads").as_posix() in q


def test_a_same_named_folder_next_to_the_input_is_no_question(tmp_path):
    # (owner, 2026-10-24): a relative output is written under the working folder, never
    # next to its input, so a `Desktop` folder there could not have received it.
    work = _work(tmp_path)
    (work / "media" / "Desktop").mkdir(parents=True)
    plan = [{"tool": "convert_video", "args": {"inputs": ["media/c.mov"], "output": "Desktop"}}]
    assert _rw(plan, tmp_path)[0]["args"]["output"].startswith((tmp_path / "known").as_posix())


def test_a_same_named_file_is_no_question(tmp_path):
    (_work(tmp_path) / "Downloads").write_text("not a folder", encoding="utf-8")
    plan = [{"tool": "convert_video", "args": {"inputs": ["c.mov"], "output": "Downloads/c.mp4"}}]
    assert _rw(plan, tmp_path)[0]["args"]["output"].startswith((tmp_path / "known").as_posix())


def test_a_known_folder_outside_the_sandbox_is_refused(tmp_path):
    work = _work(tmp_path)
    plan = [{"tool": "convert_video", "args": {"inputs": ["c.mov"], "output": "Downloads/c.mp4"}}]
    with pytest.raises(ValueError, match="outside the sandbox"):
        _rw(plan, tmp_path, sandbox=work)


def test_a_known_folder_inside_the_sandbox_is_allowed(tmp_path):
    _work(tmp_path)
    plan = [{"tool": "convert_video", "args": {"inputs": ["c.mov"], "output": "Downloads/c.mp4"}}]
    out = _rw(plan, tmp_path, sandbox=tmp_path)
    assert out[0]["args"]["output"].startswith((tmp_path / "known").as_posix())


def test_a_variable_reference_is_untouched(tmp_path):
    _work(tmp_path)
    plan = [{"tool": "convert_video", "args": {"inputs": ["$clip"], "output": "$out"}}]
    assert _rw(plan, tmp_path) == plan


# ── the pipeline rewrites before anything runs ───────────────────────────────


def _ffmpeg(cwd: Path, monkeypatch, root: Path):
    from knaif import CommandAgent

    monkeypatch.setenv("KNAIF_KNOWN_FOLDERS_ROOT", str(root))
    monkeypatch.chdir(cwd)
    skills = Path(__file__).resolve().parents[3] / "skills"
    return CommandAgent.from_skill(skills / "ffmpeg")


def _clip(cwd: Path) -> Path:
    src = Path(__file__).resolve().parents[3] / "sandbox" / "fixtures" / "ffmpeg" / "clip.mov"
    if not src.exists():
        pytest.skip("ffmpeg fixtures not generated")
    (cwd / "clip.mov").write_bytes(src.read_bytes())
    return cwd / "clip.mov"


def test_a_dry_run_writes_into_the_known_folder(tmp_path, monkeypatch):
    work = _work(tmp_path)
    _clip(work)
    agent = _ffmpeg(work, monkeypatch, tmp_path / "known")
    plan = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {
                    "inputs": ["clip.mov"],
                    "container": "mp4",
                    "output": "Downloads/clip.mp4",
                },
            }
        ]
    }
    results = agent.execute_plan(
        plan, dry_run=True, utterance="convert clip.mov to mp4 into Downloads"
    )
    rendered = json.dumps(results)
    assert (tmp_path / "known" / "Downloads").as_posix() in rendered.replace("\\\\", "/")


def test_a_same_named_folder_makes_the_pipeline_ask(tmp_path, monkeypatch):
    work = _work(tmp_path)
    _clip(work)
    (work / "Downloads").mkdir()
    agent = _ffmpeg(work, monkeypatch, tmp_path / "known")
    plan = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {
                    "inputs": ["clip.mov"],
                    "container": "mp4",
                    "output": "Downloads/clip.mp4",
                },
            }
        ]
    }
    [res] = agent.execute_plan(plan, dry_run=True, utterance="convert clip.mov into Downloads")
    assert res["tool"] == "clarify"
    assert "Which one" in res["result"]["question"]


def test_a_relative_root_is_made_absolute_where_it_is_read(tmp_path: Path, monkeypatch) -> None:
    """A relative KNAIF_KNOWN_FOLDERS_ROOT read against the working folder once, here: a handler
    that joins a relative output to its own root nested the "known folder" inside the row folder
    (`<row>/sandbox/.../<row>.known/Documents/x.md`), found by an eval run."""
    from knaif.known_folders import ROOT_ENV, resolve

    monkeypatch.chdir(tmp_path)
    got = resolve("downloads", env={ROOT_ENV: "lane/r.known"})
    assert got.is_absolute()
    assert got == tmp_path / "lane" / "r.known" / "Downloads"


_SPELLED_FIRST = """
import pathlib, sys
from knaif.known_folders import SameNameFolder, rewrite_outputs

work, known = pathlib.Path(sys.argv[1]), sys.argv[2]
# Both spellings are folders here, as on a case-insensitive disk (Windows, macOS).
pathlib.Path.is_dir = lambda self: self.parent == work and self.name.lower() == "videos"
plan = [{"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "videos/small"}}]
try:
    rewrite_outputs(plan, {}, cwd=work, env={"KNAIF_KNOWN_FOLDERS_ROOT": known})
except SameNameFolder as exc:
    print(str(exc).split(" folder at ")[0])
"""


def test_the_question_names_the_folder_as_the_plan_spells_it(tmp_path: Path) -> None:
    """(2026-10-24): `videos/small` asked about a "Videos" folder in
    some runs and a "videos" folder in others: the two spellings were looped over as a set, whose
    order changes with the process's hash seed. As native: the spelling the plan wrote, first."""
    import os
    import subprocess
    import sys

    got = set()
    for seed in range(16):
        out = subprocess.run(
            [sys.executable, "-c", _SPELLED_FIRST, str(tmp_path), str(tmp_path / "known")],
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
            check=True,
        )
        got.add(out.stdout.strip())
    assert got == {"There is a videos"}
