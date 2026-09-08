"""Тесты main.py через стабы MicroPython (network, machine, dht, _thread)."""

import json
import sys

import pytest

import main
from telemetry import trim_history


class FakeSocket:
    """Фейковый клиентский сокет для проверки ответов сервера."""

    def __init__(self, request_text: str) -> None:
        """Сохраняет текст запроса и готовит буфер отправки.

        Args:
            request_text: Сырой HTTP-запрос от «клиента».
        """
        self._request = request_text.encode()
        self.sent = b""
        self.closed = False

    def recv(self, size: int) -> bytes:
        """Возвращает заранее заданный запрос.

        Args:
            size: Размер чтения (игнорируется).

        Returns:
            Байты запроса.
        """
        return self._request

    def send(self, data: bytes) -> int:
        """Копит отправленные байты.

        Args:
            data: Часть ответа.

        Returns:
            Число принятых байтов.
        """
        self.sent += data
        return len(data)

    def close(self) -> None:
        """Помечает сокет закрытым."""
        self.closed = True

    def getpeername(self) -> tuple:
        """Возвращает фиктивный адрес клиента.

        Returns:
            Кортеж (ip, port).
        """
        return ("127.0.0.1", 1234)


def _response_parts(sock: FakeSocket) -> tuple:
    """Разбирает отправленный ответ на заголовок и тело.

    Args:
        sock: Фейковый сокет после обработки.

    Returns:
        Кортеж (заголовок строкой, тело байтами).
    """
    header_bytes, _, body = sock.sent.partition(b"\r\n\r\n")
    return header_bytes.decode("ascii"), body


def test_data_route_returns_json() -> None:
    """Маршрут /data отдаёт JSON с историей и часовыми точками."""
    main.telemetry_history.clear()
    main.hourly_history.clear()
    main.telemetry_history.append({"time": 1, "temp": 21.5, "hum": 52.0})
    main.hourly_history.append({"time": 0, "temp": 21.0, "hum": 51.0, "count": 1})
    sock = FakeSocket("GET /data HTTP/1.1\r\nHost: x\r\n\r\n")
    main.process_client_connection(sock)
    header, body = _response_parts(sock)
    assert "200 OK" in header
    assert "application/json" in header
    payload = json.loads(body.decode("utf-8"))
    assert payload["temp"] == 21.5
    assert payload["hum"] == 52.0
    assert payload["hourly"] == [{"time": 946684800, "temp": 21.0, "hum": 51.0, "count": 1}]
    assert sock.closed


def test_root_route_returns_html_with_charset() -> None:
    """Маршрут / отдаёт HTML с указанием кодировки."""
    sock = FakeSocket("GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    main.process_client_connection(sock)
    header, body = _response_parts(sock)
    assert "200 OK" in header
    assert "text/html; charset=utf-8" in header
    assert "Климат-монитор".encode("utf-8") in body
    assert sock.closed


def test_unknown_route_returns_404() -> None:
    """Неизвестный путь (напр. /favicon.ico) даёт 404, а не HTML."""
    sock = FakeSocket("GET /favicon.ico HTTP/1.1\r\nHost: x\r\n\r\n")
    main.process_client_connection(sock)
    header, _ = _response_parts(sock)
    assert "404 Not Found" in header
    assert sock.closed


def test_content_length_matches_utf8_bytes() -> None:
    """Content-Length ответа равен длине UTF-8 байтов тела."""
    sock = FakeSocket("GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    main.process_client_connection(sock)
    header, body = _response_parts(sock)
    assert f"Content-Length: {len(body)}" in header


def test_wifi_empty_ssid_rejected() -> None:
    """Пустой SSID отклоняется сразу."""
    with pytest.raises(ValueError, match="SSID"):
        main.connect_to_wifi_network("", "pass", timeout_sec=1)


def test_wifi_bad_timeout_rejected() -> None:
    """Некорректный таймаут отклоняется."""
    with pytest.raises(ValueError, match="timeout_sec"):
        main.connect_to_wifi_network("ssid", "pass", timeout_sec=0)


def test_wifi_connect_success_via_stub() -> None:
    """Подключение через стаб network возвращает IP стаба."""
    assert main.connect_to_wifi_network("ssid", "pass", timeout_sec=2) == "192.168.1.100"


def test_mdns_hostname_set_before_active() -> None:
    """Hostname задаётся до поднятия интерфейса (иначе mDNS его не подхватит)."""
    import network

    network.events.clear()
    main.connect_to_wifi_network("ssid", "pass", timeout_sec=2)
    assert network.events.index("hostname") < network.events.index("active")
    assert network.hostname() == "temp-logger"


def test_mdns_hostname_value_valid() -> None:
    """Имя совместимо с mDNS: без точек, пробелов и в лимите 32 символов."""
    assert "." not in main.MDNS_HOSTNAME
    assert " " not in main.MDNS_HOSTNAME
    assert 1 <= len(main.MDNS_HOSTNAME) <= 32


def test_wifi_without_hostname_support(monkeypatch) -> None:
    """На старой прошивке без network.hostname подключение всё равно работает."""
    import network

    def _no_hostname(name: str) -> str:
        raise AttributeError("no hostname")

    monkeypatch.setattr(network, "hostname", _no_hostname)
    assert main.connect_to_wifi_network("ssid", "pass", timeout_sec=2) == "192.168.1.100"


def test_append_telemetry_point_trims() -> None:
    """Добавление точки обрезает историю до лимита."""
    history: list = [{"i": i} for i in range(5)]
    main.append_telemetry_point(history, {"i": 5}, 3)
    assert history == [{"i": 3}, {"i": 4}, {"i": 5}]
    assert trim_history(history, 60) == history


def test_hourly_history_save_load_roundtrip(tmp_path) -> None:
    """Часовая история переживает запись/чтение файла."""
    file_path = str(tmp_path / "hourly.json")
    hourly = [
        {"time": 3600, "temp": 21.5, "hum": 52.0, "count": 12},
        {"time": 7200, "temp": 22.0, "hum": 53.0, "count": 12},
    ]
    assert main.save_hourly_history(file_path, hourly) is True
    assert main.load_hourly_history(file_path) == hourly


def test_hourly_history_load_missing_file(tmp_path) -> None:
    """Отсутствующий файл даёт пустую историю без ошибок."""
    assert main.load_hourly_history(str(tmp_path / "nope.json")) == []


def test_hourly_history_load_corrupted_file(tmp_path) -> None:
    """Повреждённый файл даёт пустую историю без ошибок."""
    file_path = tmp_path / "hourly.json"
    file_path.write_text("не json", encoding="utf-8")
    assert main.load_hourly_history(str(file_path)) == []


def test_sync_time_ntp_success_via_stub() -> None:
    """Со стабом ntptime синхронизация считается успешной."""
    assert main.sync_time_ntp() is True


def test_sync_time_ntp_no_module(monkeypatch) -> None:
    """Без модуля ntptime синхронизация возвращает False."""
    monkeypatch.setitem(sys.modules, "ntptime", None)
    assert main.sync_time_ntp() is False


def test_sync_time_ntp_network_error(monkeypatch) -> None:
    """Ошибка сети при синхронизации возвращает False."""
    import types

    fake = types.ModuleType("ntptime")

    def _fail() -> None:
        raise OSError("no network")

    monkeypatch.setattr(fake, "settime", _fail, raising=False)
    monkeypatch.setitem(sys.modules, "ntptime", fake)
    assert main.sync_time_ntp() is False


def test_settings_save_load_roundtrip(tmp_path) -> None:
    """Поправка переживает запись/чтение файла."""
    file_path = str(tmp_path / "settings.json")
    assert main.save_settings(file_path, 5) is True
    assert main.load_settings(file_path) == 5


def test_settings_save_rejects_out_of_range(tmp_path) -> None:
    """Поправка вне -12..+14 не сохраняется."""
    file_path = str(tmp_path / "settings.json")
    assert main.save_settings(file_path, 99) is False
    assert main.save_settings(file_path, -13) is False


def test_settings_load_missing_file(tmp_path) -> None:
    """Отсутствующий файл даёт поправку по умолчанию."""
    assert main.load_settings(str(tmp_path / "nope.json")) == 0


def test_settings_load_corrupted_file(tmp_path) -> None:
    """Повреждённый файл даёт поправку по умолчанию."""
    file_path = tmp_path / "settings.json"
    file_path.write_text("не json", encoding="utf-8")
    assert main.load_settings(str(file_path)) == 0


def test_settings_load_out_of_range_value(tmp_path) -> None:
    """Значение вне диапазона в файле игнорируется."""
    file_path = tmp_path / "settings.json"
    file_path.write_text('{"tz_offset": 50}', encoding="utf-8")
    assert main.load_settings(str(file_path)) == 0


def test_format_tz_sign() -> None:
    """Поправка форматируется со знаком."""
    assert main.format_tz_sign(3) == "+3"
    assert main.format_tz_sign(-5) == "-5"
    assert main.format_tz_sign(0) == "+0"


def test_settings_route_saves_tz(monkeypatch, tmp_path) -> None:
    """GET /settings?tz=5 сохраняет поправку и отвечает 200."""
    monkeypatch.setattr(main, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(main, "tz_offset_hours", 0)
    sock = FakeSocket("GET /settings?tz=5 HTTP/1.1\r\nHost: x\r\n\r\n")
    main.process_client_connection(sock)
    header, body = _response_parts(sock)
    assert "200 OK" in header
    assert "+5" in body.decode("utf-8")
    assert main.tz_offset_hours == 5
    assert main.load_settings(str(tmp_path / "settings.json")) == 5


def test_settings_route_rejects_bad_tz(monkeypatch, tmp_path) -> None:
    """Некорректная поправка даёт 400 и не меняет текущую."""
    monkeypatch.setattr(main, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(main, "tz_offset_hours", 3)
    for target in (
        "GET /settings?tz=99 HTTP/1.1",
        "GET /settings?tz=abc HTTP/1.1",
        "GET /settings HTTP/1.1",
    ):
        sock = FakeSocket(target + "\r\nHost: x\r\n\r\n")
        main.process_client_connection(sock)
        header, _ = _response_parts(sock)
        assert "400 Bad Request" in header
    assert main.tz_offset_hours == 3


def test_data_route_includes_tz_offset(monkeypatch) -> None:
    """Маршрут /data отдаёт поправку часового пояса."""
    monkeypatch.setattr(main, "tz_offset_hours", 7)
    main.telemetry_history.clear()
    main.hourly_history.clear()
    sock = FakeSocket("GET /data HTTP/1.1\r\nHost: x\r\n\r\n")
    main.process_client_connection(sock)
    _, body = _response_parts(sock)
    assert json.loads(body.decode("utf-8"))["tz_offset"] == 7


def test_power_profile_disabled_by_default() -> None:
    """Без BATTERY_MODE профиль ничего не трогает."""
    import machine
    import network

    machine.applied_freq = None
    wlan = network.WLAN(network.STA_IF)
    main.apply_power_profile(wlan)
    assert machine.applied_freq is None
    assert wlan.pm_mode is None


def test_power_profile_battery_mode(monkeypatch) -> None:
    """В BATTERY_MODE снижаются частота CPU и включается modem sleep."""
    import machine
    import network

    monkeypatch.setattr(main, "BATTERY_MODE", True)
    machine.applied_freq = None
    wlan = network.WLAN(network.STA_IF)
    main.apply_power_profile(wlan)
    assert machine.applied_freq == 80000000
    assert wlan.pm_mode == 0xA11140
