# -------------------------------------------------
# main.py – ESP32 DHT22 + HTTP-сервер
# -------------------------------------------------
import _thread
import json
import socket
import time

import dht
import machine
import network

from telemetry import (
    MAX_HOURLY_POINTS,
    build_http_response_bytes,
    build_telemetry_json_payload,
    parse_query_params,
    parse_request_path,
    update_hourly_history,
)

# ----- НАСТРОЙКИ ------------------------------------------------
WIFI_SSID = "fish"
WIFI_PASSWORD = "fr-1-2-3"
WIFI_CONNECT_TIMEOUT_SEC = 15
# Имя для mDNS (плата отвечает как temp-logger.local) и DHCP.
# Суффикс .local добавляет резолвер, в константе его быть не должно.
MDNS_HOSTNAME = "temp-logger"
DHT_SENSOR_PIN = machine.Pin(4)
MEASURE_INTERVAL_SEC = 5
MAX_HISTORY_POINTS = 60
HOURLY_FILE = "hourly.json"
SETTINGS_FILE = "settings.json"
DEFAULT_TZ_OFFSET = 0
TZ_MIN_HOURS = -12
TZ_MAX_HOURS = 14
HTML_CONTENT_TYPE = "text/html; charset=utf-8"
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
# ----- ЭКОНОМИЯ БАТАРЕИ (Фаза 2) --------------------------------
# BATTERY_MODE=True: CPU 80 МГц + modem sleep. Сервер и опрос каждые
# 5 сек сохраняются, страница отвечает с задержкой ~0.1-0.5 сек.
BATTERY_MODE = False
CPU_FREQ_HZ = 80000000
WIFI_PM_MODEM_SLEEP = 0xA11140

# ----- ГЛОБАЛЬНОЕ СОСТОЯНИЕ -------------------------------------
sensor = dht.DHT22(DHT_SENSOR_PIN)
telemetry_history = []  # Список словарей: [{"time": ..., "temp": ..., "hum": ...}]
hourly_history = []  # Часовые точки за сутки: [{"time": начало часа, ...}]
tz_offset_hours = DEFAULT_TZ_OFFSET
history_lock = _thread.allocate_lock()

INDEX_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>ESP32 Climate</title>
    <style>
        body{font-family:Arial;margin:20px;background:#f5f5f5}
        .card{background:#fff;padding:15px;margin:15px 0;border-radius:8px;
            box-shadow:0 2px 5px rgba(0,0,0,.1)}
        .big{font-size:2.5em;font-weight:bold}
        canvas{width:100%;height:200px;background:#fafafa;border:1px solid #ddd}
    </style>
</head>
<body>
    <h1>Климат-монитор</h1>
    <div class="card">
        <span>Температура:</span> <span id="t" class="big">--</span> °C<br>
        <span>Влажность:</span> <span id="h" class="big">--</span> %
    </div>
    <div class="card"><h3>Температура — по часам (сутки)</h3>
    <canvas id="c1"></canvas></div>
    <div class="card"><h3>Влажность — по часам (сутки)</h3>
    <canvas id="c2"></canvas></div>
    <div class="card"><h3>Часовой пояс</h3>
    <form action="/settings" method="get">
        <label>Поправка, часов:
        <input type="number" id="tz" name="tz" min="-12" max="14"
            step="1" required></label>
        <button type="submit">Сохранить на плате</button>
    </form></div>

<script>
function fmtHourLabel(ts, tz){
    if(!ts) return '';
    const d = new Date((ts + tz * 3600) * 1000);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    return hh + ':00';
}
function fitCanvas(canvas){
    const w = canvas.clientWidth || 300;
    const h = canvas.clientHeight || 200;
    if(canvas.width !== w) canvas.width = w;
    if(canvas.height !== h) canvas.height = h;
}
function decimalsForSpan(span){
    if(span >= 10) return 0;
    if(span < 0.1) return 3;
    if(span < 1) return 2;
    return 1;
}
function drawHourlyChart(canvasId, points, color, unit, fixMin, fixMax,
    clampMin, clampMax){
    const canvas = document.getElementById(canvasId);
    if(!canvas) return;
    fitCanvas(canvas);
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    const mL = 46, mB = 22, mT = 8, mR = 8;
    ctx.clearRect(0, 0, W, H);
    ctx.font = '11px Arial';
    if(points.length < 2){
        ctx.fillStyle = '#999'; ctx.font = '20px Arial';
        ctx.textAlign = 'center';
        ctx.fillText('Нет данных', W / 2, H / 2);
        return;
    }
    const values = points.map(p => p.value);
    let yMin = fixMin, yMax = fixMax;
    if(yMin === null || yMax === null){
        yMin = Math.min(...values);
        yMax = Math.max(...values);
        const span = (yMax - yMin) || 1;
        yMin -= span * 0.2;
        yMax += span * 0.2;
    }
    if(yMax === yMin) yMax = yMin + 1;
    if(clampMin !== null) yMin = Math.max(clampMin, yMin);
    if(clampMax !== null) yMax = Math.min(clampMax, yMax);
    if(yMax <= yMin) yMax = yMin + 1;
    const dec = decimalsForSpan(yMax - yMin);
    const pw = W - mL - mR, ph = H - mT - mB;
    const xPos = i => mL + (i / (points.length - 1)) * pw;
    const yPos = v => mT + ((yMax - v) / (yMax - yMin)) * ph;
    ctx.strokeStyle = '#e0e0e0';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#666';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for(let g = 0; g <= 4; g++){
        const v = yMin + ((yMax - yMin) * g) / 4;
        const y = yPos(v);
        ctx.beginPath();
        ctx.moveTo(mL, y);
        ctx.lineTo(W - mR, y);
        ctx.stroke();
        ctx.fillText(v.toFixed(dec) + unit, mL - 4, y);
    }
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const step = Math.max(1, Math.ceil(points.length / 6));
    for(let i = 0; i < points.length; i += step){
        ctx.fillText(fmtHourLabel(points[i].time, points[i].tz),
            xPos(i), H - mB + 4);
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    for(let i = 0; i < points.length; i++){
        const y = yPos(points[i].value);
        i === 0 ? ctx.moveTo(xPos(i), y) : ctx.lineTo(xPos(i), y);
    }
    ctx.stroke();
    ctx.fillStyle = color;
    for(let i = 0; i < points.length; i++){
        ctx.beginPath();
        ctx.arc(xPos(i), yPos(points[i].value), 3, 0, 2 * Math.PI);
        ctx.fill();
    }
    ctx.textBaseline = 'alphabetic';
}
async function loadData(){
    try{
        const response = await fetch('/data');
        if(!response.ok) throw new Error(response.status);
        const payload = await response.json();
        if(payload.temp !== null){
            document.getElementById('t').innerText =
                payload.temp.toFixed(1);
            document.getElementById('h').innerText =
                payload.hum.toFixed(1);
        }
        const tz = payload.tz_offset || 0;
        if(!window.tzInit){
            document.getElementById('tz').value = tz;
            window.tzInit = true;
        }
        const hourly = payload.hourly || [];
        const temps = hourly.map(p => ({time: p.time,
            value: p.temp, tz: tz}));
        const hums = hourly.map(p => ({time: p.time,
            value: p.hum, tz: tz}));
        drawHourlyChart('c1', temps, '#e74c3c', '°',
            null, null, null, null);
        drawHourlyChart('c2', hums, '#3498db', '%',
            null, null, 0, 100);
    } catch(e) { console.error('[WEB] Error fetching data', e); }
}
loadData();
setInterval(loadData, 5000);
</script>
</body>
</html>"""


# ------------------ ПРОЦЕДУРЫ СБОРА ДАННЫХ --------------------


def lock_acquire(lock) -> None:
    """Захватывает блокировку (совместимо со старыми прошивками без `with`).

    Args:
        lock: Блокировка из `_thread.allocate_lock()`.
    """
    lock.acquire()


def lock_release(lock) -> None:
    """Освобождает блокировку.

    Args:
        lock: Блокировка из `_thread.allocate_lock()`.
    """
    lock.release()


def save_hourly_history(file_path: str, hourly: list) -> bool:
    """Сохраняет часовую историю во flash-память.

    Args:
        file_path: Путь к файлу (например 'hourly.json').
        hourly: Список часовых точек.

    Returns:
        True при успешной записи, иначе False.
    """
    try:
        with open(file_path, "w") as f:
            f.write(json.dumps(hourly))
        return True
    except OSError as error:
        print(f"[DHT-ERR] Не удалось сохранить часовую историю: {error}")
        return False


def load_hourly_history(file_path: str) -> list:
    """Загружает часовую историю из flash-памяти.

    Args:
        file_path: Путь к файлу (например 'hourly.json').

    Returns:
        Список часовых точек или пустой список, если файла нет
        либо он повреждён.
    """
    try:
        with open(file_path, "r") as f:
            data = json.loads(f.read())
        if isinstance(data, list):
            print(f"[DHT] Загружена часовая история: {len(data)} точек")
            return data
        print("[DHT-ERR] Файл часовой истории повреждён, начинаю заново")
        return []
    except (OSError, ValueError):
        return []


def load_settings(file_path: str) -> int:
    """Загружает поправку часового пояса из flash-памяти.

    Args:
        file_path: Путь к файлу (например 'settings.json').

    Returns:
        Поправка в часах или DEFAULT_TZ_OFFSET, если файла нет,
        он повреждён либо значение вне диапазона.
    """
    try:
        with open(file_path, "r") as f:
            data = json.loads(f.read())
        tz_value = int(data["tz_offset"])
        if TZ_MIN_HOURS <= tz_value <= TZ_MAX_HOURS:
            print("[SRV] Часовой пояс из настроек: " + format_tz_sign(tz_value))
            return tz_value
        print("[SRV-ERR] Поправка вне диапазона, использую 0")
        return DEFAULT_TZ_OFFSET
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_TZ_OFFSET


def save_settings(file_path: str, tz_offset: int) -> bool:
    """Сохраняет поправку часового пояса во flash-память.

    Args:
        file_path: Путь к файлу (например 'settings.json').
        tz_offset: Поправка в часах целым числом.

    Returns:
        True при успешной записи корректного значения, иначе False.
    """
    if tz_offset < TZ_MIN_HOURS or tz_offset > TZ_MAX_HOURS:
        print(f"[SRV-ERR] Поправка {tz_offset} вне диапазона -12..+14")
        return False
    try:
        with open(file_path, "w") as f:
            f.write(json.dumps({"tz_offset": tz_offset}))
        return True
    except OSError as error:
        print(f"[SRV-ERR] Не удалось сохранить настройки: {error}")
        return False


def format_tz_sign(tz_offset: int) -> str:
    """Форматирует поправку со знаком ('+3', '-5', '0').

    Args:
        tz_offset: Поправка в часах.

    Returns:
        Строка со знаком для неотрицательных значений.
    """
    if tz_offset >= 0:
        return "+" + str(tz_offset)
    return str(tz_offset)


def process_settings_query(query: dict) -> tuple:
    """Обрабатывает query-параметры /settings и обновляет поправку.

    Args:
        query: Словарь query-параметров (ожидается 'tz').

    Returns:
        Кортеж (статус HTTP, тело HTML).
    """
    global tz_offset_hours
    try:
        new_tz = int(query.get("tz", ""))
    except (ValueError, TypeError):
        return (
            "400 Bad Request",
            "<h2>400 Укажите поправку: /settings?tz=3</h2>",
        )
    if new_tz < TZ_MIN_HOURS or new_tz > TZ_MAX_HOURS:
        return ("400 Bad Request", "<h2>400 Поправка должна быть от -12 до +14</h2>")
    if not save_settings(SETTINGS_FILE, new_tz):
        return ("500 Internal Server Error", "<h2>500 Не удалось сохранить</h2>")
    tz_offset_hours = new_tz
    print(f"[SRV] Часовой пояс сохранён: {format_tz_sign(new_tz)}")
    return (
        "200 OK",
        "<h2>Часовой пояс сохранён: " + format_tz_sign(new_tz) + "</h2><a href='/'>Вернуться</a>",
    )


def append_telemetry_point(history: list, point: dict, max_points: int) -> list:
    """Добавляет точку и обрезает историю до лимита.

    Args:
        history: Список точек (изменяется на месте).
        point: Новая точка вида {"time": ..., "temp": ..., "hum": ...}.
        max_points: Максимальное число хранимых точек.

    Returns:
        Та же история после добавления и обрезки.
    """
    history.append(point)
    if len(history) > max_points:
        del history[0 : len(history) - max_points]
    return history


def collect_sensor_data_loop() -> None:
    """Фоновая процедура опроса датчика DHT22."""
    while True:
        try:
            sensor.measure()
            current_temperature = sensor.temperature()
            current_humidity = sensor.humidity()
            measurement_ts = time.time()

            lock_acquire(history_lock)
            try:
                append_telemetry_point(
                    telemetry_history,
                    {
                        "time": measurement_ts,
                        "temp": current_temperature,
                        "hum": current_humidity,
                    },
                    MAX_HISTORY_POINTS,
                )
                prev_hour = hourly_history[-1]["time"] if hourly_history else None
                update_hourly_history(
                    hourly_history,
                    measurement_ts,
                    current_temperature,
                    current_humidity,
                    MAX_HOURLY_POINTS,
                )
                hour_rolled = hourly_history and hourly_history[-1]["time"] != prev_hour
            finally:
                lock_release(history_lock)

            if hour_rolled:
                save_hourly_history(HOURLY_FILE, hourly_history)

            print(f"[DHT] Прочитано: {current_temperature:.1f}°C, {current_humidity:.1f}%")
        except (OSError, ValueError) as error:
            print(f"[DHT-ERR] Ошибка чтения датчика: {error}")
        except Exception as error:
            print(f"[DHT-ERR] Неожиданная ошибка датчика: {error}")

        time.sleep(MEASURE_INTERVAL_SEC)


def build_current_telemetry_payload() -> str:
    """Сериализует текущий снимок измерений в JSON-строку.

    Returns:
        JSON-строка с полями temp, hum, history и hourly.
    """
    lock_acquire(history_lock)
    try:
        history_snapshot = list(telemetry_history)
        hourly_snapshot = list(hourly_history)
        current_tz = tz_offset_hours
    finally:
        lock_release(history_lock)
    return build_telemetry_json_payload(history_snapshot, hourly_snapshot, current_tz)


# -------------------- СЕТЕВЫЕ ПРОЦЕДУРЫ ----------------------------


def send_http_response(
    client_socket, status_code_text: str, body_content: str, content_type: str
) -> None:
    """Процедура формирования и отправки HTTP-ответа в сокет.

    Args:
        client_socket: Клиентский сокет.
        status_code_text: Статус вида '200 OK'.
        body_content: Тело ответа строкой.
        content_type: Значение заголовка Content-Type.
    """
    try:
        response_bytes = build_http_response_bytes(status_code_text, body_content, content_type)
        client_socket.send(response_bytes)
    except Exception as error:
        print(f"[HTTP-ERR] Ошибка передачи: {error}")
    finally:
        try:
            client_socket.close()
        except Exception:
            pass


def process_client_connection(client_socket) -> None:
    """Процедура обработки входящего подключения.

    Args:
        client_socket: Клиентский сокет.
    """
    try:
        # MicroPython: встроенные C-методы (bytes.decode, socket.*) не принимают
        # keyword-аргументы — только позиционные, иначе TypeError на устройстве.
        raw_request_text = client_socket.recv(1024).decode("utf-8", "ignore")
        requested_path = parse_request_path(raw_request_text)
        path_parts = requested_path.split("?", 1)
        base_path = path_parts[0]
        query = parse_query_params(path_parts[1]) if len(path_parts) > 1 else {}

        try:
            client_ip = client_socket.getpeername()[0]
        except Exception:
            client_ip = "?"
        print(f"[SRV] Запрос от {client_ip} -> path: {requested_path}")

        if base_path == "/data":
            json_payload = build_current_telemetry_payload()
            send_http_response(client_socket, "200 OK", json_payload, JSON_CONTENT_TYPE)
            print(f"[SRV] -> Отправлен JSON ({len(json_payload)} байт)")
        elif base_path == "/":
            send_http_response(client_socket, "200 OK", INDEX_HTML_TEMPLATE, HTML_CONTENT_TYPE)
            print("[SRV] -> Отправлен HTML")
        elif base_path == "/settings":
            status_text, body = process_settings_query(query)
            send_http_response(client_socket, status_text, body, HTML_CONTENT_TYPE)
            print(f"[SRV] -> Настройки: {status_text}")
        else:
            send_http_response(
                client_socket, "404 Not Found", "<h2>404 Не найдено</h2>", HTML_CONTENT_TYPE
            )
            print(f"[SRV] -> 404 для {requested_path}")

    except Exception as error:
        print(f"[SRV-ERR] Ошибка при обработке клиента: {error}")
        try:
            send_http_response(
                client_socket,
                "500 Internal Server Error",
                "<h2>500 Внутренняя ошибка</h2>",
                HTML_CONTENT_TYPE,
            )
        except Exception:
            pass


def sync_time_ntp() -> bool:
    """Синхронизирует часы ESP32 по NTP.

    Без синхронизации time.time() после перезагрузки начинается
    с нуля, и часовые метки на графиках бессмысленны.

    Returns:
        True при успешной синхронизации, иначе False.
    """
    try:
        import ntptime
    except ImportError:
        print("[NTP] Модуль ntptime недоступен, метки времени относительные")
        return False
    try:
        ntptime.settime()
        print("[NTP] Время синхронизировано")
        return True
    except OSError as error:
        print(f"[NTP-ERR] Не удалось синхронизировать время: {error}")
        return False


def start_http_server(server_ip: str) -> None:
    """Процедура инициализации и запуска слушающего сокета.

    Args:
        server_ip: IP-адрес для bind.
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((server_ip, 80))
    server_socket.listen(2)
    print(f"[SRV] Сервер запущен: http://{server_ip}")

    while True:
        try:
            client_socket, _ = server_socket.accept()
            client_socket.settimeout(2)
            process_client_connection(client_socket)
        except OSError as error:
            print(f"[SRV-LOOP-ERR] Ошибка сервера: {error}")
            time.sleep(0.1)


def apply_power_profile(wlan) -> None:
    """Включает экономный профиль при BATTERY_MODE.

    Снижает частоту CPU и переводит Wi-Fi в modem sleep.
    Сервер и опрос датчика сохраняются, страница отвечает
    с задержкой ~0.1-0.5 сек. При BATTERY_MODE=False ничего не делает.

    Args:
        wlan: Активный интерфейс network.WLAN.
    """
    if not BATTERY_MODE:
        return
    try:
        machine.freq(CPU_FREQ_HZ)
        print(f"[PWR] CPU: {CPU_FREQ_HZ // 1000000} МГц")
    except (AttributeError, OSError, ValueError) as error:
        print(f"[PWR-ERR] Не удалось снизить частоту CPU: {error}")
    try:
        # Зависит от прошивки: на старых сборках kwarg 'pm' может отсутствовать.
        wlan.config(pm=WIFI_PM_MODEM_SLEEP)
        print("[PWR] Modem sleep включён")
    except TypeError:
        print("[PWR] Прошивка без pm-режима, обычный Wi-Fi")
    except (OSError, ValueError) as error:
        print(f"[PWR-ERR] Ошибка pm-режима: {error}")


def connect_to_wifi_network(
    ssid: str, password: str, timeout_sec: int = WIFI_CONNECT_TIMEOUT_SEC
) -> str:
    """Процедура подключения к Wi-Fi с таймаутом.

    Args:
        ssid: Имя Wi-Fi сети.
        password: Пароль сети.
        timeout_sec: Максимальное время ожидания подключения.

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
    wlan = network.WLAN(network.STA_IF)
    try:
        # Должно вызываться ДО active()/connect, иначе имя не применится.
        network.hostname(MDNS_HOSTNAME)
        print(f"[WiFi] Hostname: {MDNS_HOSTNAME} (доступ: {MDNS_HOSTNAME}.local)")
    except AttributeError:
        print("[WiFi] Прошивка без network.hostname, доступ только по IP")
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
    apply_power_profile(wlan)
    return assigned_ip


# ----------------------- ТОЧКА ВХОДА ---------------------------
def main() -> None:
    """Точка входа: подключает Wi-Fi и запускает сбор данных и сервер."""
    global tz_offset_hours
    tz_offset_hours = load_settings(SETTINGS_FILE)
    current_ip = connect_to_wifi_network(WIFI_SSID, WIFI_PASSWORD)
    sync_time_ntp()
    hourly_history[:] = load_hourly_history(HOURLY_FILE)

    # Запуск фоновой процедуры
    _thread.start_new_thread(collect_sensor_data_loop, ())

    # Запуск основного сетевого цикла
    start_http_server(current_ip)


if __name__ == "__main__":
    main()
