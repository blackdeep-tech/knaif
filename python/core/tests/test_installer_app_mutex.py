"""The installer's `AppMutex` and the CLI's held mutex must be the same string.

`AppMutex` is what makes an upgrade *refuse to proceed while knaif is running*. Without it, setup
hits a locked `bin\\knaif.exe`, silently defers the replacement to the next reboot, and leaves the
user on the old build with no indication why.

The failure mode is that the two strings drift apart. Nothing fails when they do: the installer
simply looks for a mutex nobody creates, finds nothing, and proceeds — so the directive quietly
becomes a no-op and every upgrade goes back to the deferred-to-reboot behaviour. There is no error,
no warning, and no artifact difference.

Until this file existed the only thing that would have caught it was a human running the upgrade
path by hand, with a `knaif.exe` deliberately left running, once per release
(`docs/RELEASE.md` §4). That check is worth keeping — it proves the *directive* works, which a
string comparison cannot — but it is far too late and far too manual to be the only guard on a
one-word typo.

A text lint over two files, deliberately: the alternative needs Windows, an install, and a GUI.
"""

import re
from pathlib import Path

ROOT = Path(".").resolve()
ISS = ROOT / "installers" / "windows" / "knaif.iss"
MAIN_RS = ROOT / "apps" / "cli" / "src" / "main.rs"


def _app_mutex() -> str:
    """The `AppMutex=` value from `[Setup]`."""
    match = re.search(r"^AppMutex=(.+)$", ISS.read_text(encoding="utf-8"), re.M)
    assert (
        match
    ), "knaif.iss declares no AppMutex — an upgrade over a running CLI will defer to reboot"
    return match.group(1).strip()


def _held_mutex() -> str:
    """The name `hold_app_mutex` passes to `CreateMutexW`, minus its NUL terminator."""
    body = re.search(
        r"fn hold_app_mutex\(\)\s*\{(.+?)\n\}", MAIN_RS.read_text(encoding="utf-8"), re.S
    )
    assert body, "no hold_app_mutex in apps/cli/src/main.rs"
    name = re.search(r'"([^"]+)\\0"\s*\.encode_utf16', body.group(1))
    assert name, (
        "hold_app_mutex no longer builds a NUL-terminated UTF-16 literal; if the mutex is now "
        "named some other way, update this test rather than deleting it"
    )
    return name.group(1)


def test_the_cli_holds_the_mutex_the_installer_waits_on() -> None:
    iss, rs = _app_mutex(), _held_mutex()
    assert iss == rs, (
        f"knaif.iss AppMutex={iss!r} but hold_app_mutex creates {rs!r}. These must match exactly. "
        f"They do not fail loudly when they drift — setup looks for a mutex nothing creates, finds "
        f"none, proceeds against a locked knaif.exe, and defers the file replacement to the next "
        f"reboot. The user stays on the old build and is told nothing."
    )


def test_the_mutex_is_session_local() -> None:
    # The install is per-user, so the mutex must NOT be in the `Global\` namespace: a global name
    # would let one user's running CLI block another user's upgrade, and on a locked-down box
    # creating it can fail outright — which silently costs the installer its detection.
    assert not _held_mutex().startswith(
        "Global\\"
    ), "the app mutex must be session-local for a per-user install, not Global\\"
