"""Guard the macOS .pkg (installers/macos/) — the counterpart of `test_installer_iss.py`.

Three things must agree, as on Windows: each skill.yaml's `macos` block, build-pkg.sh's TOOLS table
and the Distribution's tool choices. Beyond that, the install scripts are run here for real against
a scratch volume, with fakes standing in for `stat`, `sudo`, Homebrew, `pkgbuild` and
`productbuild` — Installer passes the target volume as $3, and every script builds its paths from
it. What cannot run here is Installer itself: the options page, the signature and the upgrade are
the Mac's E6 gates.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
MACOS = REPO / "installers" / "macos"
BUILD = MACOS / "build-pkg.sh"
DIST = MACOS / "pkg" / "Distribution.xml.in"
SCRIPTS = MACOS / "pkg" / "scripts"
UNINSTALL = MACOS / "pkg" / "uninstall.sh"


def _active_skills() -> dict[str, dict]:
    out = {}
    for path in sorted((REPO / "skills").glob("*/skill.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("status") != "stale":
            out[data["name"]] = data
    return out


def _tools_table() -> list[tuple[str, str, str, str, str]]:
    text = BUILD.read_text(encoding="utf-8")
    block = re.search(r'^TOOLS="(.*?)"$', text, re.M | re.S)
    assert block, "TOOLS table not found in build-pkg.sh"
    return [tuple(line.split("|")) for line in block.group(1).splitlines()]  # type: ignore[misc]


def _build_var(name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"$', BUILD.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} not found in build-pkg.sh"
    return match.group(1)


def _distribution() -> ET.Element:
    text = DIST.read_text(encoding="utf-8")
    for key, value in {"@VERSION@": "1.3.0", "@MODEL@": "m", "@MIN_OS@": "12.0"}.items():
        text = text.replace(key, value)
    return ET.fromstring(text)


# -- the three-way mirror ---------------------------------------------------------------------


def test_every_external_tool_declares_how_the_pkg_installs_it() -> None:
    for name, skill in _active_skills().items():
        for tool in skill.get("dependencies", {}).get("external_tools", []):
            mac = tool.get("macos") or {}
            assert mac.get("brew"), f"{name}/{tool['name']}: no macos.brew in skill.yaml"


def test_the_tools_table_mirrors_skill_yaml() -> None:
    declared = set()
    for name, skill in _active_skills().items():
        for tool in skill.get("dependencies", {}).get("external_tools", []):
            mac = tool["macos"]
            declared.add((tool["name"], mac["brew"], "1" if mac.get("cask") else "0", name))
    table = {(tid, brew, cask, skill) for tid, _display, brew, cask, skill in _tools_table()}
    assert table == declared


def test_the_skill_list_is_every_active_skill() -> None:
    assert set(_build_var("SKILLS").split()) == set(_active_skills())


def test_the_distribution_offers_each_tool_and_skill_once() -> None:
    root = _distribution()
    ids = {c.get("id") for c in root.iter("choice")}
    tools = {row[0] for row in _tools_table()}
    skills = set(_active_skills())
    assert {i[len("tool_") :] for i in ids if i.startswith("tool_")} == tools
    assert {i[len("skill_") :] for i in ids if i.startswith("skill_")} == skills
    # Each tool choice is offered only with its own skill.
    for tid, _display, _brew, _cask, skill in _tools_table():
        choice = next(c for c in root.iter("choice") if c.get("id") == f"tool_{tid}")
        assert f"'skill_{skill}'" in (choice.get("enabled") or "")


def test_every_pkg_ref_is_a_package_build_pkg_builds() -> None:
    prefix = _build_var("PREFIX")
    built = {"core", "path", "model", "finish"}
    built |= {f"skill-{s}" for s in _build_var("SKILLS").split()}
    built |= {f"tool-{row[0]}" for row in _tools_table()}
    refs = {r.get("id"): (r.text or "").strip() for r in _distribution().iter("pkg-ref") if r.text}
    assert set(refs.values()) == {f"{name}.pkg" for name in built}
    for ident, file in refs.items():
        # build-pkg.sh names a package's identifier after its file: skill-ffmpeg -> skill.ffmpeg.
        assert ident == f"{prefix}.{file[: -len('.pkg')].replace('-', '.')}"
    # And every choice's pkg-ref names one of them.
    for choice in _distribution().iter("choice"):
        for ref in choice.iter("pkg-ref"):
            assert ref.get("id") in refs


def test_the_distribution_is_arm64_admin_options_always_shown() -> None:
    root = _distribution()
    options = root.find("options")
    assert options is not None
    assert options.get("customize") == "always"  # D13: the options page is always shown
    assert options.get("hostArchitectures") == "arm64"  # D4
    domains = root.find("domains")
    assert domains is not None and domains.get("enable_localSystem") == "true"
    assert domains.get("enable_currentUserHome") == "false"
    floor = root.find("volume-check/allowed-os-versions/os-version")
    assert floor is not None and floor.get("min") == "12.0"


def test_the_floor_and_the_model_are_never_typed_into_the_distribution() -> None:
    text = DIST.read_text(encoding="utf-8")
    assert 'min="@MIN_OS@"' in text
    assert "@MODEL@" in text and "qwen" not in text.lower()
    # The same default floor as the build (D9, D15).
    assert "MACOSX_DEPLOYMENT_TARGET:-12.0" in BUILD.read_text(encoding="utf-8")
    assert "MACOSX_DEPLOYMENT_TARGET:-12.0" in (REPO / "installers" / "package.sh").read_text(
        encoding="utf-8"
    )


def test_the_core_choice_cannot_be_deselected() -> None:
    core = next(c for c in _distribution().iter("choice") if c.get("id") == "core")
    assert core.get("enabled") == "false" and core.get("selected") == "true"


def test_the_uninstaller_forgets_the_same_receipts() -> None:
    text = UNINSTALL.read_text(encoding="utf-8")
    assert f'PKG_PREFIX="{_build_var("PREFIX")}."' in text


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*install.sh")), ids=lambda p: p.name)
def test_install_scripts_never_fail_the_install(script: Path) -> None:
    # D13: a failed download or brew install is reported, never fatal.
    text = script.read_text(encoding="utf-8")
    assert not re.search(r"^\s*set -[a-z]*e", text, re.M), f"{script.name} uses set -e"
    assert text.rstrip().endswith("exit 0"), f"{script.name} must end in exit 0"


# -- the scripts, run against a scratch volume --------------------------------------------------


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    return bash


def _posix(path: Path) -> str:
    """A path as bash's PATH needs it: Git Bash spells C:/x as /c/x (a colon splits PATH)."""
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        text = f"/{text[0].lower()}{text[2:]}"
    return text


FAKES = {
    # `stat -f%Su /dev/console`: the logged-in user, or failure when there is none.
    "stat": '[ -n "${FAKE_CONSOLE_USER:-}" ] && echo "$FAKE_CONSOLE_USER" || exit 1\n',
    # `sudo -u <user> -H cmd...`: record who, then run cmd as ourselves.
    "sudo": 'echo "$2" >> "$FAKE_LOG.sudo"\nshift 3\nexec "$@"\n',
    # `id -u <user>`: a fixed uid, so `launchctl asuser` has one to record.
    "id": "echo 501\n",
    # `launchctl asuser <uid> cmd...`: record the uid, then run cmd.
    "launchctl": 'echo "launchctl $1 $2" >> "$FAKE_LOG"\nshift 2\nexec "$@"\n',
    # `open -a Terminal <file>`: keep what Terminal was given, then run it as Terminal would.
    # FAKE_OPEN_EXIT makes it fail instead, as `open` does with no GUI session to open in.
    "open": 'echo "open $1 $2" >> "$FAKE_LOG"\n'
    '[ "${FAKE_OPEN_EXIT:-0}" = 0 ] || exit "$FAKE_OPEN_EXIT"\n'
    'cp "$3" "$FAKE_LOG.command"\nexec bash "$3"\n',
    "pkgutil": 'echo "pkgutil $*" >> "$FAKE_LOG"\n'
    '[ "$1" = --pkgs ] && printf "%s\\n" tech.blackdeep.knaif.core com.other.thing\nexit 0\n',
}


@pytest.fixture()
def volume(tmp_path: Path):
    """A scratch target volume with fake tools first on PATH. Returns (vol, env, log)."""
    vol = tmp_path / "vol"
    (vol / "usr/local/knaif/bin").mkdir(parents=True)
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    for name, body in FAKES.items():
        path = fakes / name
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    log = tmp_path / "log"
    env = {
        **os.environ,
        "PATH": ":".join([_posix(fakes), _posix(Path(_bash()).parent), "/usr/bin", "/bin"]),
        "FAKE_BIN": str(fakes),
        "FAKE_LOG": log.as_posix(),
        "FAKE_CONSOLE_USER": "alice",
    }
    return vol, env, log


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _install_script(tmp_path: Path, source: str, name: str, env_file: str = "") -> Path:
    """Lay a script out the way build-pkg.sh does: common.sh beside it, plus its .env."""
    d = tmp_path / f"scripts-{name}-{source}"
    d.mkdir()
    shutil.copy(SCRIPTS / "common.sh", d / "common.sh")
    shutil.copy(SCRIPTS / source, d / name)
    if env_file:
        (d / env_file.split("\n", 1)[0]).write_text(
            env_file.split("\n", 1)[1], encoding="utf-8", newline="\n"
        )
    return d / name


def _run_installer_script(script: Path, vol: Path, env: dict) -> subprocess.CompletedProcess:
    # Installer's arguments: package path, install location, target volume.
    return subprocess.run(
        [_bash(), script.as_posix(), "/tmp/x.pkg", "/usr/local/knaif", vol.as_posix() + "/"],
        capture_output=True,
        text=True,
        env=env,
    )


TOOL_ENV = "tool.env\nTOOL_NAME=LibreOffice\nBREW_NAME=libreoffice\nBREW_CASK=1\nSKILL=documents\n"


def _queue(vol: Path) -> Path:
    """The steps the model and tool scripts leave for finish-postinstall.sh's Terminal window."""
    return vol / "usr/local/knaif/.setup-steps"


def test_a_tool_is_queued_for_the_window_not_installed_by_setup(tmp_path: Path, volume) -> None:
    """pkgbuild gives every package script 600 s and Installer.app shows none of its output."""
    vol, env, log = volume
    (vol / "usr/local/knaif/skills/documents").mkdir(parents=True)
    _write_exe(vol / "opt/homebrew/bin/brew", 'echo "brew $*" >> "$FAKE_LOG"\n')
    script = _install_script(tmp_path, "tool-postinstall.sh", "postinstall", TOOL_ENV)
    proc = _run_installer_script(script, vol, env)
    assert proc.returncode == 0, proc.stderr
    assert not log.exists(), "setup itself must not run brew"
    assert "install --cask libreoffice" in _queue(vol).read_text()
    assert "Terminal window" in proc.stdout


@pytest.mark.parametrize(
    "setup, expect",
    [
        ("no-skill", "the documents skill was not installed"),
        ("no-brew", "Homebrew is not installed"),
        ("no-user", "nobody is logged in"),
    ],
)
def test_a_tool_is_skipped_not_failed(tmp_path: Path, volume, setup: str, expect: str) -> None:
    vol, env, log = volume
    if setup != "no-skill":
        (vol / "usr/local/knaif/skills/documents").mkdir(parents=True)
    if setup != "no-brew":
        _write_exe(vol / "opt/homebrew/bin/brew", 'echo "brew $*" >> "$FAKE_LOG"\n')
    if setup == "no-user":
        env = {**env, "FAKE_CONSOLE_USER": ""}
    script = _install_script(tmp_path, "tool-postinstall.sh", "postinstall", TOOL_ENV)
    proc = _run_installer_script(script, vol, env)
    assert proc.returncode == 0
    assert expect in proc.stdout
    assert not log.exists(), "brew must not run"
    assert not _queue(vol).exists(), "nothing is left for the window"


MODEL_ENV = "model.env\nMODEL=knaif-test-v9\n"


def _fake_knaif(vol: Path, listing: str, pull_exit: int = 0) -> None:
    _write_exe(
        vol / "usr/local/knaif/bin/knaif",
        'echo "knaif $*" >> "$FAKE_LOG"\n'
        f'[ "$1 $2" = "models list" ] && printf "%b" "{listing}"\n'
        f'[ "$1 $2" = "models pull" ] && exit {pull_exit}\nexit 0\n',
    )


def test_the_model_is_queued_for_the_window_not_pulled_by_setup(tmp_path: Path, volume) -> None:
    vol, env, log = volume
    _fake_knaif(vol, "  knaif-test-v9    available\\n")
    script = _install_script(tmp_path, "model-postinstall.sh", "postinstall", MODEL_ENV)
    proc = _run_installer_script(script, vol, env)
    assert proc.returncode == 0, proc.stderr
    assert "models pull" not in log.read_text(), "setup itself must not download"
    assert "models pull knaif-test-v9" in _queue(vol).read_text()
    assert "Terminal window" in proc.stdout


def test_an_installed_model_is_not_downloaded_again(tmp_path: Path, volume) -> None:
    vol, env, log = volume
    _fake_knaif(vol, "  knaif-test-v9    installed\\n")
    script = _install_script(tmp_path, "model-postinstall.sh", "postinstall", MODEL_ENV)
    proc = _run_installer_script(script, vol, env)
    assert proc.returncode == 0
    assert "already installed" in proc.stdout
    assert "models pull" not in log.read_text()
    assert not _queue(vol).exists()


def _setup_with_a_tool_and_the_model(
    tmp_path: Path, vol: Path, env: dict, brew_exit: int = 0, pull_exit: int = 0
) -> subprocess.CompletedProcess:
    """Run the tool, model and finish scripts in the order Installer runs their packages."""
    (vol / "usr/local/knaif/skills/documents").mkdir(parents=True)
    _write_exe(vol / "opt/homebrew/bin/brew", f'echo "brew $*" >> "$FAKE_LOG"\nexit {brew_exit}\n')
    _fake_knaif(vol, "  knaif-test-v9    available\\n", pull_exit=pull_exit)
    for source, env_file in (
        ("tool-postinstall.sh", TOOL_ENV),
        ("model-postinstall.sh", MODEL_ENV),
    ):
        script = _install_script(tmp_path, source, "postinstall", env_file)
        assert _run_installer_script(script, vol, env).returncode == 0
    finish = _install_script(tmp_path, "finish-postinstall.sh", "postinstall")
    return _run_installer_script(finish, vol, env)


def test_one_terminal_window_runs_every_queued_step_in_order(tmp_path: Path, volume) -> None:
    """Installer.app shows nothing a script prints: the window is where progress shows."""
    vol, env, log = volume
    proc = _setup_with_a_tool_and_the_model(tmp_path, vol, env)
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text()
    assert calls.count("open -a Terminal") == 1
    assert "launchctl asuser 501" in calls, "Terminal must open in the user's own session"
    brew = calls.index("brew install --cask libreoffice")
    assert brew < calls.index("knaif models pull knaif-test-v9"), "the options page's order"
    assert set(Path(f"{log}.sudo").read_text().split()) == {"alice"}
    assert not _queue(vol).exists(), "the queue is used once"
    assert "Done." in proc.stdout  # what the window shows; the fake `open` runs it here


def test_the_window_names_what_failed_and_how_to_run_it_again(tmp_path: Path, volume) -> None:
    vol, env, _log = volume
    proc = _setup_with_a_tool_and_the_model(tmp_path, vol, env, brew_exit=1)
    assert proc.returncode == 0
    shown = proc.stdout.split("Run these again any time", 1)
    assert len(shown) == 2, proc.stdout
    assert "brew install --cask libreoffice" in shown[1]
    assert "knaif models pull" not in shown[1], "the model downloaded; only the tool is named"


def test_the_window_s_script_removes_itself(tmp_path: Path, volume) -> None:
    vol, env, log = volume
    _setup_with_a_tool_and_the_model(tmp_path, vol, env)
    command = Path(f"{log}.command").read_text()
    assert command.startswith("#!/bin/bash\n")
    assert 'rm -rf "$here"' in command


@pytest.mark.parametrize(
    "why, env_change",
    [
        ("could not open a Terminal window", {"FAKE_OPEN_EXIT": "1"}),
        ("nobody is logged in", {"FAKE_CONSOLE_USER": ""}),
    ],
)
def test_without_a_window_nothing_runs_and_every_command_is_logged(
    tmp_path: Path, volume, why: str, env_change: dict
) -> None:
    vol, env, log = volume
    (vol / "usr/local/knaif/skills/documents").mkdir(parents=True)
    _write_exe(vol / "opt/homebrew/bin/brew", 'echo "brew $*" >> "$FAKE_LOG"\n')
    _fake_knaif(vol, "  knaif-test-v9    available\\n")
    for source, env_file in (
        ("tool-postinstall.sh", TOOL_ENV),
        ("model-postinstall.sh", MODEL_ENV),
    ):
        script = _install_script(tmp_path, source, "postinstall", env_file)
        _run_installer_script(script, vol, env)
    finish = _install_script(tmp_path, "finish-postinstall.sh", "postinstall")
    proc = _run_installer_script(finish, vol, {**env, **env_change})
    assert proc.returncode == 0
    assert why in proc.stdout
    assert "brew install --cask libreoffice" in proc.stdout
    assert "knaif models pull knaif-test-v9" in proc.stdout
    calls = log.read_text()
    assert "brew install" not in calls and "models pull" not in calls
    assert not _queue(vol).exists()


def test_with_nothing_queued_no_window_opens(tmp_path: Path, volume) -> None:
    vol, env, log = volume
    finish = _install_script(tmp_path, "finish-postinstall.sh", "postinstall")
    proc = _run_installer_script(finish, vol, env)
    assert proc.returncode == 0
    assert not log.exists() or "open" not in log.read_text()


def test_the_options_page_says_where_downloads_and_installs_show() -> None:
    root = _distribution()
    choice = {c.get("id"): c for c in root.iter("choice")}
    assert "Terminal window" in choice["model"].get("title", "")
    assert "setup will wait" not in choice["model"].get("title", "")
    assert "Terminal window" in choice["tools"].get("description", "")


def test_the_finish_package_is_hidden_always_installed_and_last() -> None:
    """Installer installs packages in choices-outline order; the window must see every step."""
    root = _distribution()
    finish = next(c for c in root.iter("choice") if c.get("id") == "finish")
    assert finish.get("visible") == "false"
    assert finish.get("selected") == "true" and finish.get("enabled") == "false"
    outline = root.find("choices-outline")
    assert outline is not None
    assert [line.get("choice") for line in outline.iter("line")][-1] == "finish"


def test_upgrade_clears_only_the_program_folders(tmp_path: Path, volume) -> None:
    vol, env, _log = volume
    root = vol / "usr/local/knaif"
    for d in ("bin", "skills/io", "contracts", "licenses"):
        (root / d).mkdir(parents=True, exist_ok=True)
        (root / d / "stale").write_text("old")
    (root / "keep.txt").write_text("not ours to delete")
    # An aborted install's queue must not run in this install's window.
    (root / ".setup-steps").write_text("step old")
    script = _install_script(tmp_path, "core-preinstall.sh", "preinstall")
    proc = _run_installer_script(script, vol, env)
    assert proc.returncode == 0, proc.stderr
    assert sorted(p.name for p in root.iterdir()) == ["keep.txt"]


def _fake_daemon_knaif(vol: Path, stop_exit: int = 0) -> None:
    """An installed knaif whose `daemon stop` is logged and exits `stop_exit`."""
    _write_exe(
        vol / "usr/local/knaif/bin/knaif",
        f'echo "knaif $*" >> "$FAKE_LOG"\n[ "$1 $2" = "daemon stop" ] && exit {stop_exit}\nexit 0\n',
    )


def test_upgrade_stops_the_daemon_as_the_console_user_first(tmp_path: Path, volume) -> None:
    # The daemon outlives every run and would keep serving the old build (knaif.iss: StopDaemon).
    vol, env, log = volume
    _fake_daemon_knaif(vol)
    script = _install_script(tmp_path, "core-preinstall.sh", "preinstall")
    proc = _run_installer_script(script, vol, env)
    assert proc.returncode == 0, proc.stderr
    assert "knaif daemon stop" in log.read_text()
    assert Path(f"{log}.sudo").read_text().split() == ["alice"]
    assert not (vol / "usr/local/knaif/bin").exists(), "the old program is still cleared"


@pytest.mark.parametrize(
    "stop_exit, console_user",
    [(2, "alice"), (1, "alice"), (0, "")],
    ids=["release-without-daemon", "daemon-would-not-stop", "nobody-logged-in"],
)
def test_a_daemon_that_cannot_be_stopped_never_fails_the_upgrade(
    tmp_path: Path, volume, stop_exit: int, console_user: str
) -> None:
    vol, env, log = volume
    _fake_daemon_knaif(vol, stop_exit)
    script = _install_script(tmp_path, "core-preinstall.sh", "preinstall")
    proc = _run_installer_script(script, vol, {**env, "FAKE_CONSOLE_USER": console_user})
    assert proc.returncode == 0, proc.stderr
    assert not (vol / "usr/local/knaif/bin").exists()
    if not console_user:
        assert not log.exists() or "daemon stop" not in log.read_text()


def test_uninstall_stops_the_invoking_users_daemon(tmp_path: Path, volume) -> None:
    vol, env, log = volume
    _fake_daemon_knaif(vol, stop_exit=1)
    env = {**env, "KNAIF_PKG_VOLUME": vol.as_posix(), "SUDO_USER": "bob"}
    proc = subprocess.run([_bash(), UNINSTALL.as_posix()], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "knaif daemon stop" in log.read_text()
    assert Path(f"{log}.sudo").read_text().split() == ["bob"]
    assert not (vol / "usr/local/knaif").exists(), "a daemon that will not stop never blocks it"


needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="Git Bash cannot make real symlinks")


@needs_symlinks
def test_the_path_link_is_made_and_a_foreign_one_is_left(tmp_path: Path, volume) -> None:
    vol, env, _log = volume
    script = _install_script(tmp_path, "path-postinstall.sh", "postinstall")
    link = vol / "usr/local/bin/knaif"
    assert _run_installer_script(script, vol, env).returncode == 0
    assert os.readlink(link) == "/usr/local/knaif/bin/knaif"
    # Idempotent on reinstall.
    assert "already in place" in _run_installer_script(script, vol, env).stdout
    # Something else's file is never replaced.
    link.unlink()
    link.write_text("another knaif")
    proc = _run_installer_script(script, vol, env)
    assert proc.returncode == 0 and "not replacing" in proc.stdout
    assert link.read_text() == "another knaif"


def test_uninstall_removes_the_install_and_the_receipts(tmp_path: Path, volume) -> None:
    vol, env, log = volume
    home = vol / "Users" / "alice"
    (home / ".knaif/models").mkdir(parents=True)
    (vol / "usr/local/knaif/bin/knaif").write_text("x")
    env = {**env, "KNAIF_PKG_VOLUME": vol.as_posix(), "SUDO_USER": ""}
    proc = subprocess.run([_bash(), UNINSTALL.as_posix()], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert not (vol / "usr/local/knaif").exists()
    assert "pkgutil --forget tech.blackdeep.knaif.core" in log.read_text()
    assert "com.other.thing" not in log.read_text().replace("--pkgs", "")
    assert (home / ".knaif/models").is_dir(), "models are kept without --purge"


# -- build-pkg.sh, with pkgbuild/productbuild faked --------------------------------------------

FAKE_PKGBUILD = r"""
out="${@: -1}"
echo "$*" > "$out.args"
while [ $# -gt 1 ]; do
  case "$1" in
    --scripts) cp -R "$2" "$out.scripts"; shift 2 ;;
    --root) cp -R "$2" "$out.root"; shift 2 ;;
    --info) cp "$2" "$out.info"; shift 2 ;;
    *) shift ;;
  esac
done
touch "$out"
"""

FAKE_PRODUCTBUILD = r"""
out="${@: -1}"
echo "$*" > "$out.args"
while [ $# -gt 1 ]; do
  case "$1" in
    --distribution) cp "$2" "$out.Distribution.xml"; shift 2 ;;
    --package-path) ls "$2" > "$out.packages"; cp -R "$2" "$out.pkgs"; shift 2 ;;
    --resources) cp -R "$2" "$out.resources"; shift 2 ;;
    *) shift ;;
  esac
done
touch "$out"
"""


@pytest.fixture()
def built(tmp_path: Path, volume):
    """Run build-pkg.sh over a fake staged tree; returns the output .pkg path."""
    _vol, env, _log = volume
    fakes = Path(env["FAKE_BIN"])
    _write_exe(fakes / "pkgbuild", FAKE_PKGBUILD)
    _write_exe(fakes / "productbuild", FAKE_PRODUCTBUILD)
    stage = tmp_path / "stage"
    for d in ("bin", "contracts/models", "licenses", "skills/ffmpeg", "skills/documents"):
        (stage / d).mkdir(parents=True)
    _write_exe(stage / "bin/knaif", "exit 0\n")
    for f in ("LICENSE", "NOTICE", "README.txt"):
        (stage / f).write_text(f)
    out = tmp_path / "out" / "knaif.pkg"
    proc = subprocess.run(
        [_bash(), BUILD.as_posix(), stage.as_posix(), "--out", out.as_posix()],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out


def test_build_pkg_builds_every_component_package(built: Path) -> None:
    # The fake pkgbuild leaves .args/.root/.scripts sidecars beside each package it "builds".
    packages = {p for p in Path(f"{built}.packages").read_text().split() if p.endswith(".pkg")}
    refs = {(r.text or "").strip() for r in _distribution().iter("pkg-ref") if r.text}
    assert packages == refs


def test_build_pkg_fills_the_distribution_from_cargo_and_the_manifest(built: Path) -> None:
    text = Path(f"{built}.Distribution.xml").read_text(encoding="utf-8")
    assert "@" not in re.sub(r"<!--.*?-->", "", text, flags=re.S)
    ver = re.search(r'^version = "([^"]+)"', (REPO / "Cargo.toml").read_text(), re.M)
    assert ver and f"<title>knaif {ver.group(1)}</title>" in text
    manifest = yaml.safe_load(
        (REPO / "contracts/models/model-manifest.yaml").read_text(encoding="utf-8")
    )
    assert manifest["recommendations"]["desktop"] in text
    ET.fromstring(text)


def _templated_kbytes(pkgs: Path, name: str) -> int:
    payload = ET.fromstring(Path(f"{pkgs}/{name}.pkg.info").read_text()).find("payload")
    assert payload is not None, name
    return int(payload.get("installKBytes", "0"))


def test_build_pkg_sizes_the_script_only_choices(built: Path) -> None:
    # These packages have no payload, so their size is templated into their PackageInfo (what
    # productbuild reads); without it Installer shows "Zero KB" for a 2.5 GB model download.
    manifest = yaml.safe_load(
        (REPO / "contracts/models/model-manifest.yaml").read_text(encoding="utf-8")
    )
    size = manifest["models"][manifest["recommendations"]["desktop"]]["size_bytes"]
    pkgs = Path(f"{built}.pkgs")
    assert _templated_kbytes(pkgs, "model") == -(-size // 1024)
    # The Homebrew tools carry estimates (the options page says so); every one has one.
    tools = [f"tool-{row[0]}" for row in _tools_table()]
    assert all(_templated_kbytes(pkgs, t) > 0 for t in tools)
    # Payload packages are sized by pkgbuild itself, and the PATH link is only a symlink.
    assert sorted(p.name for p in pkgs.glob("*.info")) == sorted(
        f"{n}.pkg.info" for n in ["model", *tools]
    )


def test_build_pkg_wires_each_tool_script_to_its_table_row(built: Path) -> None:
    pkgs = Path(f"{built}.pkgs")
    for tid, _display, brew, cask, skill in _tools_table():
        scripts = Path(f"{pkgs}/tool-{tid}.pkg.scripts")
        env = (scripts / "tool.env").read_text()
        assert f"BREW_NAME={brew}" in env and f"BREW_CASK={cask}" in env
        assert f"SKILL={skill}" in env
        assert (scripts / "postinstall").read_text() == (
            SCRIPTS / "tool-postinstall.sh"
        ).read_text()
        assert (scripts / "common.sh").is_file()


def test_build_pkg_puts_the_uninstaller_in_core_and_skills_in_their_own(built: Path) -> None:
    pkgs = Path(f"{built}.pkgs")
    core = Path(f"{pkgs}/core.pkg.root")
    assert (core / "uninstall.sh").is_file() and (core / "bin/knaif").is_file()
    assert not (core / "skills").exists()
    assert (Path(f"{pkgs}/skill-ffmpeg.pkg.root") / "skills/ffmpeg").is_dir()
    args = Path(f"{pkgs}/core.pkg.args").read_text()
    assert "--install-location /usr/local/knaif" in args
    assert "--identifier tech.blackdeep.knaif.core" in args
    assert "--ownership recommended" in args


def test_build_pkg_builds_the_finish_package_from_its_script(built: Path) -> None:
    scripts = Path(f"{built}.pkgs/finish.pkg.scripts")
    assert (scripts / "postinstall").read_text() == (SCRIPTS / "finish-postinstall.sh").read_text()
    assert (scripts / "common.sh").is_file()


def test_build_pkg_is_unsigned_unless_asked(built: Path) -> None:
    assert "--sign" not in Path(f"{built}.args").read_text()
