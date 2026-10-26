"""Known folders: Downloads, Desktop, Documents, … named in a request instead of a path.

"save it in Downloads" means the user's real Downloads folder, not a new `Downloads` folder next
to the input. An output path whose first segment is a known-folder name (or `~`) names that
folder. The model never writes an absolute path; code resolves the folder.
"""

from __future__ import annotations

import copy
import os
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# The names a request may use, lower case. `home` is also written `~`.
KNOWN_FOLDERS: tuple[str, ...] = (
    "downloads",
    "desktop",
    "documents",
    "pictures",
    "music",
    "videos",
    "home",
)

# The folder's own name on disk, where the OS does not say otherwise.
DISPLAY_NAMES: dict[str, str] = {
    "downloads": "Downloads",
    "desktop": "Desktop",
    "documents": "Documents",
    "pictures": "Pictures",
    "music": "Music",
    "videos": "Videos",
    "home": "~",
}

# Tests and the eval lanes map every known folder under one directory.
ROOT_ENV = "KNAIF_KNOWN_FOLDERS_ROOT"

_XDG_KEYS = {
    "downloads": "XDG_DOWNLOAD_DIR",
    "desktop": "XDG_DESKTOP_DIR",
    "documents": "XDG_DOCUMENTS_DIR",
    "pictures": "XDG_PICTURES_DIR",
    "music": "XDG_MUSIC_DIR",
    "videos": "XDG_VIDEOS_DIR",
}

# FOLDERID_* for SHGetKnownFolderPath.
_WINDOWS_IDS = {
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}

# Arg keys that name where a step writes. A schema's `path_role: output` counts too.
_OUTPUT_KEYS = frozenset({"output", "output_dir"})
_INPUT_KEYS = ("inputs", "input", "files", "src", "path", "base", "append")


class SameNameFolder(ValueError):
    """A known folder named in the plan also exists, by name, next to the input: ask."""


def _windows_known_folder(name: str) -> Path:
    import ctypes
    import uuid
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    raw = uuid.UUID(_WINDOWS_IDS[name]).bytes_le
    guid = _GUID.from_buffer_copy(raw)
    out = ctypes.c_wchar_p()
    if sys.platform != "win32":
        raise OSError("known folders come from the Windows shell only on Windows")
    windll = ctypes.windll
    hr = windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
    try:
        if hr != 0 or not out.value:
            raise OSError(f"SHGetKnownFolderPath failed for {name} ({hr})")
        return Path(out.value)
    finally:
        windll.ole32.CoTaskMemFree(out)


def _xdg_dir(name: str, env: Mapping[str, str], home: Path) -> Path | None:
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    try:
        text = (config / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        return None
    key = _XDG_KEYS[name]
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip().strip('"')
            value = value.replace("$HOME", str(home))
            return Path(value) if value else None
    return None


def resolve(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
    windows_shell: Callable[[str], Path] | None = None,
) -> Path:
    """Where the known folder *name* is on this machine."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    root = env.get(ROOT_ENV)
    if root:
        # Absolute here, against the working folder: a handler joins a relative output to its
        # own root, which would nest the "known folder" inside the user's folder.
        base = Path(root).absolute()
        return base if name == "home" else base / DISPLAY_NAMES[name]
    home = Path.home() if home is None else home
    if name == "home":
        return home
    if platform == "win32":
        try:
            return (windows_shell or _windows_known_folder)(name)
        except Exception:  # noqa: BLE001 - fall back to the default layout
            return home / DISPLAY_NAMES[name]
    if platform != "darwin":
        found = _xdg_dir(name, env, home)
        if found is not None:
            return found
    return home / DISPLAY_NAMES[name]


_SEP = re.compile(r"[\\/]+")
_FOLDER = r"(?:my\s+|the\s+)?(?:downloads|desktop|documents|pictures|music|videos)(?![\w.])"
# A known folder as where something goes. "into"/"onto" always say so ("into my Music folder");
# "in"/"to"/"on" only after a verb of putting ("save it in Downloads", "put it on the Desktop"),
# because "everything in my Videos folder" says where files are, not where they go.
_INTO = re.compile(rf"\b(?:into|onto)\s+{_FOLDER}", re.IGNORECASE)
_PUT = re.compile(
    r"\b(?:save|saved|put|store|place|output|export|write|copy|move|send|drop|keep|leave)\b"
    rf".*?\b(?:in|to|on)\s+{_FOLDER}",
    re.IGNORECASE,
)


_PLACE = re.compile(rf"\b(?:in|into|to|on|onto|from)\s+{_FOLDER}(?:\s+folder\b)?", re.IGNORECASE)


def without_folder_places(text: str) -> str:
    """*text* with every known folder named as a place left out ("in my Downloads folder",
    "to Documents"): a folder there says where, not how many, so "put clip.mp4 in my Downloads
    folder" is no batch (``nl_clarify_gate.asks_for_many_files``)."""
    return _PLACE.sub(" ", text)


def names_known_folder(text: str) -> bool:
    """True when *text* names a known folder as where something goes ("save it in Downloads").

    Gates the prompt examples that write into a known folder (``prompt.select_examples``), so a
    request that names none gets the same examples as before.
    """
    return bool(_INTO.search(text) or _PUT.search(text))


def named_folder(value: str) -> str | None:
    """The known folder *value* starts with, or None.

    ``Downloads``, ``downloads/clip.mp4`` and ``~/Downloads/clip.mp4`` name ``downloads``;
    ``~/clip.mp4`` and a bare ``~`` name ``home``; ``out/Downloads/clip.mp4`` names none. A
    leading ``./`` is the model's habit, not the user's words, so ``./Desktop/x.jpg`` names
    ``desktop`` too.
    """
    parts = [p for p in _SEP.split(value.strip()) if p]
    while parts and parts[0] == ".":
        parts.pop(0)
    if not parts:
        return None
    if parts[0] == "~":
        if len(parts) > 1 and parts[1].lower() in KNOWN_FOLDERS and parts[1].lower() != "home":
            return parts[1].lower()
        return "home"
    head = parts[0].lower()
    return head if head in KNOWN_FOLDERS else None


def _rest(value: str, folder: str) -> list[str]:
    """The path parts of *value* after the known-folder segment it starts with."""
    parts = [p for p in _SEP.split(value.strip()) if p]
    while parts and parts[0] == ".":
        parts.pop(0)
    if parts and parts[0] == "~":
        parts.pop(0)
        if folder == "home":
            return parts
    if parts and parts[0].lower() == folder:
        parts.pop(0)
    return parts


def _within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _output_keys(tool_def: Any) -> set[str]:
    keys = set(_OUTPUT_KEYS)
    for arg, schema in (getattr(tool_def, "arg_schemas", None) or {}).items():
        if getattr(schema, "path_role", None) == "output":
            keys.add(arg)
    return keys


def _input_dirs(args: Mapping[str, Any], cwd: Path) -> list[Path]:
    dirs = [cwd]
    for key in _INPUT_KEYS:
        value = args.get(key)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item and not item.startswith("$") and "://" not in item:
                parent = Path(item).parent
                dirs.append(parent if parent.is_absolute() else cwd / parent)
    return dirs


def rewrite_outputs(
    plan: list[dict[str, Any]],
    registry: Mapping[str, Any],
    *,
    cwd: Path,
    sandbox: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """*plan* with every output arg that names a known folder pointed into that folder.

    ``Downloads/clip.mp4`` becomes ``<the user's Downloads>/clip.mp4``; inputs, ``$var``
    references and paths that only contain the name further in (``out/Downloads/x``) stay.
    Raises ``SameNameFolder`` when a folder of that name also exists in *cwd* or next to an
    input (contracts/parity/known_folder_cases.json), and ``ValueError`` when *sandbox* is set
    and the folder is outside it. *plan* itself is left unchanged.
    """
    out = copy.deepcopy(plan)
    for step in out:
        if not isinstance(step, dict) or not isinstance(step.get("args"), dict):
            continue
        args = step["args"]
        for key in _output_keys(registry.get(step.get("tool", ""))):
            value = args.get(key)
            if not isinstance(value, str) or value.startswith("$"):
                continue
            folder = named_folder(value)
            if folder is None:
                continue
            base = resolve(folder, env=env)
            target = base.joinpath(*_rest(value, folder))
            explicit_home = value.strip().startswith("~")
            if folder != "home" and not explicit_home:
                spelled = [p for p in _SEP.split(value.strip()) if p not in ("", ".")][0]
                for d in _input_dirs(args, cwd):
                    for name in {spelled, DISPLAY_NAMES[folder]}:
                        local = d / name
                        if local.is_dir() and not _within(local, base):
                            raise SameNameFolder(
                                f"There is a {name} folder at {local.as_posix()}, and your "
                                f"{DISPLAY_NAMES[folder]} folder is {base.as_posix()}. "
                                "Which one should I save into?"
                            )
            if sandbox is not None and not _within(target, Path(sandbox)):
                raise ValueError(
                    f"Path '{target.as_posix()}' is outside the sandbox "
                    f"'{Path(sandbox).as_posix()}'."
                )
            args[key] = target.as_posix()
    return out
