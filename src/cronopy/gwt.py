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
TYPE_TIME = "com.cronometer.shared.entries.models.Time/1552252503"  # fields: hour, minute, second
TYPE_SHORT = "java.lang.Short/551743396"
TYPE_ARRAY_LIST = "java.util.ArrayList/4159755760"
TYPE_SINGLETON_LIST = "java.util.Collections$SingletonList/1586180994"
TYPE_SERVING = "com.cronometer.shared.entries.models.Serving/2553599101"
TYPE_ADD_ENTRY = "com.cronometer.shared.entries.changes.AddEntryChange/3949104564"
TYPE_DELETE_ENTRY = "com.cronometer.shared.entries.changes.DeleteEntryChange/2820697428"
TYPE_FOOD = "com.cronometer.shared.foods.models.Food/2097636843"
TYPE_MEASURE = "com.cronometer.shared.foods.models.Measure/1979099908"
TYPE_NUTRIENT = "com.cronometer.shared.foods.models.Nutrient/331784102"
NUTRIENT_ENERGY = 208  # USDA nutrient id for energy (kcal)


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


@dataclass(frozen=True)
class Long:
    """A Java ``long`` field. GWT writes longs as base64 tokens, not decimal."""

    value: int


@dataclass(frozen=True)
class GwtObject:
    """A serializable object: its type signature followed by its fields in order.

    ``param_type`` is the signature written in the parameter-type slot when the
    object is a top-level argument, e.g. ``java.util.List`` for a list value.
    """

    type_sig: str
    fields: tuple[GwtField, ...] = ()
    param_type: str | None = None


type GwtField = str | int | float | bool | Boxed | Long | dt.date | dt.time | GwtObject | None
type GwtParam = str | int | Boxed | dt.date | GwtObject


def gwt_list(items: list[GwtObject]) -> GwtObject:
    """A ``java.util.List`` argument, mirroring the frontend's choice of implementation."""
    if len(items) == 1:
        return GwtObject(TYPE_SINGLETON_LIST, (items[0],), param_type="java.util.List")
    return GwtObject(TYPE_ARRAY_LIST, (len(items), *items), param_type="java.util.List")


_LONG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789$_"


def encode_long(value: int) -> str:
    """GWT's base64 long encoding (no padding, most significant digit first)."""
    value &= (1 << 64) - 1
    if value == 0:
        return "A"
    out = ""
    while value:
        out = _LONG_ALPHABET[value & 63] + out
        value >>= 6
    return out


def decode_long(token: str) -> int:
    value = 0
    for ch in token:
        value = (value << 6) | _LONG_ALPHABET.index(ch)
    if value >= 1 << 63:
        value -= 1 << 64
    return value


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
            case GwtObject(param_type=pt, type_sig=sig):
                return pt or sig
            case int():
                return "I"
        raise TypeError(f"Unsupported GWT parameter: {param!r}")

    def _value(self, field: GwtField) -> list[str]:
        match field:
            case None:
                return ["0"]
            case bool():
                return ["1" if field else "0"]
            case str():
                return [self._ref(field)]
            case Boxed(value=v):
                return [self._ref(TYPE_INTEGER), str(v)]
            case Long(value=v):
                return [encode_long(v)]
            case dt.datetime():
                raise TypeError("Pass a date or a time, not a datetime")
            case dt.date():
                return [self._ref(TYPE_DAY), str(field.day), str(field.month), str(field.year)]
            case dt.time():
                return [self._ref(TYPE_TIME), str(field.hour), str(field.minute), str(field.second)]
            case GwtObject(type_sig=sig, fields=fields):
                out = [self._ref(sig)]
                for f in fields:
                    out += self._value(f)
                return out
            case float():
                return [str(int(field)) if field.is_integer() else repr(field)]
            case int():
                return [str(field)]
        raise TypeError(f"Unsupported GWT value: {field!r}")

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


class GwtReader:
    """Sequential reader over a decoded response, in the order the server wrote it."""

    def __init__(self, body: str) -> None:
        wire = decode_response(body)
        # Wire order is reversed: [...values..., [strings], flags, version].
        self.strings: list[str] = wire[-3]
        self.tokens: list[Any] = wire[:-3][::-1]
        self.pos = 0

    def type_index(self, type_sig: str) -> int | None:
        """1-based string-table index of ``type_sig``, or ``None`` if absent."""
        return self.strings.index(type_sig) + 1 if type_sig in self.strings else None

    def read(self) -> Any:
        value = self.tokens[self.pos]
        self.pos += 1
        return value

    def read_string(self) -> str | None:
        idx = self.read()
        return self.strings[idx - 1] if idx else None

    def read_long(self) -> int:
        return decode_long(self.read())

    def skip_object_ref(self) -> None:
        """Consume a nullable boxed ``Short`` slot: null, back-reference, or type + value."""
        head = self.read()
        if head > 0:
            self.read()
