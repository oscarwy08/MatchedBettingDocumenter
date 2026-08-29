from app import mailbox
from app.friends import decrypt_view, encrypt_view, fetch_live


def test_topic_hides_secret():
    topic = mailbox.topic_for("view", "super-secret-value")
    assert "super-secret" not in topic
    assert topic.startswith("mbd/v1/view/")


def test_fetch_live_uses_mailbox_when_direct_fails(monkeypatch):
    payload = {"nickname": "Sam", "stats": {"net_profit": "3.00"}, "recent_bets": [], "profit_by_bookie": []}
    secret = "aaaaaaaaaaaaaaaa"
    store = {}

    def fake_put(topic, blob):
        store[topic] = blob

    def fake_get(topic, timeout):
        return store.get(topic)

    monkeypatch.setattr(mailbox, "_put", fake_put)
    monkeypatch.setattr(mailbox, "_get", fake_get)
    mailbox.put("view", secret, encrypt_view(secret, payload))

    def boom(*_args, **_kwargs):
        raise TimeoutError("no lan")

    monkeypatch.setattr("app.live_sync.fetch_json", boom)
    view = fetch_live({"secret": secret, "lan_host": "100.120.12.40", "port": 5050, "host": "100.120.12.40:5050"})
    assert view["stats"]["net_profit"] == "3.00"


def test_fetch_live_mailbox_without_address(monkeypatch):
    payload = {"nickname": "Sam", "stats": {"net_profit": "1.00"}, "recent_bets": [], "profit_by_bookie": []}
    secret = "bbbbbbbbbbbbbbbb"
    store = {}
    monkeypatch.setattr(mailbox, "_put", lambda topic, blob: store.__setitem__(topic, blob))
    monkeypatch.setattr(mailbox, "_get", lambda topic, timeout: store.get(topic))
    mailbox.put("view", secret, encrypt_view(secret, payload))
    view = fetch_live({"secret": secret})
    assert view["nickname"] == "Sam"
