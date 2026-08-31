from app.linux_firewall import _ufw_allows, allow_port


def test_allow_port_skips_windows(monkeypatch):
    monkeypatch.setattr("app.linux_firewall.sys.platform", "win32")
    assert allow_port(5050) is False


def test_ufw_allows_reads_user_rules(tmp_path, monkeypatch):
    rules = tmp_path / "user.rules"
    rules.write_text(
        "### tuple ### allow tcp 5050 0.0.0.0/0 any 0.0.0.0/0 in\n"
        "-A ufw-user-input -p tcp --dport 5050 -j ACCEPT\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.linux_firewall.Path", lambda *_a, **_k: rules)
    assert _ufw_allows(5050) is True
    assert _ufw_allows(6060) is False
