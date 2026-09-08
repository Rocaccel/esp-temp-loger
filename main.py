# pyright: reportAttributeAccessIssue=false
# secrets.py генерируется под плату (динамический модуль), типы неизвестны статике.
# -------------------------------------------------
# main.py – ESP32 DHT22 -> Firebase, deep sleep
# -------------------------------------------------
import time

import dht
import machine
import network

try:
    import urequests
except ImportError:
    urequests = None

import secrets

from fbsync import (
    acc_add,
    acc_mean,
    build_bucket_payload,
    build_current_payload,
    decode_rtc_state,
    encode_rtc_state,
    ensure_id_token,
    new_acc,
    new_state,
    node_url,
    prune_key,
)
from telemetry import hour_start_ts, to_unix_ts

sensor = dht.DHT22(machine.Pin(4))
WIFI_CONNECT_TIMEOUT_SEC = 15


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


def wifi_connect(ssid: str, password: str, timeout_sec: int = WIFI_CONNECT_TIMEOUT_SEC) -> str:
    """Подключается к Wi-Fi с таймаутом.

    Args:
        ssid: Имя Wi-Fi сети.
        password: Пароль сети.
        timeout_sec: Максимальное время ожидания.

    Returns:
        Назначенный IP-адрес.

    Raises:
        OSError: Если подключиться не удалось за timeout_sec.
        ValueError: Если ssid пустой или timeout_sec меньше 1.
    """
    if not ssid:
        raise ValueError("SSID не должен быть пустым")
    if timeout_sec < 1:
        raise ValueError("timeout_sec должен быть >= 1")
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
    assigned_ip = wlan.ifconfig()[0]
    print(f"[WiFi] Подключено. IP: {assigned_ip}")
    return assigned_ip


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


def _post(url: str, payload: dict) -> dict:
    """POST JSON с проверкой статуса.

    Args:
        url: Полный URL.
        payload: Тело запроса.

    Returns:
        Распарсенный JSON-ответ.

    Raises:
        OSError: При сетевой ошибке или статусе >= 400.
    """
    assert urequests is not None
    resp = urequests.post(url, json=payload)
    try:
        code = resp.status_code
        data = resp.json() if code < 400 else {}
        if code >= 400:
            raise OSError(f"HTTP {code}")
        return data
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _put(url: str, payload: dict) -> dict:
    """PUT JSON с проверкой статуса.

    Args:
        url: Полный URL.
        payload: Тело запроса.

    Returns:
        Распарсенный JSON-ответ.

    Raises:
        OSError: При сетевой ошибке или статусе >= 400.
    """
    assert urequests is not None
    resp = urequests.put(url, json=payload)
    try:
        code = resp.status_code
        data = resp.json() if code < 400 else {}
        if code >= 400:
            raise OSError(f"HTTP {code}")
        return data
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _delete(url: str) -> None:
    """DELETE с проверкой статуса.

    Args:
        url: Полный URL.

    Raises:
        OSError: При сетевой ошибке или статусе >= 400.
    """
    assert urequests is not None
    resp = urequests.delete(url)
    try:
        if resp.status_code >= 400:
            raise OSError(f"HTTP {resp.status_code}")
    finally:
        try:
            resp.close()
        except Exception:
            pass


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
    if urequests is None:
        print("[SYS-ERR] Нет модуля urequests (нужен mip install urequests)")
        sleep_now()
        return

    deep_wake = is_deep_wake()
    if deep_wake:
        print("[SYS] Пробуждение из deep sleep")
    else:
        print("[SYS] Холодный старт")
    state = load_state()

    try:
        sensor.measure()
        temp = sensor.temperature()
        hum = sensor.humidity()
        print(f"[DHT] Прочитано: {temp:.1f}°C, {hum:.1f}%")
    except (OSError, ValueError) as error:
        print(f"[DHT-ERR] Ошибка чтения датчика: {error}")
        sleep_now()
        return

    try:
        wifi_connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
    except (OSError, ValueError) as error:
        print(f"[WiFi-ERR] {error}")
        sleep_now()
        return

    if not deep_wake or not state["time_valid"]:
        if ntp_sync():
            state["time_valid"] = True
    if not state["time_valid"]:
        print("[SYS-ERR] Нет точного времени, отправка пропущена")
        save_state(state)
        sleep_now()
        return

    now_unix = to_unix_ts(time.time())
    try:
        token = ensure_id_token(_post, secrets.FIREBASE_API_KEY, state, now_unix)
    except (OSError, ValueError) as error:
        print(f"[FB-ERR] Auth: {error}")
        save_state(state)
        sleep_now()
        return

    hour = hour_start_ts(now_unix)
    acc = state["acc"]
    try:
        if acc["n"] > 0 and acc["hour"] != hour:
            _delete(db_node("hourly/" + str(prune_key(hour)), token))
            acc = new_acc(hour)
            state["acc"] = acc
        acc_add(acc, temp, hum)
        avg_temp, avg_hum, count = acc_mean(acc)
        _put(db_node("current", token), build_current_payload(temp, hum, now_unix))
        _put(db_node("hourly/" + str(hour), token), build_bucket_payload(avg_temp, avg_hum, count))
        print(f"[FB] Отправлено: {temp:.1f}°C, {hum:.1f}% (час n={count})")
    except OSError as error:
        print(f"[FB-ERR] Отправка: {error}")

    save_state(state)
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
