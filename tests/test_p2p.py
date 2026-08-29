from app import p2p
from app.live_sync import apply_push, peer_hosts
from app.sync import ensure_state, upsert_peer


def test_discovery_stays_off_http_port():
    ports = p2p.discovery_ports(5050)
    assert 5050 not in ports
    assert ports[0] == 5051


def test_listen_bind_failure_is_quiet(monkeypatch):
    class Boom:
        def setsockopt(self, *_args, **_kwargs):
            return None

        def bind(self, _addr):
            raise PermissionError("WinError 10013")

        def close(self):
            return None

    monkeypatch.setattr(p2p.socket, "socket", lambda *_args, **_kwargs: Boom())
    assert p2p._open_listen_socket(5050) is None
    p2p._listen_loop(5050)


def test_announce_remembers_paired_peer(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    me = ensure_state()
    upsert_peer({"device_id": "laptop-1", "token": "tok", "host": "192.168.1.138:5050", "port": 5050})
    found = p2p.apply_announce(
        {
            "v": 1,
            "t": "here",
            "device_id": "laptop-1",
            "nickname": "Laptop",
            "http": "192.168.1.20:5050",
            "port": 5050,
        }
    )
    assert found["host"] == "192.168.1.20:5050"
    assert p2p.host_for("laptop-1") == "192.168.1.20:5050"
    assert p2p.apply_announce({"t": "here", "device_id": me["device_id"], "http": "192.168.1.1:5050"}) is None
    assert p2p.apply_announce({"t": "here", "device_id": "stranger", "http": "192.168.1.9:5050"}) is None


def test_peer_hosts_prefers_discovery_skips_cgnat(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    p2p.remember("laptop-1", "192.168.1.20:5050")
    hosts = peer_hosts(
        {
            "device_id": "laptop-1",
            "lan_host": "192.168.1.138",
            "wan_host": "100.120.12.40",
            "host": "192.168.1.138:5050",
            "port": 5050,
        }
    )
    assert hosts[0] == "192.168.1.20:5050"
    assert "100.120.12.40" not in "".join(hosts)


def test_apply_push_skips_same_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from app.db import init_db
    from app.seed import seed_accounts
    from app.snapshot import dump_snapshot

    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    session.commit()
    snap = dump_snapshot(session)
    result = apply_push(session, {"snapshot": snap, "nickname": "PC"})
    assert result["same"] is True
    session.close()
