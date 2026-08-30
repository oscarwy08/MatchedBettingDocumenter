"""Best-effort Windows and Linux desktop toasts. Fail quietly."""

from __future__ import annotations

import shutil
import subprocess
import sys


def send(title: str, body: str) -> bool:
    try:
        if sys.platform.startswith("linux"):
            return _linux(title, body)
        if sys.platform == "win32":
            return _windows(title, body)
    except Exception:  # noqa: BLE001
        return False
    return False


def _linux(title: str, body: str) -> bool:
    exe = shutil.which("notify-send")
    if not exe:
        return False
    subprocess.run(
        [exe, "--app-name=Matched Betting Documenter", "--", title, body],
        check=False,
        timeout=5,
        capture_output=True,
    )
    return True


def _windows(title: str, body: str) -> bool:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return False
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        f"$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$xml.LoadXml('{_ps_xml(title, body)}')\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'Matched Betting Documenter').Show($toast)\n"
    )
    subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        timeout=8,
        capture_output=True,
    )
    return True


def _ps_xml(title: str, body: str) -> str:
    return (
        "<toast><visual><binding template='ToastText02'>"
        f"<text id='1'>{_xml(title)}</text>"
        f"<text id='2'>{_xml(body)}</text>"
        "</binding></visual></toast>"
    )


def _xml(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
        .replace('"', "&quot;")
    )
