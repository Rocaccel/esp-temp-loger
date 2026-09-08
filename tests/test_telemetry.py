"""Тесты чистой логики telemetry (без железа, без мока)."""

import json

import pytest

from telemetry import (
    UNIX_EPOCH_OFFSET_SEC,
    build_http_response_bytes,
    build_telemetry_json_payload,
    hour_start_ts,
    parse_query_params,
    parse_request_path,
    to_unix_ts,
    trim_history,
    update_hourly_history,
)


def test_parse_request_path_data() -> None:
    """Разбирает типовой GET-запрос к /data."""
    assert parse_request_path("GET /data HTTP/1.1\r\nHost: x") == "/data"


def test_parse_request_path_root() -> None:
    """Разбирает запрос корня."""
    assert parse_request_path("GET / HTTP/1.1") == "/"


def test_parse_request_path_empty() -> None:
    """Пустой запрос даёт корень."""
    assert parse_request_path("") == "/"


def test_parse_request_path_malformed() -> None:
    """Битый запрос без URI даёт корень."""
    assert parse_request_path("GARBAGE") == "/"
    assert parse_request_path("GET ") == "/"


def test_trim_history_keeps_tail() -> None:
    """Обрезка оставляет последние max_points точек."""
    history = [{"i": i} for i in range(10)]
    assert trim_history(history, 3) == [{"i": 7}, {"i": 8}, {"i": 9}]


def test_trim_history_short_list_unchanged() -> None:
    """Короткая история возвращается копией целиком."""
    history = [{"i": 1}]
    result = trim_history(history, 60)
    assert result == history
    assert result is not history


def test_trim_history_invalid_max() -> None:
    """Некорректный лимит отклоняется."""
    with pytest.raises(ValueError, match="max_points"):
        trim_history([], 0)


def test_build_payload_empty_history() -> None:
    """Пустая история даёт null в temp/hum, пустой hourly и tz 0."""
    payload = json.loads(build_telemetry_json_payload([], []))
    assert payload == {
        "temp": None,
        "hum": None,
        "history": [],
        "hourly": [],
        "tz_offset": 0,
    }


def test_build_payload_latest_values() -> None:
    """Последняя точка попадает в temp/hum."""
    history = [
        {"time": 1, "temp": 20.0, "hum": 50.0},
        {"time": 2, "temp": 21.5, "hum": 52.0},
    ]
    hourly = [{"time": 0, "temp": 21.0, "hum": 51.0, "count": 2}]
    payload = json.loads(build_telemetry_json_payload(history, hourly, tz_offset_hours=3))
    assert payload["temp"] == 21.5
    assert payload["hum"] == 52.0
    assert payload["history"] == history
    assert payload["hourly"] == [
        {"time": UNIX_EPOCH_OFFSET_SEC, "temp": 21.0, "hum": 51.0, "count": 2}
    ]
    assert payload["tz_offset"] == 3


def test_to_unix_ts_adds_epoch_offset() -> None:
    """Метки переводятся из эпохи 2000 г. в эпоху 1970 г."""
    assert to_unix_ts(0.0) == UNIX_EPOCH_OFFSET_SEC
    assert to_unix_ts(3600.0) == UNIX_EPOCH_OFFSET_SEC + 3600


def test_parse_query_params() -> None:
    """Разбирает типовую query-строку."""
    assert parse_query_params("tz=3") == {"tz": "3"}
    assert parse_query_params("a=1&b=2") == {"a": "1", "b": "2"}
    assert parse_query_params("") == {}
    assert parse_query_params("tz") == {}
    assert parse_query_params("tz=") == {"tz": ""}


def test_hour_start_ts_truncates() -> None:
    """Метка округляется вниз до начала часа."""
    assert hour_start_ts(3700.0) == 3600
    assert hour_start_ts(3600.0) == 3600
    assert hour_start_ts(7199.9) == 3600


def test_update_hourly_averages_within_hour() -> None:
    """Замеры внутри часа усредняются в один бакет."""
    hourly: list = []
    update_hourly_history(hourly, 3600.0, 20.0, 50.0)
    update_hourly_history(hourly, 3700.0, 22.0, 60.0)
    assert len(hourly) == 1
    assert hourly[0]["time"] == 3600
    assert hourly[0]["temp"] == pytest.approx(21.0)
    assert hourly[0]["hum"] == pytest.approx(55.0)
    assert hourly[0]["count"] == 2


def test_update_hourly_new_hour_starts_bucket() -> None:
    """Новый час начинает новый бакет."""
    hourly: list = []
    update_hourly_history(hourly, 3600.0, 20.0, 50.0)
    update_hourly_history(hourly, 7200.0, 25.0, 55.0)
    assert [b["time"] for b in hourly] == [3600, 7200]
    assert hourly[1]["count"] == 1


def test_update_hourly_trims_to_24() -> None:
    """История обрезается до последних 24 часов."""
    hourly: list = []
    for h in range(30):
        update_hourly_history(hourly, h * 3600.0, 20.0, 50.0)
    assert len(hourly) == 24
    assert hourly[0]["time"] == 6 * 3600
    assert hourly[-1]["time"] == 29 * 3600


def test_update_hourly_invalid_max() -> None:
    """Некорректный лимит отклоняется."""
    with pytest.raises(ValueError, match="max_points"):
        update_hourly_history([], 3600.0, 20.0, 50.0, max_points=0)


def test_build_http_response_cyrillic_length() -> None:
    """Content-Length считается от UTF-8 байтов, а не символов (P0)."""
    body = "<h1>Климат-монитор</h1>"
    raw = build_http_response_bytes("200 OK", body)
    header_text, _, body_bytes = raw.partition(b"\r\n\r\n")
    header = header_text.decode("ascii")
    assert f"Content-Length: {len(body.encode('utf-8'))}" in header
    assert len(body.encode("utf-8")) != len(body)  # кириллица шире 1 байта
    assert body_bytes == body.encode("utf-8")


def test_build_http_response_status_and_type() -> None:
    """Статус и Content-Type попадают в заголовки."""
    raw = build_http_response_bytes("404 Not Found", "<h1>x</h1>", "text/html")
    header = raw.split(b"\r\n\r\n")[0].decode("ascii")
    assert header.startswith("HTTP/1.1 404 Not Found")
    assert "Content-Type: text/html" in header


def test_build_http_response_empty_rejected() -> None:
    """Пустые статус и тело отклоняются."""
    with pytest.raises(ValueError, match="status_code_text"):
        build_http_response_bytes("", "body")
    with pytest.raises(ValueError, match="body_content"):
        build_http_response_bytes("200 OK", "")
