"""Lint `installers/windows/knaif.iss` against the contracts it duplicates.

The Windows installer restates, in a language that cannot read YAML, things the repo
already declares elsewhere: which external tools each skill needs, which of them are
mandatory, and how their commands are satisfied. Every one of those is a copy, and copies
drift.

**F2 is why this file exists.** v1.0.1 shipped AGPL Ghostscript and a ~350 MB LibreOffice
*pre-checked* against the script's own stated intent, because the task entries used dotted
``deps\\<x>`` names. A dotted name declares a parent task; with no such parent in
``[Tasks]`` Inno renders the children under the preceding (checked) task and silently
discards both their ``unchecked`` flag and their ``GroupDescription``. Nothing failed,
nothing warned, and reading the script does not reveal it — the wizard page is the only
place the bug is visible, and per the plan's *Verification protocol* that page cannot be
probed without a human looking at it.

The bug class, however, **is fully decidable from the text of the script**, which is what
this module exploits. It is a text lint, not an Inno emulator: it catches undeclared
references, lost defaults and contract drift. It cannot catch registry state (F1), the
task-vs-``[Run]`` placement semantics (F3), or anything about rendering — those stay
manual.

The parsing idiom (path constant + regex over the raw text) follows
``test_version_consistency.py``, which already reads ``AppVersion`` out of this file.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(".").resolve()
ISS = ROOT / "installers" / "windows" / "knaif.iss"
SKILLS = ROOT / "skills"
DEPS_RS = ROOT / "native" / "crates" / "knaif-core" / "src" / "deps.rs"

# Inno functions a `Check:` may name without them being defined in [Code].
INNO_BUILTINS = {"expandconstant", "istaskselected", "iscomponentselected", "not", "and", "or"}


# --------------------------------------------------------------------------------------
# A very small Inno Setup reader
# --------------------------------------------------------------------------------------


def _sections(text: str) -> dict[str, list[str]]:
    """Split the script into ``{section: [logical lines]}``.

    Handles the two syntactic features this file uses: ``\\``-continued lines and
    whole-line ``;`` comments. ``[Code]`` is kept as raw text — there ``;`` terminates a
    Pascal statement rather than separating parameters, so the entry parser must never see
    it.
    """
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    pending = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        header = re.match(r"^\[(\w+)\]\s*$", line.strip())
        if header and not pending:
            current = out.setdefault(header.group(1), [])
            continue
        if current is None:
            continue
        stripped = line.strip()
        if not pending and (not stripped or stripped.startswith(";")):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1]
            continue
        current.append(pending + stripped)
        pending = ""
    return out


def _split_params(line: str) -> list[str]:
    """Split one entry on ``;``, honouring double quotes — exactly as Inno does.

    Only *double* quotes protect a semicolon. A Pascal string in a ``Check:`` argument is
    single-quoted, so ``Check: F('a;b')`` really is split by Inno into two parameters and
    breaks; see ``test_every_parameter_is_a_key_value_pair``.
    """
    parts: list[str] = []
    buf = ""
    in_quotes = False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
            buf += ch
        elif ch == ";" and not in_quotes:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"')
    return value


def _entries(section: str) -> list[dict[str, str]]:
    """Parse a section into a list of ``{key: value}`` parameter dicts."""
    result = []
    for line in SECTIONS.get(section, []):
        entry = {}
        for part in _split_params(line):
            key, sep, value = part.partition(":")
            if not sep:
                continue
            entry[key.strip()] = _unquote(value.strip())
        result.append(entry)
    return result


def _flags(entry: dict[str, str]) -> set[str]:
    return set(entry.get("Flags", "").split())


def _code() -> str:
    """The raw ``[Code]`` body."""
    return ISS.read_text(encoding="utf-8").split("[Code]", 1)[1]


ISS_TEXT = ISS.read_text(encoding="utf-8")
SECTIONS = _sections(ISS_TEXT)
TASKS = _entries("Tasks")
COMPONENTS = _entries("Components")
FILES = _entries("Files")
RUN = _entries("Run")
REGISTRY = _entries("Registry")
INSTALL_DELETE = _entries("InstallDelete")

TASK_NAMES = {t["Name"] for t in TASKS}
COMPONENT_NAMES = {c["Name"] for c in COMPONENTS}


# --------------------------------------------------------------------------------------
# The script parses at all
# --------------------------------------------------------------------------------------


def test_sections_are_all_present() -> None:
    """A typo in a section header silently drops every entry under it.

    Inno ignores an unrecognised section rather than erroring, so ``[Installdelet]`` would
    compile happily and simply stop clearing the stale payload. Naming the sections here
    turns that into a failure with the cause in the message.
    """
    required = {
        "Setup",
        "Types",
        "Components",
        "Tasks",
        "Files",
        "InstallDelete",
        "Registry",
        "Run",
    }
    assert required <= set(SECTIONS), f"missing section(s): {sorted(required - set(SECTIONS))}"


def test_every_parameter_is_a_key_value_pair() -> None:
    """Nothing splits into a fragment Inno cannot read as ``Key: value``.

    This is the guard against an unquoted semicolon inside a value. A command list written
    ``Check: ShouldInstallAll('ffmpeg;ffprobe')`` looks correct and compiles, but Inno cuts
    it at the semicolon and the trailing half becomes a nameless parameter — which is why
    the command lists in this script are comma-separated.
    """
    for section in ("Components", "Tasks", "Files", "Run", "Registry", "InstallDelete"):
        for line in SECTIONS.get(section, []):
            for part in _split_params(line):
                assert (
                    ":" in part
                ), f"[{section}] fragment with no parameter name: {part!r}\n  in: {line}"


# --------------------------------------------------------------------------------------
# References resolve  (F2)
# --------------------------------------------------------------------------------------


def test_task_parents_are_declared() -> None:
    """A dotted task name needs its parent in [Tasks] — this is F2 exactly."""
    for task in TASKS:
        name = task["Name"]
        if "\\" not in name:
            continue
        parent = name.rsplit("\\", 1)[0]
        assert parent in TASK_NAMES, (
            f"task {name!r} declares parent {parent!r}, which is not defined in [Tasks]. "
            "Inno renders it under the preceding task instead, discarding its Flags and "
            "GroupDescription."
        )


def test_dependency_tasks_are_flat() -> None:
    """Declaring a real parent does *not* rescue the dep tasks, so they must stay flat.

    ``test_task_parents_are_declared`` would be satisfied by adding a ``deps`` parent, and
    that fix is wrong: Inno force-checks the children of a checked parent, so the moment
    anyone ticks it the per-task defaults break again the same way. Flat names are the only
    shape in which ``unchecked`` survives.
    """
    for task in TASKS:
        if task["Name"].startswith("deps"):
            assert "\\" not in task["Name"], f"dependency task {task['Name']!r} must be flat"


def test_component_parents_are_declared() -> None:
    for component in COMPONENTS:
        name = component["Name"]
        if "\\" not in name:
            continue
        parent = name.rsplit("\\", 1)[0]
        assert parent in COMPONENT_NAMES, f"component {name!r} has undeclared parent {parent!r}"


def test_task_filters_resolve() -> None:
    """Every ``Tasks:`` filter names a task that exists.

    A misspelled filter is not an error in Inno — the entry simply never runs, so a winget
    install or the PATH registry write would silently do nothing.
    """
    for section, entries in (("Files", FILES), ("Run", RUN), ("Registry", REGISTRY)):
        for entry in entries:
            for name in entry.get("Tasks", "").split():
                assert name in TASK_NAMES, f"[{section}] filters on undeclared task {name!r}"


def test_component_filters_resolve() -> None:
    for section, entries in (("Files", FILES), ("Run", RUN), ("Tasks", TASKS)):
        for entry in entries:
            for name in entry.get("Components", "").split():
                assert (
                    name in COMPONENT_NAMES
                ), f"[{section}] filters on undeclared component {name!r}"


def test_check_functions_are_defined_in_code() -> None:
    """Every ``Check:`` names something [Code] actually defines.

    Inno resolves these at compile time, so a renamed function is caught by ISCC — but only
    if someone compiles. This puts it in the test suite, where a rename of e.g.
    ``ShouldInstallAll`` fails on the machine that made it.
    """
    code = _code()
    defined = {m.lower() for m in re.findall(r"^\s*(?:function|procedure)\s+(\w+)", code, re.M)}
    for section, entries in (
        ("Tasks", TASKS),
        ("Files", FILES),
        ("Run", RUN),
        ("Registry", REGISTRY),
    ):
        for entry in entries:
            expression = entry.get("Check")
            if not expression:
                continue
            # Drop Pascal string literals first, or '{app}\bin' contributes identifiers.
            bare = re.sub(r"'[^']*'", "", expression)
            for identifier in re.findall(r"[A-Za-z_]\w*", bare):
                if identifier.lower() in INNO_BUILTINS:
                    continue
                assert identifier.lower() in defined, (
                    f"[{section}] Check: {expression!r} names {identifier!r}, "
                    "which is not defined in [Code]"
                )


# --------------------------------------------------------------------------------------
# The winget offers mirror skills/<skill>/skill.yaml
# --------------------------------------------------------------------------------------


def _declared_offers() -> dict[tuple[str, frozenset[str]], tuple[str, bool, str]]:
    """What ``skills/*/skill.yaml`` says the installer should offer.

    ``skills/<name>/skill.yaml`` is the source of truth — not ``deps.rs``, which holds the
    schema and the probe but whose alias lists are ``#[cfg(test)]`` fixtures. Only skills
    that have an installer component are considered, and only tools whose Windows install
    channel is winget: a tool with no ``install.windows`` has nothing for the installer to
    run.

    Returns ``{(component, commands): (mode, default_checked, tool_name)}``.
    """
    offers = {}
    for component in COMPONENT_NAMES:
        if not component.startswith("skills\\"):
            continue
        skill = component.split("\\", 1)[1]
        manifest = yaml.safe_load((SKILLS / skill / "skill.yaml").read_text(encoding="utf-8"))
        for tool in manifest.get("dependencies", {}).get("external_tools", []):
            if tool.get("install", {}).get("windows") != "winget":
                continue
            mode = "all" if tool.get("all_required") else "any"
            key = (component, frozenset(tool["commands"]))
            offers[key] = (mode, bool(tool.get("required")), tool["name"])
    return offers


def _installer_offers() -> dict[tuple[str, frozenset[str]], tuple[str, bool, str]]:
    """What the script actually offers: ``{(component, commands): (mode, checked, task)}``."""
    by_name = {t["Name"]: t for t in TASKS}
    offers = {}
    for entry in RUN:
        if entry.get("Filename") != "winget":
            continue
        match = re.search(r"ShouldInstall(All|Any)\('([^']*)'\)", entry.get("Check", ""))
        assert match, f"winget [Run] entry has no ShouldInstallAll/Any check: {entry}"
        task_name = entry.get("Tasks", "")
        task = by_name[task_name]
        key = (task["Components"], frozenset(match.group(2).split(",")))
        offers[key] = (match.group(1).lower(), "unchecked" not in _flags(task), task_name)
    return offers


def test_winget_offers_match_the_skill_contracts() -> None:
    """Same tools, same command lists, in both directions.

    ISPP cannot read YAML, so the command lists in ``[Run]`` are a hand copy of
    ``dependencies.external_tools``. Adding a tool to a skill without adding it here means
    the installer silently stops offering something the skill needs; renaming a command
    means the probe checks for a binary nobody ships.
    """
    declared = _declared_offers()
    installed = _installer_offers()

    def label(offers):
        return sorted((c, sorted(cmds), o[2]) for (c, cmds), o in offers.items())

    assert set(declared) == set(installed), (
        "installer offers and skill contracts disagree\n"
        f"  skill.yaml declares: {label(declared)}\n"
        f"  knaif.iss offers:    {label(installed)}"
    )


def test_offer_satisfaction_mode_follows_all_required() -> None:
    """``all_required: true`` must reach the script as ``ShouldInstallAll``.

    The two are not interchangeable. ``ffmpeg`` declares ``all_required`` over
    ``[ffmpeg, ffprobe]`` because they are distinct binaries and both are needed; probing
    them as aliases reports satisfied when only ``ffmpeg`` is on PATH, and the ffmpeg skill
    then fails at runtime on a box the installer called ready.
    """
    declared = _declared_offers()
    for key, (mode, _, task) in _installer_offers().items():
        if key not in declared:
            continue  # reported by test_winget_offers_match_the_skill_contracts
        expected, _, name = declared[key]
        assert mode == expected, (
            f"task {task!r} uses ShouldInstall{mode.capitalize()} but {name!r} declares "
            f"all_required: {expected == 'all'}"
        )


def test_dependency_task_defaults_follow_the_required_flag() -> None:
    """A tool the skill marks ``required`` defaults on; everything else defaults off.

    This is the assertion F2 needed. ffmpeg is ``required: true`` for the ffmpeg skill, so
    its task is checked; Ghostscript (AGPL) and LibreOffice (~350 MB) are optional and must
    arrive unchecked, which is what v1.0.1 got wrong.
    """
    declared = _declared_offers()
    for key, (_, checked, task) in _installer_offers().items():
        if key not in declared:
            continue
        _, expected, name = declared[key]
        assert checked == expected, (
            f"task {task!r} defaults {'checked' if checked else 'unchecked'} but {name!r} "
            f"declares required: {expected}. Optional tools need `Flags: unchecked`."
        )


def test_dependency_tasks_share_one_group_heading() -> None:
    """The other half of F2: a nested task loses its ``GroupDescription`` too.

    All dependency tasks belong under one heading; a task that has drifted out of the group
    renders under whatever precedes it.
    """
    groups = {t["Name"]: t.get("GroupDescription") for t in TASKS if t["Name"].startswith("deps")}
    assert groups, "no dependency tasks found — has the naming changed?"
    assert len(set(groups.values())) == 1, f"dependency tasks split across headings: {groups}"
    assert all(groups.values()), f"dependency task with no GroupDescription: {groups}"


def test_probe_honours_the_runtime_env_override() -> None:
    """The Pascal probe reads the same ``$KNAIF_<CMD>_BIN`` override the runtime does.

    ``resolve_command`` returns the override without probing PATH, so a user who set it is
    already satisfied. An installer that ignored it would offer a winget install the runtime
    considers unnecessary.
    """
    env_key = re.search(r'format!\("(KNAIF_\{\}_BIN)"', DEPS_RS.read_text(encoding="utf-8"))
    assert env_key, "deps.rs no longer builds a KNAIF_<CMD>_BIN key — update the installer probe"
    assert (
        "'KNAIF_' + Uppercase(Cmd) + '_BIN'" in _code()
    ), "the [Code] probe no longer reads $KNAIF_<CMD>_BIN, so it has diverged from deps.rs"


# --------------------------------------------------------------------------------------
# The stale payload is cleared  (F9)
# --------------------------------------------------------------------------------------


def _installed_dirs() -> set[str]:
    return {e["DestDir"] for e in FILES if e.get("DestDir", "").startswith("{app}\\")}


def test_installdelete_covers_every_installed_subdirectory() -> None:
    """Everything [Files] writes under ``{app}\\`` is wiped first.

    Without this, deselecting a skill on a reinstall leaves the old one on disk and still
    listed by ``knaif skills list``, and a file dropped between releases survives forever.
    A new ``DestDir`` that nobody adds to ``[InstallDelete]`` reintroduces that quietly.
    """
    cleared = [e["Name"] for e in INSTALL_DELETE]
    for dest in sorted(_installed_dirs()):
        assert any(
            dest == c or dest.startswith(c + "\\") for c in cleared
        ), f"[Files] installs into {dest!r} but [InstallDelete] never clears it: {cleared}"


def test_installdelete_never_targets_the_app_root() -> None:
    """``{app}`` itself must not be wiped — ``unins000.exe`` lives there.

    The section is safe only because every entry is pure staged payload. Widening it to the
    app root would delete the uninstaller mid-install and strand the install exactly the way
    F1 did.
    """
    for entry in INSTALL_DELETE:
        assert entry["Name"] != "{app}", "[InstallDelete] must never target {app} itself"


def test_installdelete_runs_only_over_staged_payload() -> None:
    """No ``[InstallDelete]`` entry reaches outside ``{app}``.

    The user data dir (~/.knaif — the 2.5 GB model store and the opt-in backends payload)
    is deliberately outside ``{app}`` so it survives an upgrade. An entry pointing anywhere
    else turns a wipe-and-recopy into data loss.
    """
    for entry in INSTALL_DELETE:
        assert entry["Name"].startswith(
            "{app}\\"
        ), f"[InstallDelete] entry {entry['Name']!r} is outside the app dir"


# --------------------------------------------------------------------------------------
# The opt-in CUDA backend task (U4)
# --------------------------------------------------------------------------------------
#
# The installer duplicates two facts it cannot read from their source: ISPP has no YAML
# parser, so the CUDA driver floor and the payload's filename are `#define`s here. Both
# have an authoritative home in contracts/backends/backend-manifest.yaml, and a duplicate
# that drifts is worse than no duplicate — it decides, silently, whether a user is offered
# a 668 MB download that can never load.


def _define(name: str) -> str:
    match = re.search(rf'^\s*#define\s+{name}\s+"([^"]+)"', ISS_TEXT, re.M)
    assert match, f"no #define {name} in knaif.iss"
    return match.group(1)


def _backend_manifest() -> dict:
    import yaml

    path = ROOT / "contracts" / "backends" / "backend-manifest.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _cuda_task() -> dict[str, str]:
    for task in TASKS:
        if task.get("Name") == "cudabackend":
            return task
    raise AssertionError("no `cudabackend` task in [Tasks]")


def test_cuda_driver_floor_matches_the_backend_manifest() -> None:
    declared = (
        ((_backend_manifest().get("backends") or {}).get("cuda") or {}).get("requires") or {}
    ).get("min_driver")
    assert str(declared) == _define("MinNvidiaDriver"), (
        f"installer MinNvidiaDriver={_define('MinNvidiaDriver')!r} but the backend manifest "
        f"declares requires.min_driver={declared!r}. Below the real floor the installer offers a "
        f"~668 MB payload that then fails to load; above it, it silently withholds one that works."
    )


def test_cuda_backend_filename_matches_the_backend_manifest() -> None:
    # Presence of this file is how the installer avoids re-offering a payload the user already has
    # across an upgrade (the backends dir lives outside {app} and survives one).
    windows = (
        ((_backend_manifest().get("backends") or {}).get("cuda") or {}).get("platforms") or {}
    ).get("windows-x64") or {}
    names = {f.get("name") for f in (windows.get("files") or [])}
    assert _define("CudaBackendFile") in names, (
        f"installer CudaBackendFile={_define('CudaBackendFile')!r} is not among the Windows "
        f"payload files {sorted(n for n in names if n)} — the 'already installed?' check would "
        f"never fire, so every upgrade would re-offer the download."
    )


def test_cuda_task_is_opt_in() -> None:
    # The whole point of an opt-in component. A checked-by-default 668 MB download is exactly the
    # defect that shipped for Ghostscript and LibreOffice.
    assert "unchecked" in _flags(_cuda_task()), "the cudabackend task must default to unchecked"


def test_cuda_task_is_gated() -> None:
    # Without a Check the task renders on AMD boxes and below the driver floor, where the payload
    # cannot work.
    assert (
        _cuda_task().get("Check") == "CudaOfferable"
    ), "the cudabackend task must be gated on CudaOfferable"


def test_cuda_task_is_flat() -> None:
    # Same trap as the deps tasks: a dotted name declares an undefined parent, and the child then
    # loses both its GroupDescription and its `unchecked` flag.
    assert "\\" not in _cuda_task()["Name"], "task names must be flat (see the deps-task comment)"


def test_cuda_run_entry_uses_the_same_command_a_user_would() -> None:
    # One install path, one set of checksums, one atomic swap. An installer-specific copy of the
    # download logic is a second path that can rot independently.
    entries = [e for e in _entries("Run") if e.get("Tasks", "").strip('"') == "cudabackend"]
    assert entries, "no [Run] entry is bound to the cudabackend task"
    assert len(entries) == 1, "expected exactly one CUDA [Run] entry"
    entry = entries[0]
    assert entry["Filename"] == "{app}\\bin\\knaif.exe", entry["Filename"]
    assert entry["Parameters"] == "backend install cuda", entry["Parameters"]
