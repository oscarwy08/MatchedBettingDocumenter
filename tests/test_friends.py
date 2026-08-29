from app.friends import (
    apply_account,
    create_invite,
    decrypt_view,
    encrypt_view,
    export_account,
    invite_by_secret,
    is_viewer_secret,
    load_cache,
    parse_friend_code,
    store_cache,
)
from app.sync import authorize_device, ensure_state, start_share


def test_invite_and_friend_token_cannot_write(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    ensure_state()
    invite = create_invite("Sam")
    secret = invite["secret"]
    assert is_viewer_secret(secret)
    assert is_viewer_secret("view." + secret)
    assert invite_by_secret("view." + secret)["id"] == invite["id"]
    assert not authorize_device("view." + secret)
    assert not authorize_device(secret)
    pin = start_share()
    assert authorize_device(pin)
    assert not authorize_device("view." + secret)


def test_export_apply_account(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    create_invite("Sam")
    blob = export_account()
    assert blob["invites"][0]["nickname"] == "Sam"
    apply_account({"account_name": "House", "invites": [], "friends": [{"id": "ab", "secret": "x" * 20, "nickname": "Alex"}]})
    again = export_account()
    assert again["account_name"] == "House"
    assert again["invites"] == []
    assert again["friends"][0]["nickname"] == "Alex"


def test_encrypt_round_trip():
    payload = {"stats": {"net_profit": "1.50"}, "recent_bets": []}
    blob = encrypt_view("super-secret", payload)
    assert blob.startswith("mbd1.")
    assert decrypt_view("super-secret", blob) == payload


def test_parse_friend_code():
    secret, hosts = parse_friend_code("view.abcDEF1234567890@10.0.0.2:5050+8.8.8.8:5050")
    assert secret == "abcDEF1234567890"
    assert hosts[0] == "10.0.0.2:5050"
    assert hosts[1] == "8.8.8.8:5050"
    secret, hosts = parse_friend_code("view.abcDEF1234567890xyz")
    assert secret == "abcDEF1234567890xyz"
    assert hosts == []


def test_last_available_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    dto = {"nickname": "Alex", "stats": {"net_profit": "4.00"}, "recent_bets": [], "profit_by_bookie": []}
    store_cache("abc123", dto)
    cached = load_cache("abc123")
    assert cached["payload"]["stats"]["net_profit"] == "4.00"
    assert cached["fetched_at"]


def test_view_dto_has_no_full_dump(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from app.db import init_db
    from app.friends import view_dto
    from app.seed import seed_accounts

    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    session.commit()
    dto = view_dto(session, nickname="House")
    session.close()
    assert dto["nickname"] == "House"
    assert "stats" in dto
    assert "accounts" not in dto
    assert "bets" not in dto
    assert "transfers" not in dto
