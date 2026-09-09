"""Тесты генератора secrets.py."""

import pytest

from tools.gen_secrets import load_config, render_secrets


def _sample_config() -> dict:
    """Минимальный валидный конфиг."""
    return {
        "wifi": {"ssid": "s", "password": "p"},
        "firebase": {"api_key": "k", "database_url": "u", "device_id": "d"},
        "power": {"sleep_sec": 60},
        "watchdog": {"enabled": True, "timeout_ms": 90000},
        "device": {"mdns_hostname": "h"},
    }


def test_render_secrets_contains_values() -> None:
    """Сгенерированный модуль содержит все значения."""
    text = render_secrets(_sample_config())
    for expected in (
        'WIFI_SSID = "s"',
        'DEVICE_ID = "d"',
        "SLEEP_SEC = 60",
        "WDT_ENABLED = True",
        "WDT_TIMEOUT_MS = 90000",
        "NET_STATIC = ()",
    ):
        assert expected in text


def test_render_secrets_partial_network_rejected() -> None:
    """Частичная статика отклоняется."""
    config = _sample_config()
    config["network"] = {"static_ip": "1.2.3.4"}
    with pytest.raises(SystemExit) as exc:
        render_secrets(config)
    assert exc.value.code == 1


def test_render_secrets_full_network() -> None:
    """Полная статика попадает в модуль кортежем."""
    config = _sample_config()
    config["network"] = {
        "static_ip": "1.2.3.4",
        "static_mask": "255.255.255.0",
        "static_gw": "1.2.3.1",
        "static_dns": "8.8.8.8",
    }
    text = render_secrets(config)
    assert 'NET_STATIC = ("1.2.3.4", "255.255.255.0", "1.2.3.1", "8.8.8.8")' in text


def test_load_config_missing_file(tmp_path) -> None:
    """Отсутствующий файл завершает с кодом 1."""
    with pytest.raises(SystemExit) as exc:
        load_config(tmp_path / "config.toml")
    assert exc.value.code == 1


def test_load_config_missing_keys(tmp_path) -> None:
    """Неполный конфиг завершает с кодом 1."""
    path = tmp_path / "config.toml"
    path.write_text('[wifi]\nssid = "s"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        load_config(path)
    assert exc.value.code == 1


def test_load_config_ok(tmp_path) -> None:
    """Полный конфиг читается целиком."""
    path = tmp_path / "config.toml"
    path.write_text(
        '[wifi]\nssid = "s"\npassword = "p"\n'
        '[firebase]\napi_key = "k"\ndatabase_url = "u"\ndevice_id = "d"\n'
        "[power]\nsleep_sec = 60\n"
        "[watchdog]\nenabled = true\ntimeout_ms = 90000\n"
        '[device]\nmdns_hostname = "h"\n',
        encoding="utf-8",
    )
    assert load_config(path)["firebase"]["device_id"] == "d"
