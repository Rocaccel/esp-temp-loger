"""Генерирует secrets.py для платы из config.toml.

Запуск с корня проекта: uv run python tools/gen_secrets.py
Файл secrets.py заливается на ESP32 рядом с main.py и fbsync.py.
"""

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    """Читает и минимально проверяет config.toml.

    Args:
        path: Путь к config.toml.

    Returns:
        Словарь секций конфигурации.

    Raises:
        SystemExit: Если файла нет или не хватает ключей.
    """
    if not path.exists():
        print(f"Нет {path.name}: скопируй config.example.toml")
        raise SystemExit(1)
    with open(path, "rb") as f:
        config = tomllib.load(f)
    required = [
        ("wifi", "ssid"),
        ("wifi", "password"),
        ("firebase", "api_key"),
        ("firebase", "database_url"),
        ("firebase", "device_id"),
        ("power", "sleep_sec"),
        ("watchdog", "enabled"),
        ("watchdog", "timeout_ms"),
        ("device", "mdns_hostname"),
    ]
    missing = [f"{s}.{k}" for s, k in required if not config.get(s, {}).get(k)]
    if missing:
        print(f"В config.toml не хватает: {', '.join(missing)}")
        raise SystemExit(1)
    return config


def render_secrets(config: dict) -> str:
    """Строит содержимое secrets.py.

    Args:
        config: Словарь секций конфигурации.

    Returns:
        Текст модуля secrets.py.
    """
    wifi = config["wifi"]
    firebase = config["firebase"]
    power = config["power"]
    watchdog = config["watchdog"]
    net = config.get("network", {})
    device = config["device"]
    static = (
        net.get("static_ip", ""),
        net.get("static_mask", ""),
        net.get("static_gw", ""),
        net.get("static_dns", ""),
    )
    if any(static) and not all(static):
        print("Секция [network]: заполни все 4 поля или оставь пустыми")
        raise SystemExit(1)
    if all(static):
        static_repr = "(" + ", ".join(f'"{v}"' for v in static) + ")"
    else:
        static_repr = "()"
    lines = [
        "# Сгенерировано tools/gen_secrets.py. Не коммитить!",
        f'WIFI_SSID = "{wifi["ssid"]}"',
        f'WIFI_PASSWORD = "{wifi["password"]}"',
        f'FIREBASE_API_KEY = "{firebase["api_key"]}"',
        f'FIREBASE_DB_URL = "{firebase["database_url"]}"',
        f'DEVICE_ID = "{firebase["device_id"]}"',
        f"SLEEP_SEC = {int(power['sleep_sec'])}",
        f"WDT_ENABLED = {bool(watchdog['enabled'])}",
        f"WDT_TIMEOUT_MS = {int(watchdog['timeout_ms'])}",
        f"NET_STATIC = {static_repr}",
        f'MDNS_HOSTNAME = "{device["mdns_hostname"]}"',
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """Точка входа: читает config.toml, пишет secrets.py."""
    config = load_config(ROOT / "config.toml")
    out_path = ROOT / "secrets.py"
    out_path.write_text(render_secrets(config), encoding="utf-8")
    print(f"Записан {out_path.name}")


if __name__ == "__main__":
    sys.exit(main())
