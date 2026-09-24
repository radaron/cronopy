"""Persistent session storage for the Cronometer CLI."""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_session_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "cronopy" / "session.json"


@dataclass
class Session:
    user_id: int
    email: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        return cls(
            user_id=int(data["user_id"]),
            email=data.get("email"),
            cookies=dict(data.get("cookies") or {}),
        )


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
