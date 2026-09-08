"""Чистая логика телеметрии без зависимостей от железа.

Модуль не импортирует MicroPython-модули (network, machine, dht),
поэтому напрямую тестируется pytest на ПК и заливается на ESP32
в корень файловой системы рядом с main.py.
"""

import json

SECONDS_PER_HOUR = 3600
MAX_HOURLY_POINTS = 24
# MicroPython считает time.time() от 2000-01-01, а браузер Date — от
# 1970-01-01. Сдвиг между эпохами в секундах для конвертации меток.
UNIX_EPOCH_OFFSET_SEC = 946684800


def to_unix_ts(device_ts: float) -> int:
    """Переводит метку MicroPython (эпоха 2000 г.) в Unix-метку (эпоха 1970 г.).

    Args:
        device_ts: Метка времени с устройства (time.time()).

    Returns:
        Та же метка в секундах от 1970-01-01 для JavaScript Date.
    """
    return int(device_ts) + UNIX_EPOCH_OFFSET_SEC


def parse_query_params(query_string: str) -> dict:
    """Разбирает query-строку вида 'tz=3&x=1' в словарь.

    Args:
        query_string: Часть URI после '?' (без самого '?').

    Returns:
        Словарь параметров (без URL-декодирования — для цифр достаточно).
    """
    params = {}
    for part in query_string.split("&"):
        pair = part.split("=", 1)
        if len(pair) == 2 and pair[0]:
            params[pair[0]] = pair[1]
    return params


def parse_request_path(raw_request_text: str) -> str:
    """Извлекает URI из текста HTTP-запроса.

    Args:
        raw_request_text: Сырой текст HTTP-запроса (первая строка вида
            'GET /data HTTP/1.1').

    Returns:
        URI запроса или '/' для пустых/битых запросов.
    """
    if not raw_request_text:
        return "/"
    request_parts = raw_request_text.split(" ")
    if len(request_parts) >= 2 and request_parts[1]:
        return request_parts[1]
    return "/"


def trim_history(history: list, max_points: int) -> list:
    """Возвращает хвост истории не длиннее max_points.

    Args:
        history: Список точек телеметрии.
        max_points: Максимальное число хранимых точек.

    Returns:
        Новый список с последними max_points элементами.

    Raises:
        ValueError: Если max_points меньше 1.
    """
    if max_points < 1:
        raise ValueError("max_points должен быть >= 1")
    if len(history) <= max_points:
        return list(history)
    return history[len(history) - max_points :]


def hour_start_ts(ts: float) -> int:
    """Округляет метку времени вниз до начала часа.

    Args:
        ts: Метка времени в секундах (time.time()).

    Returns:
        Метка начала часа в секундах.
    """
    return int(ts) // SECONDS_PER_HOUR * SECONDS_PER_HOUR


def update_hourly_history(
    hourly: list, ts: float, temp: float, hum: float, max_points: int = MAX_HOURLY_POINTS
) -> list:
    """Добавляет замер в часовой бакет (среднее за час).

    Замеры внутри одного часа усредняются инкрементно, при наступлении
    нового часа начинается новый бакет. История обрезается до max_points
    (по умолчанию 24 — последние сутки при шаге 1 час).

    Args:
        hourly: Список часовых точек (изменяется на месте). Точка вида
            {"time": начало часа, "temp": ср. температура,
             "hum": ср. влажность, "count": число замеров}.
        ts: Метка времени замера в секундах.
        temp: Температура замера.
        hum: Влажность замера.
        max_points: Максимальное число хранимых часовых точек.

    Returns:
        Та же история после обновления.

    Raises:
        ValueError: Если max_points меньше 1.
    """
    if max_points < 1:
        raise ValueError("max_points должен быть >= 1")
    hour_start = hour_start_ts(ts)
    if hourly and hourly[-1]["time"] == hour_start:
        bucket = hourly[-1]
        count = bucket["count"] + 1
        bucket["temp"] = (bucket["temp"] * bucket["count"] + temp) / count
        bucket["hum"] = (bucket["hum"] * bucket["count"] + hum) / count
        bucket["count"] = count
    else:
        hourly.append({"time": hour_start, "temp": temp, "hum": hum, "count": 1})
        if len(hourly) > max_points:
            del hourly[0 : len(hourly) - max_points]
    return hourly


def build_telemetry_json_payload(history: list, hourly: list, tz_offset_hours: int = 0) -> str:
    """Сериализует снимок измерений в JSON-строку.

    Args:
        history: Список словарей вида
            [{"time": ..., "temp": ..., "hum": ...}].
        hourly: Список часовых точек за сутки (см. update_hourly_history).
        tz_offset_hours: Поправка часового пояса в часах (для справки клиенту).

    Returns:
        JSON-строка с полями temp, hum, history, hourly и tz_offset.
        Метки hourly конвертированы в Unix-эпоху для JavaScript.
    """
    if history:
        latest_entry = history[-1]
        last_temperature = latest_entry["temp"]
        last_humidity = latest_entry["hum"]
    else:
        last_temperature = None
        last_humidity = None
    hourly_unix = [
        {
            "time": to_unix_ts(point["time"]),
            "temp": point["temp"],
            "hum": point["hum"],
            "count": point.get("count", 1),
        }
        for point in hourly
    ]
    return json.dumps(
        {
            "temp": last_temperature,
            "hum": last_humidity,
            "history": list(history),
            "hourly": hourly_unix,
            "tz_offset": tz_offset_hours,
        }
    )


def build_http_response_bytes(
    status_code_text: str,
    body_content: str,
    content_type: str = "text/html; charset=utf-8",
) -> bytes:
    """Строит полный HTTP-ответ в виде байтов.

    Content-Length считается от UTF-8 байтов тела, а не от числа
    символов — иначе кириллица в HTML ломает длину ответа.

    Args:
        status_code_text: Статус вида '200 OK'.
        body_content: Тело ответа строкой.
        content_type: Значение заголовка Content-Type.

    Returns:
        Готовый HTTP-ответ байтами (заголовки + тело).

    Raises:
        ValueError: Если статус или тело пустые.
    """
    if not status_code_text:
        raise ValueError("status_code_text не должен быть пустым")
    if not body_content:
        raise ValueError("body_content не должен быть пустым")
    body_bytes = body_content.encode("utf-8")
    header = (
        f"HTTP/1.1 {status_code_text}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        "Connection: close\r\n\r\n"
    )
    return header.encode("ascii") + body_bytes
