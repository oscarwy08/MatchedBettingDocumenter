from app.access import is_trusted_client, remote_api_allowed


def test_trusted_is_this_computer_or_lan():
    assert is_trusted_client("127.0.0.1")
    assert is_trusted_client("::1")
    assert is_trusted_client("192.168.1.20")
    assert is_trusted_client("10.0.0.8")
    assert is_trusted_client("172.16.0.4")


def test_public_internet_is_not_trusted():
    assert not is_trusted_client("8.8.8.8")
    assert not is_trusted_client("1.1.1.1")
    assert not is_trusted_client("")
    assert not is_trusted_client("not-an-ip")


def test_remote_only_token_apis():
    assert remote_api_allowed("/api/sync/status")
    assert remote_api_allowed("/api/sync/snapshot")
    assert remote_api_allowed("/api/sync/hello")
    assert remote_api_allowed("/api/sync/482193")
    assert remote_api_allowed("/api/friend/view")
    assert not remote_api_allowed("/")
    assert not remote_api_allowed("/sync")
    assert not remote_api_allowed("/friends")
    assert not remote_api_allowed("/settings")
    assert not remote_api_allowed("/api/sync/freshness")
    assert not remote_api_allowed("/api/sync/pull")
    assert not remote_api_allowed("/api/update-status")
