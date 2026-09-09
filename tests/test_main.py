"""Тесты main.py: Wi-Fi, NTP, mDNS, WDT и deep-sleep цикл через стабы."""

import sys

import pytest

import fbsync
import https
import main
from telemetry import hour_start_ts, to_unix_ts


class FakeSession:
    """Подмена https.Session со сценарием ответов."""

    def __init__(self, *args, **kwargs) -> None:
        """Пустые вызовы и сценарий."""
        self.calls: list = []
        self.routes: dict = {}
        self.closed = False
        self.fail_with = None

    def route(self, method: str, part: str, status: int, data: dict) -> None:
        """Добавляет ответ в сценарий.

        Args:
            method: Метод HTTP.
            part: Подстрока URL.
            status: Статус ответа.
            data: Тело ответа.
        """
        self.routes.setdefault((method, part), []).append((status, data))

    def request(self, method: str, url: str, payload=None):
        """Записывает вызов, отвечает по сценарию или 200 {}."""
        self.calls.append({"method": method, "url": url, "payload": payload})
        if self.fail_with is not None:
            raise self.fail_with
        for (route_method, part), queue in self.routes.items():
            if route_method == method and part in url and queue:
                return queue.pop(0)
        return (200, {})

    def close_all(self) -> None:
        """Помечает сессию закрытой."""
        self.closed = True


@pytest.fixture
def fake_net(monkeypatch):
    """Подменяет транспорт https.Session фейком."""
    net = FakeSession()
    monkeypatch.setattr(https, "Session", lambda *args, **kwargs: net)
    return net


@pytest.fixture(autouse=True)
def _reset_hw_stubs():
    """Сбрасывает состояние железных стабов перед каждым тестом."""
    import machine

    machine.RTC._memory = b""
    machine.sleep_calls.clear()
    machine._reset_cause = machine.PWRON_RESET
    machine.WDT.instances.clear()
    import dht

    dht.made.clear()
    yield


def _signup_route(net: FakeSession) -> None:
    """Настраивает успешный анонимный signup."""
    net.route(
        "POST",
        "identitytoolkit",
        200,
        {"idToken": "id1", "refreshToken": "rf1", "expiresIn": "3600"},
    )


def _fresh_rtc() -> None:
    """Кладёт в RTC валидное состояние с далёким токеном."""
    import machine

    state = fbsync.new_state()
    state["refresh_token"] = "rf0"
    state["id_token"] = "id0"
    state["expires_at"] = 9000000000
    state["time_valid"] = True
    machine.RTC._memory = fbsync.encode_rtc_state(state).encode("utf-8")


def _ntp_spy(monkeypatch) -> dict:
    """Подменяет ntptime.settime счётчиком вызовов."""
    import ntptime

    calls = {"count": 0}

    def _fake_settime() -> None:
        calls["count"] += 1

    monkeypatch.setattr(ntptime, "settime", _fake_settime)
    return calls


def _puts(net: FakeSession) -> list:
    """Выбирает PUT-вызовы из записанных."""
    return [c for c in net.calls if c["method"] == "PUT"]


def test_wifi_empty_ssid_rejected() -> None:
    """Пустой SSID отклоняется сразу."""
    with pytest.raises(ValueError, match="SSID"):
        main.wifi_connect("", "pass", timeout_sec=1)


def test_wifi_bad_timeout_rejected() -> None:
    """Некорректный таймаут отклоняется."""
    with pytest.raises(ValueError, match="timeout_sec"):
        main.wifi_connect("ssid", "pass", timeout_sec=0)


def test_wifi_bad_static_rejected() -> None:
    """Битый static конфиг отклоняется."""
    with pytest.raises(ValueError, match="static"):
        main.wifi_connect("ssid", "pass", timeout_sec=1, static=("1.2.3.4",))


def test_wifi_connect_success_via_stub() -> None:
    """Подключение возвращает IP стаба и RSSI."""
    ip, rssi = main.wifi_connect("ssid", "pass", timeout_sec=2)
    assert ip == "192.168.1.100"
    assert rssi == -55


def test_wifi_connect_static_ip() -> None:
    """Статический IP применяется вместо DHCP."""
    static = ("192.168.1.50", "255.255.255.0", "192.168.1.1", "8.8.8.8")
    ip, _ = main.wifi_connect("ssid", "pass", timeout_sec=2, static=static)
    assert ip == "192.168.1.50"


def test_mdns_hostname_set_before_active() -> None:
    """Hostname задаётся до поднятия интерфейса."""
    import network

    network.events.clear()
    main.wifi_connect("ssid", "pass", timeout_sec=2)
    assert network.events.index("hostname") < network.events.index("active")
    assert network.hostname() == "temp-logger"


def test_mdns_hostname_value_valid() -> None:
    """Имя из secrets совместимо с mDNS."""
    from tests.stubs import secrets as stub_secrets

    assert "." not in stub_secrets.MDNS_HOSTNAME
    assert " " not in stub_secrets.MDNS_HOSTNAME
    assert 1 <= len(stub_secrets.MDNS_HOSTNAME) <= 32


def test_wifi_without_hostname_support(monkeypatch) -> None:
    """На старой прошивке подключение работает без hostname."""
    import network

    def _no_hostname(name: str) -> str:
        raise AttributeError("no hostname")

    monkeypatch.setattr(network, "hostname", _no_hostname)
    assert main.wifi_connect("ssid", "pass", timeout_sec=2)[0] == "192.168.1.100"


def test_sync_time_ntp_success_via_stub() -> None:
    """Со стабом ntptime синхронизация успешна."""
    assert main.ntp_sync() is True


def test_sync_time_ntp_no_module(monkeypatch) -> None:
    """Без модуля ntptime возвращается False."""
    monkeypatch.setitem(sys.modules, "ntptime", None)
    assert main.ntp_sync() is False


def test_sync_time_ntp_network_error(monkeypatch) -> None:
    """Ошибка сети возвращает False."""
    import types

    fake = types.ModuleType("ntptime")

    def _fail() -> None:
        raise OSError("no network")

    monkeypatch.setattr(fake, "settime", _fail, raising=False)
    monkeypatch.setitem(sys.modules, "ntptime", fake)
    assert main.ntp_sync() is False


def test_is_deep_wake_true() -> None:
    """Флаг deep-пробуждения распознаётся."""
    import machine

    machine._reset_cause = machine.DEEPSLEEP_RESET
    assert main.is_deep_wake() is True


def test_is_deep_wake_fallback(monkeypatch) -> None:
    """Без константы прошивки считается холодным стартом."""
    import machine

    monkeypatch.delattr(machine, "DEEPSLEEP_RESET")
    assert main.is_deep_wake() is False


def test_free_memory_no_counter() -> None:
    """Без gc.mem_free возвращается -1."""
    assert main.free_memory() == -1


def test_watchdog_starts_when_enabled() -> None:
    """Сторож создаётся с таймаутом из настроек."""

    wdt = main.start_watchdog()
    assert wdt is not None
    assert wdt.timeout == 90000
    main.feed_wdt(wdt)
    assert wdt.fed == 1
    main.feed_wdt(None)


def test_watchdog_disabled(monkeypatch) -> None:
    """Выключенный сторож даёт None."""
    import secrets

    monkeypatch.setattr(secrets, "WDT_ENABLED", False)
    assert main.start_watchdog() is None


def test_watchdog_missing_on_firmware(monkeypatch) -> None:
    """Без machine.WDT возвращается None без падения."""
    import machine

    monkeypatch.delattr(machine, "WDT")
    assert main.start_watchdog() is None


def test_cycle_cold_boot_full(monkeypatch, fake_net) -> None:
    """Холодный старт: NTP + signup + PUT current/hourly/health + сон."""
    import machine

    ntp = _ntp_spy(monkeypatch)
    _signup_route(fake_net)
    main.run_cycle()

    assert machine.sleep_calls == [60000]
    assert ntp["count"] == 1
    posts = [c for c in fake_net.calls if c["method"] == "POST"]
    assert len(posts) == 1 and "identitytoolkit" in posts[0]["url"]
    puts = _puts(fake_net)
    assert len(puts) == 3
    assert any("/current.json?auth=id1" in c["url"] for c in puts)
    assert any("/hourly/" in c["url"] and "?auth=id1" in c["url"] for c in puts)
    assert any(c["url"].endswith("/health.json?auth=id1") for c in puts)

    health = [c for c in puts if "/health.json" in c["url"]][0]["payload"]
    assert health["wake"] == 1
    assert health["err"] == ""
    assert health["rssi"] == -55

    state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert state["refresh_token"] == "rf1"
    assert state["acc"]["n"] == 1
    assert state["wake_id"] == 1
    assert state["dht_fails"] == 0


def test_cycle_deep_wake_skips_ntp_and_auth(monkeypatch, fake_net) -> None:
    """Пробуждение: без NTP и лишнего auth, счётчик растёт."""
    import machine

    ntp = _ntp_spy(monkeypatch)
    machine._reset_cause = machine.DEEPSLEEP_RESET
    _fresh_rtc()
    old = fbsync.decode_rtc_state(machine.RTC._memory)
    old["wake_id"] = 5
    machine.RTC._memory = fbsync.encode_rtc_state(old).encode("utf-8")
    main.run_cycle()

    assert machine.sleep_calls == [60000]
    assert ntp["count"] == 0
    assert [c for c in fake_net.calls if c["method"] == "POST"] == []
    assert len(_puts(fake_net)) == 3
    state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert state["wake_id"] == 6


def test_cycle_sensor_failure_sleeps(fake_net) -> None:
    """При ошибке датчика сети нет, серия растёт, сон есть."""
    import machine

    class FailSensor:
        def measure(self) -> None:
            raise OSError("dht fail")

    old_sensor = main.sensor
    main.sensor = FailSensor()
    try:
        main.run_cycle()
    finally:
        main.sensor = old_sensor
    assert fake_net.calls == []
    assert machine.sleep_calls == [60000]
    state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert state["dht_fails"] == 1


def test_cycle_sensor_recreated_on_streak(monkeypatch, fake_net) -> None:
    """Третья ошибка подряд пересоздаёт датчик и чинит цикл."""
    import dht
    import machine

    class FailSensor:
        def measure(self) -> None:
            raise OSError("dht fail")

    old_sensor = main.sensor
    main.sensor = FailSensor()
    try:
        state = fbsync.new_state()
        state["dht_fails"] = 2
        machine.RTC._memory = fbsync.encode_rtc_state(state).encode("utf-8")
        _signup_route(fake_net)
        main.run_cycle()
    finally:
        main.sensor = old_sensor
    assert len(dht.made) == 1
    assert len(_puts(fake_net)) == 3
    saved = fbsync.decode_rtc_state(machine.RTC._memory)
    assert saved["dht_fails"] == 0


def test_cycle_no_time_skips_send(monkeypatch, fake_net) -> None:
    """Без точного времени отправки нет, сон есть."""
    import types

    import machine

    fake = types.ModuleType("ntptime")

    def _fail() -> None:
        raise OSError("no network")

    monkeypatch.setattr(fake, "settime", _fail, raising=False)
    monkeypatch.setitem(sys.modules, "ntptime", fake)
    main.run_cycle()

    assert _puts(fake_net) == []
    assert machine.sleep_calls == [60000]
    state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert state["time_valid"] is False


def test_cycle_auth_error_sleeps(fake_net) -> None:
    """Ошибка Auth: PUT нет, токены сброшены, сон есть."""
    import machine

    fake_net.route("POST", "identitytoolkit", 400, {"error": {"message": "X"}})
    main.run_cycle()

    assert _puts(fake_net) == []
    assert machine.sleep_calls == [60000]
    state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert state["refresh_token"] == ""


def test_cycle_bad_refresh_clears_tokens(fake_net) -> None:
    """Битый refresh-токен сбрасывается к signup на следующем цикле."""
    import machine

    fake_net.route("POST", "securetoken", 400, {"error": {"message": "INVALID_REFRESH_TOKEN"}})
    state = fbsync.new_state()
    state["refresh_token"] = "rf-bad"
    state["id_token"] = "id-old"
    state["expires_at"] = 100.0
    state["time_valid"] = True
    machine.RTC._memory = fbsync.encode_rtc_state(state).encode("utf-8")
    machine._reset_cause = machine.DEEPSLEEP_RESET

    main.run_cycle()

    assert _puts(fake_net) == []
    assert machine.sleep_calls == [60000]
    saved = fbsync.decode_rtc_state(machine.RTC._memory)
    assert saved["refresh_token"] == ""
    assert saved["id_token"] == ""


def test_cycle_net_timeout_sleeps(fake_net) -> None:
    """Висящая сеть (таймаут) не вешает цикл: сон обязателен."""
    import machine

    fake_net.fail_with = OSError("timed out")
    main.run_cycle()
    assert machine.sleep_calls == [60000]


def test_cycle_rollover_prunes_old_bucket(fake_net) -> None:
    """Смена часа: DELETE старого бакета, новый аккумулятор."""
    import time

    import machine

    now_hour = hour_start_ts(to_unix_ts(time.time()))
    state = fbsync.new_state()
    state["refresh_token"] = "rf0"
    state["id_token"] = "id0"
    state["expires_at"] = 9000000000
    state["time_valid"] = True
    state["acc"] = {"hour": now_hour - 7200, "sum_t": 60.0, "sum_h": 150.0, "n": 3}
    machine.RTC._memory = fbsync.encode_rtc_state(state).encode("utf-8")
    machine._reset_cause = machine.DEEPSLEEP_RESET

    main.run_cycle()

    deletes = [c for c in fake_net.calls if c["method"] == "DELETE"]
    assert len(deletes) == 1
    assert f"/hourly/{now_hour - 86400}.json" in deletes[0]["url"]
    new_state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert new_state["acc"]["hour"] == now_hour
    assert new_state["acc"]["n"] == 1


def test_cycle_wifi_failure_sleeps(fake_net, monkeypatch) -> None:
    """Обрыв Wi-Fi: сети нет, состояние сохранено, сон есть."""
    import machine

    def _fail(*args, **kwargs):
        raise OSError("no ap")

    monkeypatch.setattr(main, "wifi_connect", _fail)
    main.run_cycle()

    assert fake_net.calls == []
    assert machine.sleep_calls == [60000]


def test_post_error_includes_body(fake_net) -> None:
    """Текст ошибки Firebase попадает в исключение."""
    fake_net.route("POST", "identitytoolkit", 400, {"error": {"message": "OPERATION_X"}})
    with pytest.raises(OSError, match="HTTP 400: OPERATION_X"):
        main._post(fake_net, "https://identitytoolkit/foo", {})
