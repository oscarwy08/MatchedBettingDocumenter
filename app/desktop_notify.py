"""Best-effort Windows and Linux desktop toasts. Fail quietly."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

# CREATE_NO_WINDOW — hides the PowerShell console without -WindowStyle Hidden,
# which can stop WinRT toasts from showing.
_CREATE_NO_WINDOW = 0x08000000

_last_error = ""


def last_error() -> str:
    return _last_error


def send(title: str, body: str) -> bool:
    global _last_error
    _last_error = ""
    try:
        if sys.platform.startswith("linux"):
            ok = _linux(title, body)
        elif sys.platform == "win32":
            ok = _windows(title, body)
        else:
            _last_error = "This computer is not Windows or Linux."
            return False
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        return False
    if not ok and not _last_error:
        _last_error = "The OS did not accept the notification."
    return ok


def _linux(title: str, body: str) -> bool:
    global _last_error
    exe = shutil.which("notify-send")
    if not exe:
        _last_error = "notify-send was not found."
        return False
    result = subprocess.run(
        [exe, "--app-name=Matched Betting Documenter", "--", title, body],
        check=False,
        timeout=5,
        capture_output=True,
    )
    if result.returncode != 0:
        _last_error = _output(result) or "notify-send failed."
        return False
    return True


def _windows(title: str, body: str) -> bool:
    global _last_error
    exe = _windows_powershell()
    if not exe:
        _last_error = "Windows PowerShell was not found."
        return False
    encoded = base64.b64encode(_windows_script(title, body).encode("utf-16-le")).decode("ascii")
    kwargs: dict = {
        "check": False,
        "timeout": 15,
        "capture_output": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    result = subprocess.run(
        [
            exe,
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        **kwargs,
    )
    if result.returncode != 0:
        _last_error = _output(result) or "Windows PowerShell did not show the popup."
        return False
    return True


def _windows_powershell() -> str | None:
    root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    names = []
    if sys.maxsize <= 2**32:
        names.append(root / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    names.extend(
        [
            root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
            root / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        ]
    )
    for path in names:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _windows_script(title: str, body: str) -> str:
    # Windows only shows toasts for a registered AppUserModelID. A custom name
    # is ignored; the stock PowerShell shortcut is already registered.
    # XML lives in a here-string so attribute quotes cannot break PowerShell.
    xml = (
        '<toast><visual><binding template="ToastGeneric">'
        f"<text>{_xml(title)}</text>"
        f"<text>{_xml(body)}</text>"
        '<text placement="attribution">Matched Betting Documenter</text>'
        "</binding></visual></toast>"
    )
    app_id = (
        "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"
    )
    return (
        "$n = $null\n"
        "$ok = $false\n"
        "try {\n"
        "  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "  $xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "  $xml.LoadXml(@'\n"
        f"{xml}\n"
        "'@)\n"
        "  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
        f"  $id = '{app_id}'\n"
        "  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($toast)\n"
        "  $ok = $true\n"
        "} catch {\n"
        "  [Console]::Error.WriteLine($_.Exception.Message)\n"
        "}\n"
        "if (-not $ok) {\n"
        "  try {\n"
        "    Add-Type -AssemblyName System.Windows.Forms | Out-Null\n"
        "    Add-Type -AssemblyName System.Drawing | Out-Null\n"
        "    $n = New-Object System.Windows.Forms.NotifyIcon\n"
        "    $n.Icon = [System.Drawing.SystemIcons]::Information\n"
        "    $n.Visible = $true\n"
        f"    $n.ShowBalloonTip(4000, {_ps_single(title)}, {_ps_single(body)}, "
        "[System.Windows.Forms.ToolTipIcon]::Info)\n"
        "    $ok = $true\n"
        "  } catch {\n"
        "    [Console]::Error.WriteLine($_.Exception.Message)\n"
        "    exit 1\n"
        "  }\n"
        "}\n"
        "Start-Sleep -Seconds 2\n"
        "if ($null -ne $n) { $n.Dispose() }\n"
        "if ($ok) { exit 0 } else { exit 1 }\n"
    )


def _ps_single(value: str) -> str:
    cleaned = (value or "").replace("\r", " ").replace("\n", " ")
    return "'" + cleaned.replace("'", "''") + "'"


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


def _output(result: subprocess.CompletedProcess) -> str:
    raw = result.stderr or result.stdout or b""
    if isinstance(raw, str):
        text = raw
    else:
        text = raw.decode("utf-8", "replace")
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:180]
