from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from cronopy.models import Session


def default_session_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "cronopy" / "session.json"


def load_session(path: Path | None = None) -> Session | None:
    path = path or default_session_path()
    if not path.exists():
        return None
    try:
        return Session.from_dict(json.loads(path.read_text()))
    except (ValueError, KeyError, TypeError):
        return None


def save_session(session: Session, path: Path | None = None) -> Path:
    path = path or default_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session.to_dict(), indent=2))
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def delete_session(path: Path | None = None) -> bool:
    path = path or default_session_path()
    if path.exists():
        path.unlink()
        return True
    return False
