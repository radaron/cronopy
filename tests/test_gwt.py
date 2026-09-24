import datetime as dt
from typing import cast

import pytest

from conftest import POLICY_HASH, USER_ID
from cronopy.gwt import (
    MODULE_BASE,
    SERVICE,
    Boxed,
    GwtParam,
    GwtProtocolError,
    GwtRequest,
    GwtServerError,
    decode_int,
    decode_response,
    decode_string,
    encode_request,
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
