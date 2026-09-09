# pyright: reportAttributeAccessIssue=false
# secrets.py генерируется под плату (динамический модуль), типы неизвестны статике.
# -------------------------------------------------
# main.py – ESP32 DHT22 -> Firebase, deep sleep
# -------------------------------------------------
import secrets
import time

import dht
import machine
import network

import https
from fbsync import (
    acc_add,
    acc_mean,
    auth_error_hint,
    build_bucket_payload,
    build_current_payload,
    build_health_payload,
    decode_rtc_state,
    describe_http_error,
    encode_rtc_state,
    ensure_id_token,
    extract_error_message,
    new_acc,
    new_state,
    node_url,
    prune_key,
)
from telemetry import hour_start_ts, to_unix_ts

DHT_PIN = 4
sensor = dht.DHT22(machine.Pin(DHT_PIN))
WIFI_CONNECT_TIMEOUT_SEC = 15
DHT_FAILS_RECREATE = 3
HTTPS_TIMEOUT_SEC = 20


def now_ms() -> int:
    """Миллисекунды монотонных часов (с fallback для CPython).

    Returns:
        Метка времени в мс.
    """
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def diff_ms(start: int) -> int:
    """Разница меток в мс с учётом переполнения счётчика.

    Args:
        start: Начальная метка из now_ms().

    Returns:
        Прошедшие миллисекунды.
    """
    now = now_ms()
    try:
        return time.ticks_diff(now, start)
    except AttributeError:
        return now - start


def free_memory() -> int:
    """Свободная RAM в байтах.

    Returns:
        gc.mem_free() или -1 где счётчика нет.
    """
    try:
        import gc

        return gc.mem_free()
    except (ImportError, AttributeError):
        return -1


def reset_cause_code() -> int:
    """Код причины перезагрузки.

    Returns:
        machine.reset_cause() или -1 без поддержки.
    """
    try:
        return machine.reset_cause()
    except AttributeError:
        return -1


def is_deep_wake() -> bool:
    """Определяет пробуждение из deep sleep.

    Returns:
        True если это пробуждение по таймеру, False при холодном
        старте или на прошивке без нужных констант.
    """
    try:
        return machine.reset_cause() == machine.DEEPSLEEP_RESET
    except AttributeError:
        return False


def load_state() -> dict:
    """Загружает состояние цикла из RTC-памяти.

    Returns:
        Словарь состояния (по умолчанию при первом старте).
    """
    try:
        return decode_rtc_state(machine.RTC().memory())
    except (OSError, ValueError, AttributeError):
        return new_state()


def save_state(state: dict) -> None:
    """Сохраняет состояние цикла в RTC-память.

    Args:
        state: Словарь состояния цикла.
    """
    try:
        machine.RTC().memory(encode_rtc_state(state).encode("utf-8"))
    except (OSError, ValueError, AttributeError) as error:
        print(f"[SYS-ERR] Не удалось сохранить RTC: {error}")


def sleep_now() -> None:
    """Уходит в deep sleep на период из настроек."""
    print(f"[SYS] Сон {secrets.SLEEP_SEC} сек")
    machine.deepsleep(secrets.SLEEP_SEC * 1000)


def start_watchdog():
    """Запускает сторожевой таймер (опционально).

    В deep sleep цифровая периферия обесточена, сторож там стоит.
    Таймаут должен превышать сон + цикл, иначе ребуты вместо сна
    (данные при этом всё равно идут, видно по reset_cause в health).

    Returns:
        Объект WDT или None если выключен/не поддерживается.
    """
    if not secrets.WDT_ENABLED:
        return None
    try:
        wdt = machine.WDT(timeout=secrets.WDT_TIMEOUT_MS)
    except AttributeError:
        print("[WDT] Нет machine.WDT на прошивке")
        return None
    print(f"[WDT] Сторож: {secrets.WDT_TIMEOUT_MS} мс")
    return wdt


def feed_wdt(wdt) -> None:
    """Сбрасывает сторожевой таймер.

    Args:
        wdt: Объект WDT или None.
    """
    if wdt is None:
        return
    try:
        wdt.feed()
    except OSError as error:
        print(f"[WDT-ERR] {error}")


def wifi_connect(
    ssid: str, password: str, timeout_sec: int = WIFI_CONNECT_TIMEOUT_SEC, static=()
) -> tuple:
    """Подключается к Wi-Fi с таймаутом.

    Args:
        ssid: Имя Wi-Fi сети.
        password: Пароль сети.
        timeout_sec: Максимальное время ожидания.
        static: Кортеж (ip, mask, gw, dns) или пустой для DHCP.

    Returns:
        Кортеж (IP-адрес, RSSI или None).

    Raises:
        OSError: Если подключиться не удалось за timeout_sec.
        ValueError: Если ssid пустой, timeout_sec меньше 1
            или static не из 4 элементов.
    """
    if not ssid:
        raise ValueError("SSID не должен быть пустым")
    if timeout_sec < 1:
        raise ValueError("timeout_sec должен быть >= 1")
    if static and len(static) != 4:
        raise ValueError("static должен быть (ip, mask, gw, dns)")
    try:
        # Должно вызываться ДО active()/connect, иначе имя не применится.
        network.hostname(secrets.MDNS_HOSTNAME)
        print(f"[WiFi] Hostname: {secrets.MDNS_HOSTNAME}")
    except AttributeError:
        print("[WiFi] Прошивка без network.hostname, доступ только по IP")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        waited = 0.0
        step = 0.5
        while not wlan.isconnected():
            if waited >= timeout_sec:
                raise OSError(f"Не удалось подключиться к Wi-Fi '{ssid}' за {timeout_sec} c")
            time.sleep(step)
            waited += step
    if static:
        wlan.ifconfig(static)
    assigned_ip = wlan.ifconfig()[0]
    try:
        rssi = wlan.status("rssi")
    except (OSError, ValueError, AttributeError):
        rssi = None
    print(f"[WiFi] Подключено. IP: {assigned_ip}")
    return (assigned_ip, rssi)


def ntp_sync() -> bool:
    """Синхронизирует часы ESP32 по NTP.

    Returns:
        True при успешной синхронизации, иначе False.
    """
    try:
        import ntptime
    except ImportError:
        print("[NTP] Модуль ntptime недоступен")
        return False
    try:
        ntptime.settime()
        print("[NTP] Время синхронизировано")
        return True
    except OSError as error:
        print(f"[NTP-ERR] Не удалось синхронизировать время: {error}")
        return False


def read_sensor(dht_fails: int):
    """Читает датчик с реанимацией после серии ошибок.

    Args:
        dht_fails: Число ошибок подряд из RTC.

    Returns:
        Кортеж (температура, влажность, новая серия ошибок).
        При отказе значения None.
    """
    global sensor
    try:
        sensor.measure()
        return (sensor.temperature(), sensor.humidity(), 0)
    except (OSError, ValueError) as error:
        print(f"[DHT-ERR] Ошибка чтения датчика: {error}")
    if dht_fails + 1 >= DHT_FAILS_RECREATE:
        print("[DHT] Пересоздаю объект датчика")
        try:
            sensor = dht.DHT22(machine.Pin(DHT_PIN))
            time.sleep(2)
            sensor.measure()
            return (sensor.temperature(), sensor.humidity(), 0)
        except (OSError, ValueError) as error:
            print(f"[DHT-ERR] Повторно: {error}")
    return (None, None, dht_fails + 1)


def _post(session, url: str, payload: dict) -> dict:
    """POST JSON с проверкой статуса.

    Args:
        session: https.Session цикла.
        url: Полный URL.
        payload: Тело запроса.

    Returns:
        Распарсенный JSON-ответ.

    Raises:
        OSError: При сетевой ошибке, таймауте или статусе >= 400.
    """
    status, data = session.request("POST", url, payload)
    if status >= 400:
        raise OSError(describe_http_error(status, extract_error_message(data)))
    return data


def _put(session, url: str, payload: dict) -> dict:
    """PUT JSON с проверкой статуса.

    Args:
        session: https.Session цикла.
        url: Полный URL.
        payload: Тело запроса.

    Returns:
        Распарсенный JSON-ответ.

    Raises:
        OSError: При сетевой ошибке, таймауте или статусе >= 400.
    """
    status, data = session.request("PUT", url, payload)
    if status >= 400:
        raise OSError(describe_http_error(status, extract_error_message(data)))
    return data


def _delete(session, url: str) -> None:
    """DELETE с проверкой статуса.

    Args:
        session: https.Session цикла.
        url: Полный URL.

    Raises:
        OSError: При сетевой ошибке, таймауте или статусе >= 400.
    """
    status, data = session.request("DELETE", url, None)
    if status >= 400:
        raise OSError(describe_http_error(status, extract_error_message(data)))


def db_node(node: str, token: str) -> str:
    """URL узла устройства в базе.

    Args:
        node: Путь узла (например 'current').
        token: Действующий id_token.

    Returns:
        Полный REST URL узла.
    """
    return node_url(secrets.FIREBASE_DB_URL, secrets.DEVICE_ID, node, token)


def run_cycle() -> None:
    """Один цикл: замер -> отправка в Firebase -> сон."""
    wdt = start_watchdog()
    feed_wdt(wdt)

    deep_wake = is_deep_wake()
    if deep_wake:
        print("[SYS] Пробуждение из deep sleep")
    else:
        print("[SYS] Холодный старт")
    print(f"[TLS] Модуль: {https.tls_name()}")
    state = load_state()
    state["wake_id"] = state.get("wake_id", 0) + 1
    session = https.Session(timeout=HTTPS_TIMEOUT_SEC)
    err = ""
    rssi = None
    wifi_ms = 0
    net_ms = 0

    temp, hum, dht_fails = read_sensor(state.get("dht_fails", 0))
    state["dht_fails"] = dht_fails
    if temp is None or hum is None:
        err = "dht"
        save_state(state)
        session.close_all()
        sleep_now()
        return
    feed_wdt(wdt)

    wifi_start = now_ms()
    try:
        _, rssi = wifi_connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD, static=secrets.NET_STATIC)
    except (OSError, ValueError) as error:
        print(f"[WiFi-ERR] {error}")
        err = "wifi"
        save_state(state)
        session.close_all()
        sleep_now()
        return
    wifi_ms = diff_ms(wifi_start)
    feed_wdt(wdt)

    if not deep_wake or not state["time_valid"]:
        if ntp_sync():
            state["time_valid"] = True
    if not state["time_valid"]:
        print("[SYS-ERR] Нет точного времени, отправка пропущена")
        err = "ntp"
        save_state(state)
        session.close_all()
        sleep_now()
        return

    net_start = now_ms()
    now_unix = to_unix_ts(time.time())
    try:
        token = ensure_id_token(
            lambda url, payload: _post(session, url, payload),
            secrets.FIREBASE_API_KEY,
            state,
            now_unix,
        )
    except (OSError, ValueError) as error:
        hint = auth_error_hint(str(error))
        if hint:
            print(f"[FB-ERR] Auth: {error} ({hint})")
        else:
            print(f"[FB-ERR] Auth: {error}")
        # Битый refresh-токен иначе крутился бы вечно — сброс к signup.
        state["id_token"] = ""
        state["refresh_token"] = ""
        state["expires_at"] = 0
        err = "auth"
        save_state(state)
        session.close_all()
        sleep_now()
        return
    feed_wdt(wdt)

    hour = hour_start_ts(now_unix)
    acc = state["acc"]
    try:
        if acc["n"] > 0 and acc["hour"] != hour:
            _delete(session, db_node("hourly/" + str(prune_key(hour)), token))
            acc = new_acc(hour)
            state["acc"] = acc
        feed_wdt(wdt)
        acc_add(acc, temp, hum)
        avg_temp, avg_hum, count = acc_mean(acc)
        _put(session, db_node("current", token), build_current_payload(temp, hum, now_unix))
        feed_wdt(wdt)
        _put(
            session,
            db_node("hourly/" + str(hour), token),
            build_bucket_payload(avg_temp, avg_hum, count),
        )
        print(f"[FB] Отправлено: {temp:.1f}°C, {hum:.1f}% (час n={count})")
    except OSError as error:
        print(f"[FB-ERR] Отправка: {error}")
        err = "send"
    feed_wdt(wdt)

    net_ms = diff_ms(net_start)
    try:
        _put(
            session,
            db_node("health", token),
            build_health_payload(
                state["wake_id"],
                reset_cause_code(),
                wifi_ms,
                dht_fails,
                free_memory(),
                rssi,
                err,
                now_unix,
                net_ms,
            ),
        )
    except OSError as error:
        print(f"[FB-ERR] Health: {error}")

    save_state(state)
    session.close_all()
    sleep_now()


def main() -> None:
    """Точка входа: один цикл с гарантированным уходом в сон."""
    try:
        run_cycle()
    except Exception as error:
        print(f"[SYS-ERR] Неожиданная ошибка: {error}")
        try:
            sleep_now()
        except Exception:
            pass


if __name__ == "__main__":
    main()
