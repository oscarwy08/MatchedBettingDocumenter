from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.db import init_db
from app.models import Account, Bet, BetStatus, BetType
from app.seed import seed_accounts
from app.snapshot import apply_snapshot, dump_snapshot, fingerprint_payload, would_shrink
from app.sync import (
    adopt_pair_secret,
    authorize_device,
    authorize_linked,
    compare_fingerprints,
    ensure_pair_secret,
    forget_peer,
    remember_linked_device,
    ensure_state,
    make_link_code,
    parse_link_code,
    parse_link_targets,
    save_state,
    start_share,
    stop_share,
    upsert_peer,
)


def test_parse_link_code():
    pin, host = parse_link_code(" 482193@192.168.1.10:5050 ")
    assert pin == "482193"
    assert host == "192.168.1.10:5050"
    pin, host = parse_link_code("482193@10.0.0.5")
    assert host == "10.0.0.5:5050"


def test_parse_link_targets_lan_and_wan():
    pin, hosts = parse_link_targets("482193@192.168.1.10:5050+203.0.113.4:5050")
    assert pin == "482193"
    assert hosts == ["192.168.1.10:5050", "203.0.113.4:5050"]


def test_compare_matrix():
    last = "aaa"
    assert compare_fingerprints("aaa", "aaa", last) == "same"
    assert compare_fingerprints("aaa", "bbb", last) == "pull"
    assert compare_fingerprints("ccc", "aaa", last) == "wait"
    assert compare_fingerprints("ccc", "bbb", last) == "conflict"
    assert compare_fingerprints("x", "y", "") == "conflict"
    assert compare_fingerprints("same", "same", "other") == "same"


def test_would_shrink():
    local = {"accounts": [1], "offers": [1, 2], "bets": [1, 2, 3], "transfers": []}
    remote = {"accounts": [1], "offers": [1], "bets": [1], "transfers": []}
    assert would_shrink(local, remote) is True
    assert would_shrink(remote, local) is False
    assert would_shrink({"bets": 5, "offers": 2}, {"bets": 5, "offers": 2}) is False
    assert would_shrink({"bets": 5, "offers": 2}, {"bets": 4, "offers": 2}) is True
    # Status API sends integer counts, not row lists — this 500'd freshness and log-a-bet.
    assert would_shrink({"accounts": 34, "offers": 0, "bets": 2, "transfers": 0}, {"accounts": 34, "offers": 0, "bets": 3, "transfers": 0}) is False
    assert would_shrink({"accounts": 34, "offers": 2, "bets": 9, "transfers": 1}, {"accounts": 34, "offers": 1, "bets": 3, "transfers": 1}) is True


def test_freshness_does_not_dial(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from app.db import init_db
    from app.live_sync import freshness
    from app.seed import seed_accounts

    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    session.commit()
    remember_linked_device(device_id="laptop-1", token="tok", nickname="Laptop", lan_host="10.0.0.2")

    def boom(*_args, **_kwargs):
        raise AssertionError("freshness must not dial the other computer")

    monkeypatch.setattr("app.live_sync.fetch_peer", boom)
    state = freshness(session)
    assert state["needs_confirm"] is False
    session.close()


def test_pairing_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    state = ensure_state()
    token = state["device_token"]
    assert token
    upsert_peer(
        {
            "device_id": "other",
            "nickname": "Laptop",
            "token": "peer-token",
            "host": "192.168.1.9:5050",
            "lan_host": "192.168.1.9",
            "port": 5050,
        }
    )
    again = ensure_state()
    assert again["device_id"] == state["device_id"]
    assert again["peers"][0]["nickname"] == "Laptop"
    save_state(again)
    assert (tmp_path / "data" / "sync.json").is_file()


def test_authorize_pin_and_reject_friend(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    ensure_state()
    pin = start_share()
    assert authorize_device(pin)
    assert authorize_device(ensure_state()["device_token"])
    assert not authorize_device("view.not-a-real-invite")
    stop_share()
    assert not authorize_device(pin)


def test_snapshot_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    Session = init_db(tmp_path / "a.db")
    session = Session()
    seed_accounts(session)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    smarkets = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(
        Bet(
            event="Keep me",
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
    payload = dump_snapshot(session)
    assert payload["fingerprint"] == fingerprint_payload(payload)
    session.close()

    Session2 = init_db(tmp_path / "b.db")
    other = Session2()
    seed_accounts(other)
    apply_snapshot(other, payload)
    other.commit()
    copied = other.scalars(select(Bet)).one()
    assert copied.event == "Keep me"
    assert copied.back_stake == Decimal("10.00")
    assert copied.placed_at == datetime(2026, 8, 1, 11, 30)
    other.add(Account(name="Brand New Bookie", type="bookie", commission_percent=Decimal("0")))
    other.commit()
    fresh = other.scalars(select(Account).where(Account.name == "Brand New Bookie")).one()
    assert fresh.id > copied.bookie_id
    other.close()
    assert "482193" in make_link_code("482193", 5050)


def test_snapshot_includes_friends_account(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from app.friends import create_invite, load_state as load_friends
    from app.snapshot import fingerprint_bets_only

    Session = init_db(tmp_path / "a.db")
    session = Session()
    seed_accounts(session)
    session.commit()
    create_invite("Sam")
    payload = dump_snapshot(session)
    assert payload["friends"]["invites"]
    assert payload["fingerprint"] != fingerprint_bets_only(payload)
    session.close()

    Session2 = init_db(tmp_path / "b.db")
    other = Session2()
    apply_snapshot(other, payload)
    other.commit()
    other.close()
    invites = load_friends()["invites"]
    assert len(invites) == 1
    assert invites[0]["nickname"] == "Sam"


def test_migrate_last_agreed_from_bets_only_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from app.live_sync import migrate_last_agreed
    from app.snapshot import fingerprint_bets_only

    Session = init_db(tmp_path / "a.db")
    session = Session()
    seed_accounts(session)
    session.commit()
    snap = dump_snapshot(session)
    state = ensure_state()
    state["last_agreed"] = fingerprint_bets_only(snap)
    save_state(state)
    migrate_last_agreed(session)
    assert ensure_state()["last_agreed"] == snap["fingerprint"]
    session.close()


def test_pair_secret_is_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    first = ensure_pair_secret()
    assert first
    assert ensure_pair_secret() == first
    adopt_pair_secret("shared-pair-from-other")
    assert ensure_state()["pair_secret"] == "shared-pair-from-other"


def test_remember_linked_device_is_two_way(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    me = ensure_state()
    remember_linked_device(
        device_id="laptop-1",
        token="laptop-token",
        nickname="Laptop",
        lan_host="192.168.1.20",
        port=5050,
    )
    peers = ensure_state()["peers"]
    assert len(peers) == 1
    assert peers[0]["token"] == "laptop-token"
    assert peers[0]["our_token"] == me["device_token"]
    assert peers[0]["host"] == "192.168.1.20:5050"
    remember_linked_device(device_id="laptop-1", token="laptop-token", nickname="Laptop")
    assert ensure_state()["peers"][0]["lan_host"] == "192.168.1.20"
    assert remember_linked_device(device_id=me["device_id"], token=me["device_token"]) is None


def test_firewall_helper_is_windows_only():
    from app.win_firewall import allow_port

    assert allow_port(5050) is False


def test_unlink_stays_unlinked(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    ensure_state()
    remember_linked_device(device_id="laptop-1", token="laptop-token", nickname="Laptop", lan_host="10.0.0.2")
    peer_id = ensure_state()["peers"][0]["id"]
    forget_peer(peer_id)
    assert ensure_state()["peers"] == []
    assert remember_linked_device(device_id="laptop-1", token="laptop-token", nickname="Laptop") is None
    assert ensure_state()["peers"] == []


def test_fetch_tokens_try_ours_first(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from app.live_sync import _tokens_for

    me = ensure_state()
    peer = {"token": "laptop-token"}
    assert _tokens_for(peer)[0] == me["device_token"]
    assert "laptop-token" in _tokens_for(peer)


def test_authorize_linked_accepts_peer_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    ensure_state()
    upsert_peer({"device_id": "other", "token": "peer-secret-token", "host": "192.168.1.9:5050"})
    assert authorize_linked("peer-secret-token")
    assert not authorize_device("peer-secret-token")
    assert not authorize_linked("view.not-an-invite")
