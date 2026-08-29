"""Discover LAN/WAN addresses and map the listen port on the home router."""

from __future__ import annotations

import ipaddress
import re
import socket
import struct
import subprocess
import urllib.error
import urllib.request
from typing import Callable
from xml.etree import ElementTree as ET

CGNAT = ipaddress.ip_network("100.64.0.0/10")
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_ST = "urn:schemas-upnp-org:device:InternetGatewayDevice:1"
STUN_HOSTS = (
    ("stun.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
)

_state: dict = {
    "lan_ip": None,
    "wan_ip": None,
    "mapped": False,
    "mapped_port": None,
    "error": None,
}

# Tests replace these.
_upnp_backend: Callable[[int], dict | None] | None = None
_stun_backend: Callable[[], str | None] | None = None


def _as_ip(raw: str):
    try:
        return ipaddress.ip_address((raw or "").split("%")[0])
    except ValueError:
        return None


def is_cgnat(ip: str) -> bool:
    addr = _as_ip(ip)
    return bool(addr and addr in CGNAT)


def is_rfc1918(ip: str) -> bool:
    addr = _as_ip(ip)
    return bool(addr and any(addr in net for net in _RFC1918))


def is_lan_ip(ip: str) -> bool:
    addr = _as_ip(ip)
    if addr is None or addr.is_loopback or addr.is_link_local or addr in CGNAT:
        return False
    return is_rfc1918(str(addr))


def is_public_wan(ip: str) -> bool:
    """Real internet address — not LAN, not CGNAT. TEST-NET counts so tests can mock a WAN."""
    addr = _as_ip(ip)
    if addr is None or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    if addr in CGNAT or is_rfc1918(str(addr)):
        return False
    return True


def _route_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def local_ipv4s() -> list[str]:
    found: list[str] = []
    for candidate in [_route_ip()]:
        if candidate and candidate not in found:
            found.append(candidate)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and ip not in found:
                found.append(ip)
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        for line in out.stdout.splitlines():
            parts = line.split()
            if "inet" in parts:
                ip = parts[parts.index("inet") + 1].split("/")[0]
                if ip and ip not in found:
                    found.append(ip)
    except (OSError, ValueError):
        pass
    return found


def lan_ip() -> str:
    for ip in local_ipv4s():
        if is_lan_ip(ip):
            return ip
    route = _route_ip()
    return route if route else "127.0.0.1"


def stun_wan_ip(timeout: float = 1.5) -> str | None:
    if _stun_backend is not None:
        return _stun_backend()
    request = bytes.fromhex("00010000") + b"\x21\x12\xa4\x42" + b"\x00" * 12
    for host, port in STUN_HOSTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(request, (host, port))
            data, _ = sock.recvfrom(2048)
        except OSError:
            continue
        finally:
            sock.close()
        ip = _parse_stun_mapped(data)
        if ip:
            return ip
    return None


def _parse_stun_mapped(data: bytes) -> str | None:
    if len(data) < 20:
        return None
    offset = 20
    while offset + 4 <= len(data):
        atype, length = struct.unpack("!HH", data[offset : offset + 4])
        value = data[offset + 4 : offset + 4 + length]
        offset += 4 + length
        if offset % 4:
            offset += 4 - (offset % 4)
        # XOR-MAPPED-ADDRESS (0x0020) or MAPPED-ADDRESS (0x0001)
        if atype not in (0x0020, 0x0001) or len(value) < 8:
            continue
        family = value[1]
        port = struct.unpack("!H", value[2:4])[0]
        raw_ip = value[4:8]
        if atype == 0x0020:
            port ^= 0x2112
            raw_ip = bytes(b ^ m for b, m in zip(raw_ip, b"\x21\x12\xa4\x42"))
        if family == 0x01:
            return socket.inet_ntoa(raw_ip)
    return None


def _ssdp_locations(timeout: float = 1.2) -> list[str]:
    message = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"ST: {SSDP_ST}\r\n"
        "MX: 1\r\n"
        "\r\n"
    ).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    found: list[str] = []
    try:
        sock.sendto(message, SSDP_ADDR)
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except TimeoutError:
                break
            except OSError:
                break
            match = re.search(rb"(?i)location:\s*(\S+)", data)
            if match:
                url = match.group(1).decode("ascii", "ignore").strip()
                if url not in found:
                    found.append(url)
    finally:
        sock.close()
    return found


def _soap_action(control_url: str, service_type: str, action: str, body: str, timeout: float = 2.5) -> str | None:
    envelope = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f"<s:Body><u:{action} xmlns:u=\"{service_type}\">{body}</u:{action}></s:Body>"
        "</s:Envelope>"
    ).encode()
    req = urllib.request.Request(
        control_url,
        data=envelope,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service_type}#{action}"',
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "ignore")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _igd_control(desc_url: str) -> tuple[str, str] | None:
    try:
        with urllib.request.urlopen(desc_url, timeout=2.5) as resp:
            xml = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    ns = {"d": "urn:schemas-upnp-org:device-1-0"}
    for service in root.findall(".//d:service", ns):
        st = (service.findtext("d:serviceType", default="", namespaces=ns) or "").strip()
        if "WANIPConnection" not in st and "WANPPPConnection" not in st:
            continue
        control = (service.findtext("d:controlURL", default="", namespaces=ns) or "").strip()
        if not control:
            continue
        if control.startswith("http"):
            return st, control
        base = desc_url.rsplit("/", 1)[0]
        if not control.startswith("/"):
            control = "/" + control
        host = urllib.request.urlparse(desc_url)
        return st, f"{host.scheme}://{host.netloc}{control}" if control.startswith("/") else f"{base}/{control}"
    return None


def _upnp_map(port: int) -> dict | None:
    if _upnp_backend is not None:
        return _upnp_backend(port)
    local = lan_ip()
    for location in _ssdp_locations():
        found = _igd_control(location)
        if not found:
            continue
        service_type, control = found
        ext_xml = _soap_action(control, service_type, "GetExternalIPAddress", "")
        wan = None
        if ext_xml:
            match = re.search(r"<NewExternalIPAddress>([^<]+)</NewExternalIPAddress>", ext_xml)
            if match:
                wan = match.group(1).strip()
        body = (
            f"<NewRemoteHost></NewRemoteHost>"
            f"<NewExternalPort>{port}</NewExternalPort>"
            f"<NewProtocol>TCP</NewProtocol>"
            f"<NewInternalPort>{port}</NewInternalPort>"
            f"<NewInternalClient>{local}</NewInternalClient>"
            f"<NewEnabled>1</NewEnabled>"
            f"<NewPortMappingDescription>MatchedBettingDocumenter</NewPortMappingDescription>"
            f"<NewLeaseDuration>0</NewLeaseDuration>"
        )
        added = _soap_action(control, service_type, "AddPortMapping", body)
        if added is None:
            continue
        return {"wan_ip": wan, "port": port, "control": control, "service_type": service_type}
    return None


def _upnp_unmap(port: int, control: str, service_type: str) -> None:
    body = (
        f"<NewRemoteHost></NewRemoteHost>"
        f"<NewExternalPort>{port}</NewExternalPort>"
        f"<NewProtocol>TCP</NewProtocol>"
    )
    _soap_action(control, service_type, "DeletePortMapping", body)


_mapping_meta: dict | None = None


def refresh(port: int) -> dict:
    """Try to publish *port* and refresh LAN/WAN status."""
    _state["error"] = None
    _state["lan_ip"] = lan_ip()
    mapped = None
    try:
        mapped = _upnp_map(port)
    except Exception as exc:  # noqa: BLE001
        _state["error"] = str(exc)
    global _mapping_meta
    wan = (mapped or {}).get("wan_ip") if mapped else None
    if not wan:
        try:
            wan = stun_wan_ip()
        except Exception:  # noqa: BLE001
            wan = None
    # UPnP on a CGNAT "WAN" only maps the home router. The ISP still blocks inbound.
    internet = bool(mapped) and bool(wan) and is_public_wan(str(wan))
    if mapped and internet:
        _state["mapped"] = True
        _state["mapped_port"] = port
        _state["wan_ip"] = wan
        _mapping_meta = mapped
    else:
        _state["mapped"] = False
        _state["mapped_port"] = None
        _mapping_meta = mapped if mapped else None
        _state["wan_ip"] = wan
        if wan and is_cgnat(str(wan)):
            _state["error"] = "cgnat"
    return reachability(port)


def release() -> None:
    global _mapping_meta
    meta = _mapping_meta
    port = _state.get("mapped_port")
    if meta and port:
        try:
            _upnp_unmap(int(port), meta["control"], meta["service_type"])
        except Exception:  # noqa: BLE001
            pass
    _state["mapped"] = False
    _state["mapped_port"] = None
    _mapping_meta = None


def reachability(port: int | None = None) -> dict:
    lan = _state.get("lan_ip") or lan_ip()
    wan = _state.get("wan_ip")
    mapped = bool(_state.get("mapped"))
    if mapped and wan and is_public_wan(str(wan)):
        label = "Reachable from the internet"
        kind = "internet"
    elif wan and (is_cgnat(str(wan)) or _state.get("error") == "cgnat"):
        label = "This house cannot accept inbound internet (carrier NAT). Linked computers sync on the same Wi‑Fi."
        kind = "cgnat"
    elif lan and lan != "127.0.0.1":
        label = "Reachable on this Wi‑Fi"
        kind = "lan"
    else:
        label = "Only this computer"
        kind = "local"
    if wan and not mapped and is_public_wan(str(wan)):
        label = "On this Wi‑Fi · this house is not accepting inbound internet (normal on many ISPs)"
        kind = "lan"
    return {
        "lan_ip": lan,
        "wan_ip": wan if wan and is_public_wan(str(wan)) else None,
        "mapped": mapped,
        "port": port or _state.get("mapped_port"),
        "label": label,
        "kind": kind,
        "error": _state.get("error"),
        "cgnat": bool(wan and is_cgnat(str(wan))),
    }


def share_hosts(port: int) -> list[str]:
    """LAN addresses for same Wi‑Fi. CGNAT 100.x is never advertised as internet."""
    hosts: list[str] = []
    for ip in local_ipv4s():
        if is_lan_ip(ip):
            item = f"{ip}:{port}"
            if item not in hosts:
                hosts.append(item)
    if not hosts:
        fallback = lan_ip()
        if fallback and not is_cgnat(fallback):
            hosts.append(f"{fallback}:{port}")
    status = reachability(port)
    wan = status.get("wan_ip")
    if status.get("mapped") and wan and is_public_wan(str(wan)):
        item = f"{wan}:{port}"
        if item not in hosts:
            hosts.append(item)
    return hosts or [f"{lan_ip()}:{port}"]


def format_hosts(port: int) -> str:
    return "+".join(share_hosts(port))
