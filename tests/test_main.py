"""Тесты main.py: Wi-Fi, NTP, mDNS и deep-sleep цикл через стабы."""

import sys

import pytest

import fbsync
import main
from telemetry import hour_start_ts, to_unix_ts


@pytest.fixture(autouse=True)
def _reset_hw_stubs():
    """Сбрасывает состояние железных стабов перед каждым тестом."""
    import machine
    import urequests

    machine.RTC._memory = b""
    machine.sleep_calls.clear()
    machine._reset_cause = machine.PWRON_RESET
    urequests.reset()
    yield


def _signup_route() -> None:
    """Настраивает успешный анонимный signup."""
    import urequests

    urequests.route(
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


def test_wifi_empty_ssid_rejected() -> None:
    """Пустой SSID отклоняется сразу."""
    with pytest.raises(ValueError, match="SSID"):
        main.wifi_connect("", "pass", timeout_sec=1)


def test_wifi_bad_timeout_rejected() -> None:
    """Некорректный таймаут отклоняется."""
    with pytest.raises(ValueError, match="timeout_sec"):
        main.wifi_connect("ssid", "pass", timeout_sec=0)


def test_wifi_connect_success_via_stub() -> None:
    """Подключение через стаб network возвращает IP стаба."""
    assert main.wifi_connect("ssid", "pass", timeout_sec=2) == "192.168.1.100"


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
    assert main.wifi_connect("ssid", "pass", timeout_sec=2) == "192.168.1.100"


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


def test_cycle_cold_boot_full(monkeypatch) -> None:
    """Холодный старт: NTP + signup + PUT current/hourly + сон."""
    import machine
    import urequests

    ntp = _ntp_spy(monkeypatch)
    _signup_route()
    main.run_cycle()

    assert machine.sleep_calls == [60000]
    assert ntp["count"] == 1
    posts = [c for c in urequests.calls if c["method"] == "POST"]
    puts = [c for c in urequests.calls if c["method"] == "PUT"]
    assert len(posts) == 1 and "identitytoolkit" in posts[0]["url"]
    assert len(puts) == 2
    assert any("/current.json?auth=id1" in c["url"] for c in puts)
    assert any("/hourly/" in c["url"] and "?auth=id1" in c["url"] for c in puts)

    state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert state["refresh_token"] == "rf1"
    assert state["acc"]["n"] == 1
    assert state["time_valid"] is True


def test_cycle_deep_wake_skips_ntp_and_auth(monkeypatch) -> None:
    """Пробуждение: без NTP и без лишнего auth, только PUT."""
    import machine
    import urequests

    ntp = _ntp_spy(monkeypatch)
    machine._reset_cause = machine.DEEPSLEEP_RESET
    _fresh_rtc()
    main.run_cycle()

    assert machine.sleep_calls == [60000]
    assert ntp["count"] == 0
    assert [c for c in urequests.calls if c["method"] == "POST"] == []
    puts = [c for c in urequests.calls if c["method"] == "PUT"]
    assert len(puts) == 2
    assert all("?auth=id0" in c["url"] for c in puts)


def test_cycle_sensor_failure_sleeps() -> None:
    """При ошибке датчика сети нет, но сон обязателен."""
    import machine
    import urequests

    class FailSensor:
        def measure(self) -> None:
            raise OSError("dht fail")

    old_sensor = main.sensor
    main.sensor = FailSensor()
    try:
        main.run_cycle()
    finally:
        main.sensor = old_sensor
    assert urequests.calls == []
    assert machine.sleep_calls == [60000]


def test_cycle_no_time_skips_send(monkeypatch) -> None:
    """Без точного времени отправки нет, сон есть."""
    import types

    import machine
    import urequests

    fake = types.ModuleType("ntptime")

    def _fail() -> None:
        raise OSError("no network")

    monkeypatch.setattr(fake, "settime", _fail, raising=False)
    monkeypatch.setitem(sys.modules, "ntptime", fake)
    main.run_cycle()

    assert [c for c in urequests.calls if "firebaseio" in c["url"]] == []
    assert [c for c in urequests.calls if c["method"] == "PUT"] == []
    assert machine.sleep_calls == [60000]
    state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert state["time_valid"] is False


def test_cycle_auth_error_sleeps() -> None:
    """Ошибка Auth: PUT нет, состояние сохранено, сон есть."""
    import machine
    import urequests

    urequests.route("POST", "identitytoolkit", 400, {})
    main.run_cycle()

    assert [c for c in urequests.calls if c["method"] == "PUT"] == []
    assert machine.sleep_calls == [60000]
    state = fbsync.decode_rtc_state(machine.RTC._memory)
    assert state["refresh_token"] == ""


def test_cycle_rollover_prunes_old_bucket() -> None:
    """Смена часа: DELETE старого бакета, новый аккумулятор."""
    import time

    import machine
    import urequests

    import fbsync as _fb

    now_hour = hour_start_ts(to_unix_ts(time.time()))
    old_hour = now_hour - 7200
    state = _fb.new_state()
    state["refresh_token"] = "rf0"
    state["id_token"] = "id0"
    state["expires_at"] = 9000000000
    state["time_valid"] = True
    state["acc"] = {"hour": old_hour, "sum_t": 60.0, "sum_h": 150.0, "n": 3}
    machine.RTC._memory = _fb.encode_rtc_state(state).encode("utf-8")
    machine._reset_cause = machine.DEEPSLEEP_RESET

    main.run_cycle()

    deletes = [c for c in urequests.calls if c["method"] == "DELETE"]
    assert len(deletes) == 1
    assert f"/hourly/{now_hour - 86400}.json" in deletes[0]["url"]
    new_state = _fb.decode_rtc_state(machine.RTC._memory)
    assert new_state["acc"]["hour"] == now_hour
    assert new_state["acc"]["n"] == 1


def test_cycle_urequests_missing(monkeypatch) -> None:
    """Без urequests цикл уходит в сон без падения."""
    import machine

    monkeypatch.setattr(main, "urequests", None)
    main.run_cycle()
    assert machine.sleep_calls == [60000]
