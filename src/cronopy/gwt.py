"""Encoding and decoding of Cronometer's GWT-RPC wire format.

Request body: ``7|0|<n>|<n strings>|<tokens>|``. Tokens are 1-based indexes
into the string table (module base, policy hash, service, method, parameter
type signatures, string values) or literal ints. Objects are written as their
type index followed by their fields.

Response body: ``//OK[...]`` on success, ``//EX[...]`` on a server-side
exception. The JSON array is in *reversed* wire order: the last elements are
the protocol version, flags and string table; values are read from the end.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

BASE_URL = "https://cronometer.com"
MODULE_BASE = f"{BASE_URL}/cronometer/"
SERVICE = "com.cronometer.shared.rpc.CronometerService"

# GWT-RPC type signatures: "<class>/<CRC32 of the class's serializable field
# layout>". The server rejects a call if the hash does not match its own
# compiled copy, so these only change when Cronometer alters the class fields.
TYPE_STRING = "java.lang.String/2004016611"
TYPE_INTEGER = "java.lang.Integer/3438268394"
TYPE_DAY = "com.cronometer.shared.entries.models.Day/782579793"  # fields: day, month, year


class GwtError(Exception):
    """Base error for GWT-RPC decoding."""


class GwtServerError(GwtError):
    """The server answered with ``//EX``: the call raised remotely."""


class GwtProtocolError(GwtError):
    """The body is not a GWT-RPC response (e.g. an HTML login page)."""


@dataclass(frozen=True)
class Boxed:
    """A ``java.lang.Integer`` RPC argument (object), as opposed to a primitive ``int``."""

    value: int


GwtParam = str | int | Boxed | dt.date


class GwtRequest:
    """Encoder for one ``CronometerService.<method>(*params)`` request body."""

    def __init__(self, policy_hash: str, method: str) -> None:
        self._strings: list[str] = []
        self._header = [self._ref(s) for s in (MODULE_BASE, policy_hash, SERVICE, method)]

    def _ref(self, value: str) -> str:
        if value not in self._strings:
            self._strings.append(value)
        return str(self._strings.index(value) + 1)

    @staticmethod
    def _type_of(param: GwtParam) -> str:
        match param:
            case str():
                return TYPE_STRING
            case Boxed():
                return TYPE_INTEGER
            case dt.date():
                return TYPE_DAY
            case int():
                return "I"
        raise TypeError(f"Unsupported GWT parameter: {param!r}")

    def _value(self, param: GwtParam) -> list[str]:
        match param:
            case str():
                return [self._ref(param)]
            case Boxed(value=v):
                return [self._ref(TYPE_INTEGER), str(v)]
            case dt.date():
                return [self._ref(TYPE_DAY), str(param.day), str(param.month), str(param.year)]
            case int():
                return [str(param)]
        raise TypeError(f"Unsupported GWT parameter: {param!r}")

    def encode(self, *params: GwtParam) -> str:
        tokens = [*self._header, str(len(params))]
        tokens += [self._ref(self._type_of(p)) for p in params]
        for p in params:
            tokens += self._value(p)
        return f"7|0|{len(self._strings)}|" + "|".join(self._strings) + "|" + "|".join(tokens) + "|"


def encode_request(policy_hash: str, method: str, *params: GwtParam) -> str:
    return GwtRequest(policy_hash, method).encode(*params)


def decode_response(body: str) -> list[Any]:
    """Return the ``//OK[...]`` token list in wire (reversed) order."""
    if body.startswith("//OK"):
        return json.loads(body[4:])
    if body.startswith("//EX"):
        raise GwtServerError(body[:200])
    raise GwtProtocolError(body[:200])


def decode_int(body: str) -> int:
    """Decode a response carrying a single primitive ``int``."""
    tokens = decode_response(body)
    # Wire order: [<int>, [strings], 0, 7]
    if not tokens or isinstance(tokens[0], bool) or not isinstance(tokens[0], int):
        raise GwtProtocolError(f"Expected an int, got {body[:200]}")
    return tokens[0]


def decode_string(body: str) -> str | None:
    """Decode a response carrying a single ``String`` (or ``null``)."""
    tokens = decode_response(body)
    # Wire order: [<string idx or 0>, [strings], 0, 7]
    table = tokens[-3]
    return table[0] if tokens[0] and table else None
