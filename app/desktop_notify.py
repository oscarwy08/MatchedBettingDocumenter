"""Best-effort Windows and Linux desktop toasts. Fail quietly."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path


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
    result = subprocess.run(
        [exe, "--app-name=Matched Betting Documenter", "--", title, body],
        check=False,
        timeout=5,
        capture_output=True,
    )
    return result.returncode == 0


def _windows(title: str, body: str) -> bool:
    exe = _windows_powershell()
    if not exe:
        return False
    encoded = base64.b64encode(_windows_script(title, body).encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [
            exe,
            "-NoProfile",
            "-STA",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        check=False,
        timeout=12,
        capture_output=True,
    )
    return result.returncode == 0


def _windows_powershell() -> str | None:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    bundled = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("powershell")


def _windows_script(title: str, body: str) -> str:
    # Windows only shows toasts for a registered AppUserModelID. A custom name
    # is ignored; the stock PowerShell shortcut is already registered.
    xml = (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{_xml(title)}</text>"
        f"<text>{_xml(body)}</text>"
        "<text placement='attribution'>Matched Betting Documenter</text>"
        "</binding></visual></toast>"
    )
    app_id = (
        "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"
    )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$xml.LoadXml('{xml}')\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
        f"$id = '{app_id}'\n"
        "$key = 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings\\' + $id\n"
        "if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }\n"
        "New-ItemProperty -Path $key -Name ShowInActionCenter -Value 1 -PropertyType DWORD -Force | Out-Null\n"
        "New-ItemProperty -Path $key -Name Enabled -Value 1 -PropertyType DWORD -Force | Out-Null\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($toast)\n"
        "Start-Sleep -Seconds 2\n"
    )


def _xml(value: str) -> str:
    return (
        (value or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
        .replace('"', "&quot;")
    )
