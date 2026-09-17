from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app import mailbox
from app.crypto import decrypt_bytes
from app.db import init_db
from app.friends import create_invite, upsert_friend
from app.models import Account, Bet, BetStatus, BetType, Offer, OfferType
from app.seed import seed_accounts
from app.snapshot import dump_snapshot
from app.sync import ensure_pair_secret, ensure_state
from app.vault import (
    apply_held,
    compare_meta,
    held_meta,
    ingest_mailbox_holds,
    load_held,
    meta_of,
    seal_snapshot,
    start_background,
    store_if_newer,
    tick,
    unseal_snapshot,
)


def _session(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    Session = init_db(tmp_path / "data" / "app.db")
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
        )
    )


def _mailbox(monkeypatch):
    store = {}
    monkeypatch.setattr(mailbox, "_put", lambda topic, blob: store.__setitem__(topic, blob))
    monkeypatch.setattr(mailbox, "_get", lambda topic, timeout: store.get(topic))
    return store


def test_pair_secret_seals_and_view_secret_cannot_read(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    pair = ensure_pair_secret()
    _bet(session, "Sealed")
    session.commit()
    payload = dump_snapshot(session)
    envelope = seal_snapshot(payload, pair)
    assert envelope["ciphertext"].startswith("mbd1.")
    assert b"Sealed" not in envelope["ciphertext"].encode()
    opened = unseal_snapshot(envelope, pair)
    assert opened["fingerprint"] == payload["fingerprint"]
    assert any(bet["event"] == "Sealed" for bet in opened["bets"])
    try:
        unseal_snapshot(envelope, "view.not-the-pair")
        raise AssertionError("view secret must not decrypt the spare")
    except ValueError:
        pass
    try:
        decrypt_bytes("view.not-the-pair", envelope["ciphertext"])
        raise AssertionError("view secret must not decrypt bytes")
    except ValueError:
        pass
    session.close()


def test_tampered_file_fails_hmac(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    pair = ensure_pair_secret()
    payload = dump_snapshot(session)
    envelope = seal_snapshot(payload, pair)
    stored = store_if_newer("abc123", envelope)
    assert stored is True
    path = tmp_path / "data" / "friend_vault" / "abc123.mbd1"
    raw = path.read_text(encoding="utf-8").strip()
    path.write_text(raw[:-4] + "XXXX\n", encoding="utf-8")
    held = load_held("abc123")
    try:
        unseal_snapshot(held, pair)
        raise AssertionError("tampered spare must fail")
    except ValueError:
        pass
    session.close()


def test_store_if_newer_and_same_fingerprint_skips(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    pair = ensure_pair_secret()
    payload = dump_snapshot(session)
    envelope = seal_snapshot(payload, pair)
    assert store_if_newer("hold1", envelope) is True
    assert store_if_newer("hold1", envelope) is False
    meta = held_meta("hold1")
    assert meta["fingerprint"] == payload["fingerprint"]
    session.close()


def test_compare_meta_ahead_behind_same():
    local = {"fingerprint": "aaa", "exported_at": "2026-09-17T10:00:00"}
    assert compare_meta(local, None) == "push"
    assert compare_meta(local, local) == "same"
    assert compare_meta(local, {"fingerprint": "bbb", "exported_at": "2026-09-17T11:00:00"}) == "pull"
    assert compare_meta(local, {"fingerprint": "bbb", "exported_at": "2026-09-17T09:00:00"}) == "push"


def test_behind_pull_applies_with_backup(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    pair = ensure_pair_secret()
    _bet(session, "FromFriend")
    session.commit()
    payload = dump_snapshot(session)
    payload["exported_at"] = "2099-01-01T00:00:00"
    envelope = seal_snapshot(payload, pair)
    from sqlalchemy import delete

    session.execute(delete(Bet))
    session.commit()
    assert session.scalars(select(Bet)).first() is None
    result = apply_held(session, envelope, pair)
    assert result is not None
    assert result["same"] is False
    restored = session.scalars(select(Bet).where(Bet.event == "FromFriend")).one()
    assert restored.event == "FromFriend"
    from app.backups import list_backups

    reasons = [item.get("why") for item in list_backups()]
    assert "before-friend-restore" in reasons
    session.close()


def test_shrink_skips_apply(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    pair = ensure_pair_secret()
    small = dump_snapshot(session)
    small["exported_at"] = "2099-01-01T00:00:00"
    envelope = seal_snapshot(small, pair)
    _bet(session, "KeepMe")
    session.commit()
    result = apply_held(session, envelope, pair)
    assert result is None
    assert session.scalars(select(Bet).where(Bet.event == "KeepMe")).one()
    session.close()


def test_tick_pushes_when_ahead(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    _mailbox(monkeypatch)
    _bet(session, "Mine")
    session.commit()
    upsert_friend(
        {
            "id": "friend1",
            "secret": "friendsecretfriendsecret",
            "nickname": "Alex",
            "lan_host": "10.0.0.8",
            "host": "10.0.0.8:5050",
            "port": 5050,
        }
    )
    posted = []

    def fake_fetch(_hosts, path, token=None, timeout=None):
        if path.endswith("/meta"):
            return {"held": False}
        raise AssertionError(path)

    def fake_post(_hosts, path, body, token=None):
        posted.append(body)
        return {"ok": True, "stored": True}

    monkeypatch.setattr("app.live_sync.fetch_json", fake_fetch)
    monkeypatch.setattr("app.live_sync.post_json", fake_post)
    tick()
    assert posted
    assert posted[0]["ciphertext"].startswith("mbd1.")
    assert posted[0]["fingerprint"]
    session.close()


def test_tick_pulls_when_behind(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    pair = ensure_pair_secret()
    _mailbox(monkeypatch)
    _bet(session, "Theirs")
    session.commit()
    payload = dump_snapshot(session)
    payload["exported_at"] = "2099-01-01T00:00:00"
    envelope = seal_snapshot(payload, pair)
    from sqlalchemy import delete

    session.execute(delete(Bet))
    session.commit()
    upsert_friend(
        {
            "id": "friend2",
            "secret": "othersecretothersecret",
            "nickname": "Sam",
            "lan_host": "10.0.0.9",
            "host": "10.0.0.9:5050",
            "port": 5050,
        }
    )

    def fake_fetch(_hosts, path, token=None, timeout=None):
        if path.endswith("/meta"):
            return {**meta_of(envelope), "held": True}
        if path.endswith("/vault"):
            return envelope
        raise AssertionError(path)

    monkeypatch.setattr("app.live_sync.fetch_json", fake_fetch)
    monkeypatch.setattr("app.live_sync.post_json", lambda *_args, **_kwargs: {"ok": True})
    tick()
    session.expire_all()
    assert session.scalars(select(Bet).where(Bet.event == "Theirs")).one()
    session.close()


def test_tick_meta_only_when_fingerprints_match(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    _mailbox(monkeypatch)
    upsert_friend(
        {
            "id": "friend3",
            "secret": "samesecretsamesecret",
            "nickname": "Pat",
            "host": "10.0.0.10:5050",
            "port": 5050,
        }
    )
    local = dump_snapshot(session)
    fetched = []

    def fake_fetch(_hosts, path, token=None, timeout=None):
        fetched.append(path)
        if path.endswith("/meta"):
            return {**meta_of(local), "held": True}
        raise AssertionError("must not fetch the blob")

    monkeypatch.setattr("app.live_sync.fetch_json", fake_fetch)
    monkeypatch.setattr("app.live_sync.post_json", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no push")))
    tick()
    assert fetched == ["/api/friend/vault/meta"]
    session.close()


def test_mailbox_hold_ingest(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    ensure_state()
    pair = ensure_pair_secret()
    _mailbox(monkeypatch)
    invite = create_invite("Viewer")
    payload = dump_snapshot(session)
    envelope = seal_snapshot(payload, pair)
    mailbox.put("vault-hold", invite["secret"], __import__("json").dumps(envelope))
    assert ingest_mailbox_holds() == 1
    assert held_meta(invite["id"])["fingerprint"] == payload["fingerprint"]
    session.close()


def test_vault_http_hold_and_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "app.db")
    client = app.create_app().test_client()
    invite = create_invite("Holder")
    token = f"view.{invite['secret']}"
    headers = {"Authorization": f"Bearer {token}"}
    empty = client.get("/api/friend/vault/meta", headers=headers)
    assert empty.status_code == 200
    assert empty.get_json()["held"] is False
    session = _session(tmp_path, monkeypatch)
    pair = ensure_pair_secret()
    payload = dump_snapshot(session)
    envelope = seal_snapshot(payload, pair)
    session.close()
    posted = client.post("/api/friend/vault", headers=headers, json=envelope)
    assert posted.status_code == 200
    assert posted.get_json()["stored"] is True
    meta = client.get("/api/friend/vault/meta", headers=headers)
    assert meta.get_json()["held"] is True
    assert meta.get_json()["fingerprint"] == payload["fingerprint"]
    got = client.get("/api/friend/vault", headers=headers)
    assert got.get_json()["ciphertext"].startswith("mbd1.")
    forbidden = client.get("/api/friend/vault/meta")
    assert forbidden.status_code == 403


def test_vault_start_does_not_block_first_page(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    import app
    from app import vault

    monkeypatch.setattr(app, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(vault, "tick", lambda: None)
    vault._started = False
    flask_app = app.create_app()
    vault.start_background()
    resp = flask_app.test_client().get("/")
    assert resp.status_code == 200


def test_dashboard_omits_expired(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    session.add(Offer(name="Gone", type=OfferType.WELCOME, bookie_id=sky.id, end_by=date(2020, 1, 1)))
    session.commit()
    from app.services import dashboard_stats

    stats = dashboard_stats(session)
    names = [offer.name for offer in stats["in_progress_offers"]]
    assert "Gone" not in names
    session.close()
