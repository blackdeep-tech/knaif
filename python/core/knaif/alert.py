"""Completion alerts: a desktop notification and a beep when a run that asked for one ends.

The model asks for it with the ``alert_when_done`` core tool; ``planner.split_alert`` takes it
out of the plan, and ``CommandAgent.execute_plan`` fires it when the run ends:
- only when the plan asked, the run was not a dry run, and work started (a step ran or a file
  was downloaded); a request refused before anything ran is answered on screen already;
- on success ("Done"), on failure ("Failed") and when a confirmation was declined mid-run
  ("Stopped").

``KNAIF_OS_SINK=<file>`` records each alert as a JSON line instead of making it, for tests and
the eval lanes. An alert never writes to stdout (the eval lanes parse it), never runs a shell,
and never puts its text into script source. It never fails a run: a backend that is missing or
errors falls back, at worst to the terminal bell on stderr.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TextIO

SINK_ENV = "KNAIF_OS_SINK"
_MAX_MESSAGE = 120

# The toast is shown under PowerShell's own app id unless the installer registers knaif's
# (KNAIF_ALERT_APP_ID); an unregistered id shows nothing. The text comes from the environment
# and is XML-escaped here, so nothing the user typed is ever parsed as script or markup.
_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [System.Security.SecurityElement]::Escape($env:KNAIF_ALERT_TITLE)
$m = [System.Security.SecurityElement]::Escape($env:KNAIF_ALERT_MESSAGE)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
$app = $env:KNAIF_ALERT_APP_ID
if (-not $app) { $app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe' }
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""

_OSASCRIPT = [
    "-e",
    "on run argv",
    "-e",
    'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
    "-e",
    "end run",
]


@dataclass(frozen=True)
class Alert:
    outcome: str  # "done" | "failed" | "stopped"
    title: str
    message: str


def _step_failed(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if "error" in result:
        return True
    rc = result.get("returncode")
    if rc is not None and rc != 0:
        return True
    return any(
        isinstance(o, dict) and o.get("returncode") not in (None, 0)
        for o in result.get("outputs") or []
    )


def run_outcome(
    results: list[dict[str, Any]], error: BaseException | None, work_started: bool
) -> str | None:
    """``done``, ``failed`` or ``stopped`` for a run that did some work, else None."""
    if not work_started:
        return None
    if error is not None:
        return "failed"
    if any(_step_failed(r.get("result")) for r in results):
        return "failed"
    for r in results:
        res = r.get("result")
        if (
            r.get("tool") == "wait_for_confirmation"
            and isinstance(res, dict)
            and res.get("status") == "declined"
        ):
            return "stopped"
    return "done"


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_MESSAGE else text[: _MAX_MESSAGE - 1].rstrip() + "…"


def alert_for(
    outcome: str, *, request: str | None, seconds: float, reason: str | None = None
) -> Alert:
    """The notification's title and text for *outcome*."""
    took = f"{seconds:.0f} s"
    if outcome == "failed":
        message = f"Failed: {reason}" if reason else f"Failed ({took})"
    elif outcome == "stopped":
        message = "Stopped: a confirmation was declined"
    else:
        message = f"Done ({took})"
    if request and not (outcome == "failed" and reason) and outcome != "stopped":
        message += f": {request}"
    return Alert(outcome=outcome, title="knaif", message=_short(message))


def _bell(stderr: TextIO) -> str:
    stderr.write("\a")
    stderr.flush()
    return "bell"


def _windows_beep() -> None:
    # The platform check is also what lets mypy on Linux accept `winsound` (its stub is win32-only).
    if sys.platform != "win32":
        return
    import winsound  # Windows-only stdlib

    winsound.MessageBeep()


# knaif's own app id. The installer registers it under HKCU (DisplayName, IconUri), and from
# then on the banner says knaif; until then it borrows Windows PowerShell's.
APP_ID = "knaif.cli"


def _registered_app_id() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        winreg.CloseKey(
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}")
        )
    except OSError:
        return None
    return APP_ID


def fire(
    alert: Alert,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Callable[..., Any] | None = None,
    beep: Callable[[], None] | None = None,
    app_id: Callable[[], str | None] | None = None,
    stderr: TextIO | None = None,
) -> str:
    """Make *alert* (or record it in the sink). Returns the backend used; never raises."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which
    run = subprocess.run if run is None else run
    stderr = sys.stderr if stderr is None else stderr

    sink = env.get(SINK_ENV)
    if sink:
        record: dict[str, Any] = {"kind": "alert", "outcome": alert.outcome, "title": alert.title}
        record.update({"message": alert.message, "sound": True})
        with open(sink, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return "sink"

    try:
        if platform == "win32":
            (beep or _windows_beep)()
            child_env = {**env, "KNAIF_ALERT_TITLE": alert.title}
            child_env["KNAIF_ALERT_MESSAGE"] = alert.message
            registered = (app_id or _registered_app_id)()
            if registered and not env.get("KNAIF_ALERT_APP_ID"):
                child_env["KNAIF_ALERT_APP_ID"] = registered
            proc = run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
                env=child_env,
                capture_output=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "windows-toast" if getattr(proc, "returncode", 1) == 0 else _bell(stderr)
        if platform == "darwin":
            proc = run(
                ["osascript", *_OSASCRIPT, alert.title, alert.message],
                capture_output=True,
                timeout=15,
            )
            return "macos" if getattr(proc, "returncode", 1) == 0 else _bell(stderr)
        desktop = (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")) and not env.get(
            "SSH_CONNECTION"
        )
        notify_send = which("notify-send") if desktop else None
        if notify_send:
            proc = run([notify_send, alert.title, alert.message], capture_output=True, timeout=15)
            if getattr(proc, "returncode", 1) == 0:
                player = which("canberra-gtk-play")
                if player:
                    run([player, "-i", "complete"], capture_output=True, timeout=15)
                else:
                    _bell(stderr)
                return "notify-send"
        return _bell(stderr)
    except Exception:  # noqa: BLE001 - an alert must never fail the run it reports on
        try:
            return _bell(stderr)
        except Exception:  # noqa: BLE001
            return "none"
