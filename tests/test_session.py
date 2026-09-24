import json
import stat

import pytest

from cronopy.session import (
    Session,
    default_session_path,
    delete_session,
    load_session,
    save_session,
)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "nested" / "session.json"


def test_default_path_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_session_path() == tmp_path / "cronopy" / "session.json"


def test_default_path_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert default_session_path() == tmp_path / ".config" / "cronopy" / "session.json"


def test_save_and_load_roundtrip(path):
    s = Session(user_id=42, email="a@b.c", cookies={"sesnonce": "n", "JSESSIONID": "j"})
    out = save_session(s, path)
    assert out == path
    assert load_session(path) == s
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_saved_file_has_no_gwt_hashes(path):
    save_session(Session(user_id=1, cookies={"sesnonce": "n"}), path)
    data = json.loads(path.read_text())
    assert set(data) == {"user_id", "email", "cookies"}


def test_load_missing_returns_none(path):
    assert load_session(path) is None


@pytest.mark.parametrize("content", ["not json", "{}", '{"user_id": "abc"}', "[]"])
def test_load_corrupt_returns_none(path, content):
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert load_session(path) is None


def test_delete_session(path):
    assert delete_session(path) is False
    save_session(Session(user_id=1), path)
    assert delete_session(path) is True
    assert not path.exists()
