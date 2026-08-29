import errno
import os
import socket
import threading
import time
import webbrowser
from pathlib import Path

from app import create_app
from app.settings import get as setting

app = create_app()


def _maybe_open_browser(url: str) -> None:
    if not setting("open_browser"):
        return

    def open_later() -> None:
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=open_later, daemon=True).start()


def _close_inherited_listen_fds(port: int) -> None:
    """Drop a listening socket left behind by os.execv after Update now."""
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.is_dir():
        return
    for entry in fd_dir.iterdir():
        try:
            fd = int(entry.name)
        except ValueError:
            continue
        if fd < 3:
            continue
        try:
            if not os.readlink(entry).startswith("socket:"):
                continue
        except OSError:
            continue
        try:
            sock = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            continue
        try:
            addr = sock.getsockname()
            if len(addr) >= 2 and int(addr[1]) == port:
                try:
                    os.close(fd)
                except OSError:
                    pass
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass


def _wait_for_port(host: str, port: int) -> None:
    deadline = time.time() + 12
    busy = {errno.EADDRINUSE, 10048}
    if hasattr(errno, "WSAEADDRINUSE"):
        busy.add(errno.WSAEADDRINUSE)
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return
        except OSError as exc:
            if exc.errno not in busy:
                raise
            if time.time() >= deadline:
                raise
            time.sleep(0.25)
        finally:
            sock.close()


if __name__ == "__main__":
    # No debugger — sharing PINs live in this process.
    # LAN bind and port are optional (Settings); Devices pairing needs LAN on.
    from app.friends import has_active_invite
    from app.live_sync import start_background
    from app.nat import refresh as nat_refresh
    from app.p2p import start_background as start_p2p
    from app.sync import ensure_state, has_paired_peers

    ensure_state()
    want_lan = setting("allow_lan") or has_active_invite() or has_paired_peers()
    host = "0.0.0.0" if want_lan else "127.0.0.1"
    port = int(setting("port"))
    _close_inherited_listen_fds(port)
    try:
        _wait_for_port(host, port)
    except OSError:
        print(f"Port {port} is already in use. Close the other Start window, then run Start again.")
        raise SystemExit(1)
    url = f"http://127.0.0.1:{port}"
    print(f"This computer:  {url}")
    if host == "0.0.0.0":
        print(f"Other devices on Wi-Fi can use this PC IP on port {port} (see Devices).")
        # Map the port only when a friend invite or paired computer needs inbound internet.
        # The HTML UI is still blocked from the public internet (tokened APIs only).
        if has_active_invite() or has_paired_peers():
            try:
                nat_refresh(port)
            except Exception:
                pass
    _maybe_open_browser(url)
    start_background()
    if want_lan:
        start_p2p(port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
