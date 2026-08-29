import os
from pathlib import Path
from zipfile import ZipFile

from update import apply_zip, is_newer, migrate_layout, parse_latest_text, parse_version


def test_parse_and_compare_versions():
    assert parse_version("v1.4.9") == (1, 4, 9)
    assert parse_version("1.2.0") == (1, 2, 0)
    assert is_newer("1.4.9", "1.4.8") is True
    assert parse_latest_text("1.4.9\n") == "1.4.9"
    assert parse_latest_text("v1.4.9") == "1.4.9"
    assert parse_latest_text("  ") is None
    assert is_newer("v1.2.0", "1.2.0") is False
    assert is_newer("1.2.0", "1.4.0") is False


def _write_zip(zip_path: Path, staging: Path) -> None:
    with ZipFile(zip_path, "w") as archive:
        for path in staging.rglob("*"):
            archive.write(path, path.relative_to(staging.parent))


def test_apply_zip_keeps_data(tmp_path: Path, monkeypatch):
    dest = tmp_path / "install"
    dest.mkdir()
    (dest / "run.py").write_text("old = True\n", encoding="utf-8")
    data = dest / "data"
    data.mkdir()
    (data / "app.db").write_text("keep-me", encoding="utf-8")

    staging = tmp_path / "MatchedBettingDocumenter"
    staging.mkdir()
    (staging / "run.py").write_text("new = True\n", encoding="utf-8")
    (staging / "app").mkdir()
    (staging / "app" / "version.py").write_text('VERSION = "9.9.9"\n', encoding="utf-8")
    (staging / "data").mkdir()
    (staging / "data" / "app.db").write_text("should-not-copy", encoding="utf-8")

    zip_path = tmp_path / "MatchedBettingDocumenter.zip"
    _write_zip(zip_path, staging)

    import update as update_mod

    monkeypatch.setattr(update_mod, "ROOT", dest)
    monkeypatch.setattr(update_mod, "install_root", lambda: dest)
    monkeypatch.setattr(update_mod, "_is_dev_checkout", lambda: False)
    monkeypatch.setattr(update_mod, "_install_requirements", lambda: None)
    apply_zip(zip_path)

    assert (dest / "program" / "run.py").read_text(encoding="utf-8") == "new = True\n"
    assert (dest / "program" / "app" / "version.py").read_text(encoding="utf-8") == 'VERSION = "9.9.9"\n'
    assert not (dest / "run.py").exists()
    assert (data / "app.db").read_text(encoding="utf-8") == "keep-me"


def test_apply_nested_zip_onto_flat_install(tmp_path: Path, monkeypatch):
    dest = tmp_path / "install"
    dest.mkdir()
    (dest / "run.py").write_text("old = True\n", encoding="utf-8")
    (dest / "update.bat").write_text("old\n", encoding="utf-8")
    (dest / "tests").mkdir()
    (dest / "tests" / "x.py").write_text("x\n", encoding="utf-8")
    data = dest / "data"
    data.mkdir()
    (data / "app.db").write_text("keep-me", encoding="utf-8")

    staging = tmp_path / "MatchedBettingDocumenter"
    program = staging / "program"
    program.mkdir(parents=True)
    (program / "run.py").write_text("new = True\n", encoding="utf-8")
    (program / "app").mkdir()
    (program / "app" / "version.py").write_text('VERSION = "9.9.9"\n', encoding="utf-8")
    (staging / "start.bat").write_text("start\n", encoding="utf-8")
    (staging / "start.sh").write_text("start\n", encoding="utf-8")
    (staging / "allow-firewall.bat").write_text("fw\n", encoding="utf-8")
    (staging / "data").mkdir()
    (staging / "data" / "app.db").write_text("should-not-copy", encoding="utf-8")

    zip_path = tmp_path / "MatchedBettingDocumenter.zip"
    _write_zip(zip_path, staging)

    import update as update_mod

    monkeypatch.setattr(update_mod, "ROOT", dest)
    monkeypatch.setattr(update_mod, "install_root", lambda: dest)
    monkeypatch.setattr(update_mod, "_is_dev_checkout", lambda: False)
    monkeypatch.setattr(update_mod, "_install_requirements", lambda: None)
    apply_zip(zip_path)

    assert (dest / "program" / "run.py").read_text(encoding="utf-8") == "new = True\n"
    assert (dest / "start.sh").read_text(encoding="utf-8") == "start\n"
    assert os.access(dest / "start.sh", os.X_OK)
    assert (dest / "allow-firewall.bat").read_text(encoding="utf-8") == "fw\n"
    assert not (dest / "start.bat").exists()
    assert not (dest / "run.py").exists()
    assert not (dest / "update.bat").exists()
    assert not (dest / "tests").exists()
    assert (data / "app.db").read_text(encoding="utf-8") == "keep-me"
    assert "program" in (dest / ".hidden").read_text(encoding="utf-8")


def test_apply_nested_zip_keeps_front_data(tmp_path: Path, monkeypatch):
    dest = tmp_path / "install"
    program = dest / "program"
    program.mkdir(parents=True)
    (program / "run.py").write_text("old = True\n", encoding="utf-8")
    data = dest / "data"
    data.mkdir()
    (data / "app.db").write_text("keep-me", encoding="utf-8")

    staging = tmp_path / "MatchedBettingDocumenter"
    src_program = staging / "program"
    src_program.mkdir(parents=True)
    (src_program / "run.py").write_text("new = True\n", encoding="utf-8")
    (staging / "start.sh").write_text("start\n", encoding="utf-8")

    zip_path = tmp_path / "MatchedBettingDocumenter.zip"
    _write_zip(zip_path, staging)

    import update as update_mod

    monkeypatch.setattr(update_mod, "ROOT", program)
    monkeypatch.setattr(update_mod, "install_root", lambda: dest)
    monkeypatch.setattr(update_mod, "_is_dev_checkout", lambda: False)
    monkeypatch.setattr(update_mod, "_install_requirements", lambda: None)
    apply_zip(zip_path)

    assert (program / "run.py").read_text(encoding="utf-8") == "new = True\n"
    assert (dest / "start.sh").read_text(encoding="utf-8") == "start\n"
    assert (data / "app.db").read_text(encoding="utf-8") == "keep-me"


def test_migrate_tucks_already_flat_update(tmp_path: Path, monkeypatch):
    dest = tmp_path / "install"
    dest.mkdir()
    (dest / "run.py").write_text("flat\n", encoding="utf-8")
    (dest / "update.py").write_text("flat\n", encoding="utf-8")
    (dest / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (dest / "app").mkdir()
    (dest / "app" / "__init__.py").write_text("", encoding="utf-8")
    (dest / "start.sh").write_text("keep\n", encoding="utf-8")
    (dest / "start.bat").write_text("windows\n", encoding="utf-8")
    (dest / "Start.sh").write_text("extra\n", encoding="utf-8")
    (dest / "Start.command").write_text("extra\n", encoding="utf-8")
    (dest / "pack.sh").write_text("x\n", encoding="utf-8")
    data = dest / "data"
    data.mkdir()
    (data / "app.db").write_text("keep-me", encoding="utf-8")

    import update as update_mod

    monkeypatch.setattr(update_mod, "ROOT", dest)
    monkeypatch.setattr(update_mod, "install_root", lambda: dest)
    monkeypatch.setattr(update_mod, "_is_dev_checkout", lambda: False)
    migrate_layout()

    assert (dest / "program" / "run.py").read_text(encoding="utf-8") == "flat\n"
    assert (dest / "program" / "app" / "__init__.py").exists()
    assert (dest / "start.sh").read_text(encoding="utf-8") == "keep\n"
    assert not (dest / "start.bat").exists()
    assert not (dest / "Start.sh").exists()
    assert not (dest / "Start.command").exists()
    assert not (dest / "run.py").exists()
    assert not (dest / "pack.sh").exists()
    assert (data / "app.db").read_text(encoding="utf-8") == "keep-me"
