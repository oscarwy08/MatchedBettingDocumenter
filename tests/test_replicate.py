from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app import mailbox
from app.db import init_db
from app.models import Account, Bet, BetStatus, BetType
from app.replicate import (
    KIND,
    decode_snap,
    encode_snap,
    fetch_theirs,
    snap_key,
    tick,
)
from app.seed import seed_accounts
from app.snapshot import dump_snapshot
from app.sync import (
    ensure_pair_secret,
    ensure_state,
    load_state,
    remember_linked_device,
    set_last_agreed,
)


def _settings(monkeypatch, *, allow_lan=True):
    def fake(key):
        if key == "auto_sync":
            return True
        if key == "allow_lan":
            return allow_lan
        if key == "excel_sync":
            return False
        return False

    monkeypatch.setattr("app.replicate.setting", fake)
    monkeypatch.setattr("app.live_sync.setting", fake)


def _mailbox(monkeypatch):
    store = {}
    monkeypatch.setattr(mailbox, "_put", lambda topic, blob: store.__setitem__(topic, blob))
    monkeypatch.setattr(mailbox, "_get", lambda topic, timeout: store.get(topic))
    return store


def _session(tmp_path):
    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    session.commit()
    return session


def _bet(session, event="Remote"):
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    smarkets = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(
        Bet(
            event=event,
            bet_type=BetType.QUALIFYING,
            bookie_id=sky.id,
            exchange_id=smarkets.id,
            back_stake=Decimal("10.00"),
            back_odds=Decimal("2.00"),
            lay_stake=Decimal("9.62"),
            lay_odds=Decimal("2.10"),
            commission_percent=Decimal("2"),
            cashback=Decimal("0"),
            liability=Decimal("10.58"),
            expected_profit=Decimal("-0.58"),
            expected_bookie_back=Decimal("10"),
            expected_exchange_back=Decimal("-10.58"),
            expected_bookie_lay=Decimal("-10"),
            expected_exchange_lay=Decimal("9.43"),
            status=BetStatus.PENDING,
            placed_at=datetime(2026, 8, 1, 11, 30),
        )
    )
    session.commit()


def _peer():
    remember_linked_device(
        device_id="laptop-1",
        token="tok",
        nickname="Laptop",
        lan_host="10.0.0.2",
        port=5050,
    )
    return load_state()["peers"][0]


def test_snap_roundtrip_and_topic_hides_secret():
    payload = {"device_id": "abc", "fingerprint": "fff", "snapshot": {"accounts": []}}
    blob = encode_snap("pair-secret", payload)
    assert blob.startswith("mbd1.")
    assert decode_snap("pair-secret", blob)["fingerprint"] == "fff"
    topic = mailbox.topic_for(KIND, snap_key("pair-secret", "abc"))
    assert "pair-secret" not in topic
    assert topic.startswith("mbd/v1/snap/")


def test_ignore_own_mailbox_blob(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    _mailbox(monkeypatch)
    me = ensure_state()
    secret = ensure_pair_secret()
    mailbox.put(
        KIND,
        snap_key(secret, "laptop-1"),
        encode_snap(secret, {"device_id": me["device_id"], "fingerprint": "x", "snapshot": {"accounts": []}}),
    )
    mailbox.put(
        KIND,
        secret,
        encode_snap(secret, {"device_id": me["device_id"], "fingerprint": "x", "snapshot": {"accounts": []}}),
    )
    assert fetch_theirs({"device_id": "laptop-1"}) is None


def test_mailbox_applies_even_when_wifi_looks_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    _settings(monkeypatch, allow_lan=True)
    _mailbox(monkeypatch)
    session = _session(tmp_path)
    ensure_state()
    secret = ensure_pair_secret()
    local = dump_snapshot(session)
    set_last_agreed(local["fingerprint"])
    _peer()
    _bet(session)
    remote = dump_snapshot(session)
    from app.snapshot import apply_snapshot

    apply_snapshot(session, local)
    session.commit()
    mailbox.put(
        KIND,
        snap_key(secret, "laptop-1"),
        encode_snap(
            secret,
            {
                "device_id": "laptop-1",
                "nickname": "Laptop",
                "fingerprint": remote["fingerprint"],
                "counts": remote["counts"],
                "snapshot": remote,
            },
        ),
    )

    def fake_fetch(peer, path, timeout=2.0):
        return {
            "device_id": "laptop-1",
            "nickname": "Laptop",
            "fingerprint": local["fingerprint"],
            "counts": local["counts"],
            "lan_ip": "10.0.0.2",
            "port": 5050,
        }

    monkeypatch.setattr("app.live_sync.fetch_peer", fake_fetch)
    tick()
    after = dump_snapshot(session)
    assert after["fingerprint"] == remote["fingerprint"]
    session.close()


def test_lan_fail_applies_mailbox_pull(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    _settings(monkeypatch, allow_lan=True)
    _mailbox(monkeypatch)
    session = _session(tmp_path)
    ensure_state()
    secret = ensure_pair_secret()
    local = dump_snapshot(session)
    set_last_agreed(local["fingerprint"])
    _peer()
    _bet(session, event="From laptop")
    remote = dump_snapshot(session)
    from app.snapshot import apply_snapshot

    apply_snapshot(session, local)
    session.commit()
    envelope = {
        "device_id": "laptop-1",
        "nickname": "Laptop",
        "fingerprint": remote["fingerprint"],
        "counts": remote["counts"],
        "snapshot": remote,
    }
    mailbox.put(KIND, snap_key(secret, "laptop-1"), encode_snap(secret, envelope))
    mailbox.put(KIND, secret, encode_snap(secret, envelope))

    def boom(*_args, **_kwargs):
        raise TimeoutError("no lan")

    monkeypatch.setattr("app.live_sync.fetch_peer", boom)
    tick()
    after = dump_snapshot(session)
    assert after["fingerprint"] == remote["fingerprint"]
    assert any(bet["event"] == "From laptop" for bet in after["bets"])
    assert load_state()["peers"][0].get("last_via") == "mailbox"
    session.close()


def test_mailbox_conflict_when_both_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    _settings(monkeypatch, allow_lan=False)
    _mailbox(monkeypatch)
    session = _session(tmp_path)
    ensure_state()
    secret = ensure_pair_secret()
    baseline = dump_snapshot(session)
    set_last_agreed(baseline["fingerprint"])
    _peer()
    _bet(session, event="Local only")
    local = dump_snapshot(session)
    from app.snapshot import apply_snapshot

    apply_snapshot(session, baseline)
    session.commit()
    _bet(session, event="Remote only")
    remote = dump_snapshot(session)
    apply_snapshot(session, local)
    session.commit()
    mailbox.put(
        KIND,
        snap_key(secret, "laptop-1"),
        encode_snap(
            secret,
            {
                "device_id": "laptop-1",
                "nickname": "Laptop",
                "fingerprint": remote["fingerprint"],
                "counts": remote["counts"],
                "snapshot": remote,
            },
        ),
    )
    tick()
    conflict = load_state().get("conflict")
    assert conflict
    assert conflict.get("reason") == "both"
    assert dump_snapshot(session)["fingerprint"] == local["fingerprint"]
    session.close()
