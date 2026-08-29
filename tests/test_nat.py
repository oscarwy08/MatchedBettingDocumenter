from app import nat


def test_cgnat_is_not_public_or_lan():
    assert nat.is_cgnat("100.120.12.40")
    assert not nat.is_lan_ip("100.120.12.40")
    assert not nat.is_public_wan("100.120.12.40")
    assert nat.is_lan_ip("192.168.1.10")
    assert nat.is_public_wan("8.8.8.8")


def test_share_hosts_lan_only(monkeypatch):
    monkeypatch.setattr(nat, "local_ipv4s", lambda: ["192.168.1.10"])
    monkeypatch.setattr(nat, "lan_ip", lambda: "192.168.1.10")
    nat._state.update({"lan_ip": "192.168.1.10", "wan_ip": None, "mapped": False, "mapped_port": None, "error": None})
    hosts = nat.share_hosts(5050)
    assert hosts == ["192.168.1.10:5050"]
    status = nat.reachability(5050)
    assert status["kind"] == "lan"
    assert "Wi‑Fi" in status["label"]


def test_share_hosts_includes_wan_when_mapped(monkeypatch):
    monkeypatch.setattr(nat, "local_ipv4s", lambda: ["192.168.1.10"])
    monkeypatch.setattr(nat, "lan_ip", lambda: "192.168.1.10")
    nat._state.update(
        {"lan_ip": "192.168.1.10", "wan_ip": "203.0.113.4", "mapped": True, "mapped_port": 5050, "error": None}
    )
    hosts = nat.share_hosts(5050)
    assert hosts == ["192.168.1.10:5050", "203.0.113.4:5050"]
    assert nat.format_hosts(5050) == "192.168.1.10:5050+203.0.113.4:5050"
    status = nat.reachability(5050)
    assert status["kind"] == "internet"


def test_cgnat_not_added_as_internet(monkeypatch):
    monkeypatch.setattr(nat, "local_ipv4s", lambda: ["192.168.1.10", "100.120.12.40"])
    monkeypatch.setattr(nat, "lan_ip", lambda: "192.168.1.10")
    nat._state.update(
        {"lan_ip": "192.168.1.10", "wan_ip": "100.120.12.40", "mapped": True, "mapped_port": 5050, "error": "cgnat"}
    )
    hosts = nat.share_hosts(5050)
    assert hosts == ["192.168.1.10:5050"]
    assert "100.120.12.40" not in "".join(hosts)


def test_mapping_failed_label(monkeypatch):
    monkeypatch.setattr(nat, "lan_ip", lambda: "192.168.1.10")
    nat._state.update(
        {"lan_ip": "192.168.1.10", "wan_ip": "203.0.113.4", "mapped": False, "mapped_port": None, "error": None}
    )
    status = nat.reachability(5050)
    assert status["kind"] == "lan"
    assert "Wi‑Fi" in status["label"]


def test_refresh_uses_mocked_upnp(monkeypatch):
    monkeypatch.setattr(nat, "lan_ip", lambda: "10.0.0.8")
    nat._upnp_backend = lambda port: {"wan_ip": "198.51.100.2", "port": port}
    nat._stun_backend = lambda: "198.51.100.2"
    status = nat.refresh(6060)
    assert status["mapped"] is True
    assert status["wan_ip"] == "198.51.100.2"
    nat._upnp_backend = None
    nat._stun_backend = None
    nat._state.update({"lan_ip": None, "wan_ip": None, "mapped": False, "mapped_port": None, "error": None})


def test_refresh_cgnat_is_not_internet(monkeypatch):
    monkeypatch.setattr(nat, "lan_ip", lambda: "192.168.1.10")
    nat._upnp_backend = lambda port: {"wan_ip": "100.120.12.40", "port": port}
    nat._stun_backend = lambda: "100.120.12.40"
    status = nat.refresh(5050)
    assert status["mapped"] is False
    assert status["kind"] == "cgnat"
    nat._upnp_backend = None
    nat._stun_backend = None
    nat._state.update({"lan_ip": None, "wan_ip": None, "mapped": False, "mapped_port": None, "error": None})


def test_stun_parse_xor_mapped():
    header = bytes.fromhex("0101000c") + bytes.fromhex("2112a442") + b"\x00" * 12
    ip = bytes(b ^ m for b, m in zip(bytes([203, 0, 113, 1]), bytes.fromhex("2112a442")))
    attr = bytes.fromhex("00200008") + bytes([0, 1]) + bytes.fromhex("0000") + ip
    parsed = nat._parse_stun_mapped(header + attr)
    assert parsed == "203.0.113.1"
