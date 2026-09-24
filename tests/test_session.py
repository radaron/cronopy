import json

import pytest

from cronopy.models import Session
from cronopy.session import default_session_path, delete_session, load_session, save_session


@pytest.fixture
def path(tmp_path):
    return tmp_path / "cfg" / "cronopy" / "session.json"


def test_default_path_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_session_path() == tmp_path / "cronopy" / "session.json"


def test_default_path_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_session_path() == tmp_path / ".config" / "cronopy" / "session.json"


def test_save_and_load_roundtrip(path):
    s = Session(user_id=42, token="tok", email="a@b.c", timezone="Europe/Budapest")
    saved = save_session(s, path)
    assert saved == path
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_session(path) == s


def test_saved_file_holds_token_not_cookies(path):
    save_session(Session(user_id=1, token="tok"), path)
    data = json.loads(path.read_text())
    assert data == {"user_id": 1, "token": "tok", "email": None, "timezone": None}


def test_load_missing_returns_none(path):
    assert load_session(path) is None


@pytest.mark.parametrize(
    "content",
    ["not json", "{}", '{"user_id": "x", "token": "t"}', '{"user_id": 1}', '{"cookies": {}}'],
)
def test_load_corrupt_or_legacy_returns_none(path, content):
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert load_session(path) is None


def test_delete_session(path):
    assert delete_session(path) is False
    save_session(Session(user_id=1, token="t"), path)
    assert delete_session(path) is True
    assert not path.exists()
