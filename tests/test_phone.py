from flask import Flask

from app.phone import phone_context
from app.qr import _ALIGN, _build, _mask_fn, _reserved, qr_svg
from app.settings import save


def test_phone_url_is_lan_not_wan(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    save({"allow_lan": True, "port": 5050})
    monkeypatch.setattr("app.nat.lan_ip", lambda: "192.168.1.10")
    ctx = phone_context()
    assert ctx["phone_url"] == "http://192.168.1.10:5050"
    assert ctx["phone_ready"] is True
    assert ctx["phone_qr_svg"].startswith("<svg")
    assert "http://203." not in ctx["phone_url"]


def test_phone_not_ready_when_lan_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    save({"allow_lan": False, "port": 6060})
    monkeypatch.setattr("app.nat.lan_ip", lambda: "192.168.1.10")
    ctx = phone_context()
    assert ctx["phone_ready"] is False
    assert ctx["phone_qr_svg"] == ""
    assert ctx["phone_url"].endswith(":6060")


def test_phone_uses_bound_port_not_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    save({"allow_lan": True, "port": 5050})
    monkeypatch.setattr("app.nat.lan_ip", lambda: "192.168.1.10")
    app = Flask(__name__)
    app.config["BIND_HOST"] = "0.0.0.0"
    app.config["BIND_PORT"] = 6060
    with app.app_context():
        ctx = phone_context()
    assert ctx["phone_url"] == "http://192.168.1.10:6060"
    assert ctx["phone_ready"] is True


def test_phone_not_ready_when_bound_localhost(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    save({"allow_lan": True, "port": 5050})
    monkeypatch.setattr("app.nat.lan_ip", lambda: "192.168.1.10")
    app = Flask(__name__)
    app.config["BIND_HOST"] = "127.0.0.1"
    app.config["BIND_PORT"] = 5050
    with app.app_context():
        ctx = phone_context()
    assert ctx["phone_ready"] is False
    assert ctx["phone_qr_svg"] == ""


def test_qr_svg_has_finder_modules():
    svg = qr_svg("http://192.168.1.10:5050")
    assert svg.startswith("<svg")
    assert svg.count("<rect") > 40


def test_qr_round_trips_lan_url():
    url = "http://192.168.1.10:5050"
    grid = _build(url)
    size = len(grid)
    coords = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    raw = 0
    for r, c in coords:
        raw = (raw << 1) | grid[r][c]
    mask = ((raw ^ 0x5412) >> 10) & 0b111
    fn = _mask_fn(mask)
    centers = _ALIGN[2]
    col = size - 1
    upward = True
    bits = []
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not _reserved(row, c, size, centers):
                    bit = grid[row][c]
                    if fn(row, c):
                        bit ^= 1
                    bits.append(bit)
        upward = not upward
        col -= 2
    assert bits[:4] == [0, 1, 0, 0]
    n = int("".join(map(str, bits[4:12])), 2)
    payload = bits[12 : 12 + n * 8]
    text = bytes(int("".join(map(str, payload[i : i + 8])), 2) for i in range(0, len(payload), 8))
    assert text.decode() == url
