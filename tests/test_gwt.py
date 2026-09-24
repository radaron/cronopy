import datetime as dt
from typing import cast

import pytest

from conftest import POLICY_HASH, USER_ID
from cronopy.gwt import (
    MODULE_BASE,
    SERVICE,
    TYPE_ADD_ENTRY,
    TYPE_SERVING,
    Boxed,
    GwtObject,
    GwtParam,
    GwtProtocolError,
    GwtReader,
    GwtRequest,
    GwtServerError,
    Long,
    decode_int,
    decode_long,
    decode_response,
    decode_string,
    encode_long,
    encode_request,
    gwt_list,
)


@pytest.mark.parametrize(
    ("method", "params", "expected"),
    [
        (
            "authenticate",
            (Boxed(120),),
            f"7|0|5|{MODULE_BASE}|{POLICY_HASH}|{SERVICE}|authenticate|"
            "java.lang.Integer/3438268394|1|2|3|4|1|5|5|120|",
        ),
        (
            "logout",
            ("NONCE1",),
            f"7|0|6|{MODULE_BASE}|{POLICY_HASH}|{SERVICE}|logout|"
            "java.lang.String/2004016611|NONCE1|1|2|3|4|1|5|6|",
        ),
        (
            "getPreference",
            ("NONCE1", "weightGoal"),
            f"7|0|7|{MODULE_BASE}|{POLICY_HASH}|{SERVICE}|getPreference|"
            "java.lang.String/2004016611|NONCE1|weightGoal|1|2|3|4|2|5|5|6|7|",
        ),
        (
            "getCaloriesConsumedAndBurned",
            ("NONCE1", USER_ID, dt.date(2026, 9, 24), dt.date(2026, 9, 25)),
            f"7|0|8|{MODULE_BASE}|{POLICY_HASH}|{SERVICE}|getCaloriesConsumedAndBurned|"
            "java.lang.String/2004016611|I|com.cronometer.shared.entries.models.Day/782579793|"
            f"NONCE1|1|2|3|4|4|5|6|7|7|8|{USER_ID}|7|24|9|2026|7|25|9|2026|",
        ),
    ],
)
def test_gwt_request_encoding(method, params, expected):
    assert GwtRequest(POLICY_HASH, method).encode(*params) == expected


def test_gwt_request_rejects_unknown_type():
    with pytest.raises(TypeError):
        GwtRequest(POLICY_HASH, "x").encode(cast("GwtParam", 1.5))


def test_encode_request_shortcut():
    assert encode_request(POLICY_HASH, "logout", "N") == GwtRequest(POLICY_HASH, "logout").encode(
        "N"
    )


def test_decode_response_ok():
    assert decode_response('//OK[0,1,["[[D/158574334"],0,7]') == [0, 1, ["[[D/158574334"], 0, 7]


def test_decode_response_errors():
    with pytest.raises(GwtServerError):
        decode_response("//EX[0,0,7]")
    with pytest.raises(GwtProtocolError):
        decode_response("<html>login</html>")


def test_decode_int():
    assert decode_int("//OK[17669754,[],0,7]") == 17669754
    with pytest.raises(GwtProtocolError):
        decode_int('//OK[1,["x"],0,7]'.replace("1,", '"1",'))
    with pytest.raises(GwtProtocolError):
        decode_int("//OK[]")


def test_decode_string():
    assert decode_string('//OK[1,["-1.65"],0,7]') == "-1.65"
    assert decode_string("//OK[0,[],0,7]") is None


UPDATE_DIARY_EXAMPLE = (
    f"7|0|13|{MODULE_BASE}|{POLICY_HASH}|{SERVICE}|updateDiary|java.lang.String/2004016611|I|"
    "java.util.List|1886e0feaa8433af4f7b63f5cff669c5|"
    "java.util.Collections$SingletonList/1586180994|"
    "com.cronometer.shared.entries.changes.AddEntryChange/3949104564|"
    "com.cronometer.shared.entries.models.Serving/2553599101|"
    "com.cronometer.shared.entries.models.Day/782579793|"
    "com.cronometer.shared.entries.models.Time/1552252503|"
    "1|2|3|4|3|5|6|7|8|17669754|9|10|1|1|11|12|24|9|2026|1|1|0|2|13|20|44|0|0|100|455715|A|1025057|0|0|"
)


def test_encode_update_diary_matches_captured_request():
    serving = GwtObject(
        TYPE_SERVING,
        (
            dt.date(2026, 9, 24),
            True,
            True,
            None,
            2,
            dt.time(20, 44),
            0,
            100.0,
            455715,
            Long(0),
            1025057,
            0,
            0,
        ),
    )
    change = GwtObject(TYPE_ADD_ENTRY, (True, True, serving))
    body = encode_request(
        POLICY_HASH,
        "updateDiary",
        "1886e0feaa8433af4f7b63f5cff669c5",
        17669754,
        gwt_list([change]),
    )
    assert body == UPDATE_DIARY_EXAMPLE


def test_gwt_list_of_many_uses_array_list():
    body = encode_request(POLICY_HASH, "m", gwt_list([GwtObject("X/1"), GwtObject("X/1")]))
    assert "|java.util.ArrayList/4159755760|" in body
    assert body.endswith("|1|2|3|4|1|5|6|2|7|7|")


@pytest.mark.parametrize(
    ("value", "token"),
    [(0, "A"), (5207895104, "E2aixA"), (63, "_"), (64, "BA"), (-1, "P__________")],
)
def test_long_roundtrip(value, token):
    assert encode_long(value) == token
    assert decode_long(token) == value


def test_reader_walks_tokens_in_write_order():
    r = GwtReader('//OK[3,2,1,["a","b"],0,7]')
    assert r.strings == ["a", "b"]
    assert r.type_index("b") == 2
    assert r.type_index("zzz") is None
    assert (r.read(), r.read_string(), r.read()) == (1, "b", 3)
