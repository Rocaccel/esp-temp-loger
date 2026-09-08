"""Синхронизация с Firebase Realtime DB: пути, пейлоады, auth, RTC.

Модуль не импортирует железо и сеть — HTTP-вызовы передаются
колбэками (на плате это обёртки над urequests, в тестах — моки).
Токены в логи не пишем никогда.
"""

import json

TOKEN_REFRESH_MARGIN_SEC = 300
TOKEN_TTL_SEC = 3600
SECONDS_PER_DAY = 86400


def signup_url(api_key: str) -> str:
    """URL анонимной регистрации Firebase Auth.

    Args:
        api_key: Публичный web API-ключ проекта.

    Returns:
        URL Identity Toolkit signUp.
    """
    return "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=" + api_key


def refresh_url(api_key: str) -> str:
    """URL обновления токена Firebase Auth.

    Args:
        api_key: Публичный web API-ключ проекта.

    Returns:
        URL Secure Token API.
    """
    return "https://securetoken.googleapis.com/v1/token?key=" + api_key


def node_url(db_url: str, device_id: str, node: str, id_token: str) -> str:
    """Строит REST URL узла Realtime Database.

    Args:
        db_url: Корень базы (https://....firebaseio.com).
        device_id: Идентификатор устройства.
        node: Путь узла (например 'current' или 'hourly/123').
        id_token: Токен авторизации (в логи не попадает).

    Returns:
        Полный URL с ?auth=.
    """
    base = db_url.rstrip("/")
    return base + "/devices/" + device_id + "/" + node + ".json?auth=" + id_token


def build_current_payload(temp: float, hum: float, ts_unix: int) -> dict:
    """Строит пейлоад текущих показаний.

    Args:
        temp: Температура.
        hum: Влажность.
        ts_unix: Метка времени (Unix-эпоха).

    Returns:
        Словарь для PUT в узел current.
    """
    return {"temp": temp, "hum": hum, "ts": ts_unix}


def build_bucket_payload(avg_temp: float, avg_hum: float, count: int) -> dict:
    """Строит пейлоад часового бакета.

    Args:
        avg_temp: Средняя температура за час.
        avg_hum: Средняя влажность за час.
        count: Число замеров в бакете.

    Returns:
        Словарь для PUT в узел hourly/{час}.
    """
    return {"temp": avg_temp, "hum": avg_hum, "count": count}


def new_acc(hour_start: int) -> dict:
    """Создаёт пустой часовой аккумулятор.

    Args:
        hour_start: Метка начала часа (Unix-эпоха).

    Returns:
        Словарь аккумулятора.
    """
    return {"hour": hour_start, "sum_t": 0.0, "sum_h": 0.0, "n": 0}


def acc_add(acc: dict, temp: float, hum: float) -> dict:
    """Добавляет замер в аккумулятор (меняет на месте).

    Args:
        acc: Словарь аккумулятора.
        temp: Температура замера.
        hum: Влажность замера.

    Returns:
        Тот же аккумулятор.
    """
    acc["sum_t"] = acc["sum_t"] + temp
    acc["sum_h"] = acc["sum_h"] + hum
    acc["n"] = acc["n"] + 1
    return acc


def acc_mean(acc: dict) -> tuple:
    """Считает средние аккумулятора.

    Args:
        acc: Словарь аккумулятора с n > 0.

    Returns:
        Кортеж (средняя температура, средняя влажность, число замеров).
    """
    count = acc["n"]
    return (acc["sum_t"] / count, acc["sum_h"] / count, count)


def prune_key(hour_start: int) -> int:
    """Метка бакета, выходящего из 24-часового окна.

    Args:
        hour_start: Метка текущего часа (Unix-эпоха).

    Returns:
        Метка бакета ровно на сутки старше.
    """
    return hour_start - SECONDS_PER_DAY


def parse_auth_response(data: dict, now: float) -> dict:
    """Разбирает ответ Auth API (camel и snake ключи).

    Args:
        data: Словарь ответа (signUp даёт camelCase, refresh — snake_case).
        now: Текущее время в секундах.

    Returns:
        Словарь {id_token, refresh_token, expires_at}.

    Raises:
        ValueError: Если в ответе нет токенов.
    """
    id_token = data.get("idToken", data.get("id_token", ""))
    refresh_token = data.get("refreshToken", data.get("refresh_token", ""))
    ttl_raw = data.get("expiresIn", data.get("expires_in", TOKEN_TTL_SEC))
    if not id_token or not refresh_token:
        raise ValueError("В ответе Auth нет токенов")
    return {
        "id_token": id_token,
        "refresh_token": refresh_token,
        "expires_at": now + int(ttl_raw),
    }


def extract_error_message(data: dict) -> str:
    """Извлекает текст ошибки Firebase из тела ответа.

    Args:
        data: Распарсенное тело ответа (ошибка лежит в data['error']).

    Returns:
        Текст message обрезанный до 160 символов или пустая строка.
    """
    error = data.get("error", {})
    if isinstance(error, dict):
        return str(error.get("message", ""))[:160]
    return str(error)[:160]


def auth_error_hint(message: str) -> str:
    """Подбирает подсказку по тексту ошибки Auth API.

    Args:
        message: Текст ошибки Firebase (например OPERATION_NOT_ALLOWED).

    Returns:
        Подсказка для serial-лога или пустая строка.
    """
    hints = (
        ("OPERATION_NOT_ALLOWED", "включи Anonymous-провайдер в Authentication"),
        ("INVALID_ID_TOKEN", "токен отклонён, будет новый signup"),
        ("USER_NOT_FOUND", "пользователь удалён, будет новый signup"),
        ("TOKEN_EXPIRED", "токен протух, будет новый signup"),
        ("INVALID_REFRESH_TOKEN", "refresh-токен бит, будет новый signup"),
    )
    for key, hint in hints:
        if key in message:
            return hint
    return ""


def describe_http_error(code: int, message: str) -> str:
    """Строит текст HTTP-ошибки для лога.

    Args:
        code: HTTP-статус.
        message: Текст ошибки из тела ответа (может быть пустым).

    Returns:
        Строка вида 'HTTP 400: OPERATION_NOT_ALLOWED' или 'HTTP 400'.
    """
    if message:
        return f"HTTP {code}: {message}"
    return f"HTTP {code}"


def needs_refresh(state: dict, now: float) -> bool:
    """Проверяет, нужно ли обновить токен.

    Args:
        state: Словарь {id_token, refresh_token, expires_at}.
        now: Текущее время в секундах.

    Returns:
        True если токена нет или он истекает раньше запаса.
    """
    if not state.get("id_token"):
        return True
    return now >= state.get("expires_at", 0) - TOKEN_REFRESH_MARGIN_SEC


def ensure_id_token(post, api_key: str, state: dict, now: float) -> str:
    """Возвращает действующий id_token, при необходимости обновляя.

    Args:
        post: Колбэк post(url, payload) -> dict ответа.
        api_key: Публичный web API-ключ проекта.
        state: Словарь токенов (обновляется на месте).
        now: Текущее время в секундах.

    Returns:
        Действующий id_token.

    Raises:
        ValueError: Если Auth API вернул ответ без токенов.
        OSError: При сетевых/HTTP ошибках колбэка.
    """
    if not needs_refresh(state, now):
        return state["id_token"]
    if state.get("refresh_token"):
        data = post(
            refresh_url(api_key),
            {"grant_type": "refresh_token", "refresh_token": state["refresh_token"]},
        )
    else:
        data = post(signup_url(api_key), {"returnSecureToken": True})
    fresh = parse_auth_response(data, now)
    state["id_token"] = fresh["id_token"]
    state["refresh_token"] = fresh["refresh_token"]
    state["expires_at"] = fresh["expires_at"]
    return state["id_token"]


def new_state() -> dict:
    """Создаёт пустое состояние цикла (токены + аккумулятор + флаг времени).

    Returns:
        Словарь состояния для RTC.
    """
    return {
        "refresh_token": "",
        "id_token": "",
        "expires_at": 0,
        "acc": new_acc(0),
        "time_valid": False,
    }


def encode_rtc_state(state: dict) -> str:
    """Сериализует состояние для RTC.memory().

    Args:
        state: Словарь состояния цикла.

    Returns:
        JSON-строка состояния.
    """
    return json.dumps(
        {
            "refresh_token": state.get("refresh_token", ""),
            "id_token": state.get("id_token", ""),
            "expires_at": state.get("expires_at", 0),
            "acc": state.get("acc", new_acc(0)),
            "time_valid": bool(state.get("time_valid", False)),
        }
    )


def decode_rtc_state(raw) -> dict:
    """Восстанавливает состояние из RTC.memory().

    Args:
        raw: Байты из RTC.memory() или пустое значение.

    Returns:
        Словарь состояния; при любом повреждении — состояние по умолчанию.
    """
    state = new_state()
    if not raw:
        return state
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        state["refresh_token"] = str(data.get("refresh_token", ""))
        state["id_token"] = str(data.get("id_token", ""))
        state["expires_at"] = int(data.get("expires_at", 0))
        acc = data.get("acc", {})
        state["acc"] = {
            "hour": int(acc.get("hour", 0)),
            "sum_t": float(acc.get("sum_t", 0.0)),
            "sum_h": float(acc.get("sum_h", 0.0)),
            "n": int(acc.get("n", 0)),
        }
        state["time_valid"] = bool(data.get("time_valid", False))
        return state
    except (ValueError, KeyError, TypeError, AttributeError):
        return new_state()
