"""Тесты чистой логики fbsync (без железа и сети)."""

import pytest

from fbsync import (
    acc_add,
    acc_mean,
    auth_error_hint,
    build_bucket_payload,
    build_current_payload,
    decode_rtc_state,
    describe_http_error,
    encode_rtc_state,
    ensure_id_token,
    extract_error_message,
    needs_refresh,
    new_acc,
    new_state,
    node_url,
    parse_auth_response,
    prune_key,
    refresh_url,
    signup_url,
)


def test_signup_url_contains_key() -> None:
    """URL signup включает API-ключ."""
    url = signup_url("KEY123")
    assert "identitytoolkit" in url
    assert url.endswith("key=KEY123")


def test_refresh_url_contains_key() -> None:
    """URL refresh включает API-ключ."""
    url = refresh_url("KEY123")
    assert "securetoken" in url
    assert url.endswith("key=KEY123")


def test_node_url_format() -> None:
    """URL узла собирается с путём и токеном."""
    url = node_url("https://db.firebaseio.com/", "esp32-1", "hourly/123", "TOK")
    assert url == "https://db.firebaseio.com/devices/esp32-1/hourly/123.json?auth=TOK"


def test_build_current_payload() -> None:
    """Пейлоад текущих показаний имеет нужные поля."""
    assert build_current_payload(21.5, 52.0, 1700000000) == {
        "temp": 21.5,
        "hum": 52.0,
        "ts": 1700000000,
    }


def test_build_bucket_payload() -> None:
    """Пейлоад часового бакета имеет средние и счётчик."""
    assert build_bucket_payload(21.0, 51.0, 12) == {"temp": 21.0, "hum": 51.0, "count": 12}


def test_acc_add_and_mean() -> None:
    """Аккумулятор копит сумму и считает среднее."""
    acc = new_acc(3600)
    acc_add(acc, 20.0, 50.0)
    acc_add(acc, 22.0, 60.0)
    assert acc_mean(acc) == (21.0, 55.0, 2)


def test_prune_key_one_day_older() -> None:
    """Ключ чистки ровно на сутки старше."""
    assert prune_key(3600 + 86400) == 3600


def test_parse_auth_response_camel() -> None:
    """Разбирает ответ signUp (camelCase)."""
    state = parse_auth_response(
        {"idToken": "id", "refreshToken": "rf", "expiresIn": "3600"}, 1000.0
    )
    assert state == {"id_token": "id", "refresh_token": "rf", "expires_at": 4600.0}


def test_parse_auth_response_snake() -> None:
    """Разбирает ответ refresh (snake_case)."""
    state = parse_auth_response(
        {"id_token": "id", "refresh_token": "rf", "expires_in": 3600}, 1000.0
    )
    assert state["expires_at"] == 4600.0


def test_parse_auth_response_missing_tokens() -> None:
    """Ответ без токенов отклоняется."""
    with pytest.raises(ValueError, match="токенов"):
        parse_auth_response({"error": {"message": "bad"}}, 1000.0)


def test_needs_refresh_cases() -> None:
    """Пустой/протухший/далёкий токен различаются."""
    assert needs_refresh(new_state(), 1000.0) is True
    fresh = {"id_token": "id", "refresh_token": "rf", "expires_at": 10000.0}
    assert needs_refresh(fresh, 1000.0) is False
    assert needs_refresh(fresh, 9800.0) is True


def test_ensure_id_token_fresh_no_call() -> None:
    """Свежий токен возвращается без HTTP."""
    calls: list = []
    state = {"id_token": "id", "refresh_token": "rf", "expires_at": 10000.0}
    token = ensure_id_token(lambda u, p: calls.append((u, p)) or {}, "KEY", state, 1000.0)
    assert token == "id"
    assert calls == []


def test_ensure_id_token_signup() -> None:
    """Без refresh-токена идёт signup."""
    posted: list = []

    def _post(url: str, payload: dict) -> dict:
        posted.append((url, payload))
        return {"idToken": "id", "refreshToken": "rf", "expiresIn": "3600"}

    token = ensure_id_token(_post, "KEY", new_state(), 1000.0)
    assert token == "id"
    assert len(posted) == 1 and "identitytoolkit" in posted[0][0]


def test_ensure_id_token_refresh() -> None:
    """С refresh-токеном идёт refresh."""
    posted: list = []

    def _post(url: str, payload: dict) -> dict:
        posted.append((url, payload))
        return {"id_token": "id2", "refresh_token": "rf2", "expires_in": 3600}

    state = {"id_token": "old", "refresh_token": "rf", "expires_at": 100.0}
    token = ensure_id_token(_post, "KEY", state, 1000.0)
    assert token == "id2"
    assert state["refresh_token"] == "rf2"
    assert len(posted) == 1 and "securetoken" in posted[0][0]


def test_rtc_roundtrip() -> None:
    """Состояние переживает encode/decode."""
    state = new_state()
    state["refresh_token"] = "rf"
    state["id_token"] = "id"
    state["expires_at"] = 12345
    state["acc"] = {"hour": 999, "sum_t": 42.0, "sum_h": 84.0, "n": 2}
    state["time_valid"] = True
    restored = decode_rtc_state(encode_rtc_state(state).encode("utf-8"))
    assert restored == state


def test_rtc_decode_garbage() -> None:
    """Мусор даёт состояние по умолчанию."""
    assert decode_rtc_state(b"") == new_state()
    assert decode_rtc_state(None) == new_state()
    assert decode_rtc_state(b"not json") == new_state()
    assert decode_rtc_state('{"tz": 1}'.encode("utf-8")) == new_state()


def test_extract_error_message() -> None:
    """Текст ошибки извлекается из тела ответа Firebase."""
    assert extract_error_message({"error": {"message": "OPERATION_NOT_ALLOWED"}}) == (
        "OPERATION_NOT_ALLOWED"
    )
    assert extract_error_message({}) == ""
    assert extract_error_message({"error": "plain"}) == "plain"
    assert extract_error_message({"error": {"message": "x" * 200}}) == "x" * 160


def test_describe_http_error() -> None:
    """Текст HTTP-ошибки включает код и тело."""
    assert describe_http_error(400, "OPERATION_NOT_ALLOWED") == ("HTTP 400: OPERATION_NOT_ALLOWED")
    assert describe_http_error(401, "") == "HTTP 401"


def test_auth_error_hint_known() -> None:
    """Известные ошибки получают подсказки."""
    assert "Anonymous" in auth_error_hint("HTTP 400: OPERATION_NOT_ALLOWED")
    assert "signup" in auth_error_hint("TOKEN_EXPIRED")
    assert "signup" in auth_error_hint("USER_NOT_FOUND")
    assert "signup" in auth_error_hint("INVALID_REFRESH_TOKEN")
    assert "signup" in auth_error_hint("INVALID_ID_TOKEN")


def test_auth_error_hint_unknown() -> None:
    """Неизвестные ошибки без подсказок."""
    assert auth_error_hint("SOMETHING_ELSE") == ""
    assert auth_error_hint("") == ""
