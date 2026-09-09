"""Чистая логика времени без зависимостей от железа.

Модуль не импортирует MicroPython-модули (network, machine, dht),
поэтому напрямую тестируется pytest на ПК и заливается на ESP32
в корень файловой системы рядом с main.py.
"""

SECONDS_PER_HOUR = 3600
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


def hour_start_ts(ts: float) -> int:
    """Округляет метку времени вниз до начала часа.

    Args:
        ts: Метка времени в секундах (time.time()).

    Returns:
        Метка начала часа в секундах.
    """
    return int(ts) // SECONDS_PER_HOUR * SECONDS_PER_HOUR
