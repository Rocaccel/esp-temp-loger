"""Стаб модуля machine для запуска main.py под CPython в тестах."""

import threading

applied_freq = None


def freq(value: int) -> None:
    """Стаб machine.freq(): запоминает запрошенную частоту CPU.

    Args:
        value: Частота в Гц.
    """
    global applied_freq
    applied_freq = value


class Pin:
    """Минимальный стаб machine.Pin."""

    IN = 0
    OUT = 1
    PULL_UP = 2

    def __init__(self, pin_id: int, mode: int = 0, pull: int = -1) -> None:
        """Сохраняет номер пина.

        Args:
            pin_id: Номер GPIO.
            mode: Режим (игнорируется).
            pull: Подтяжка (игнорируется).
        """
        self.id = pin_id
        self._value = 0
        self._lock = threading.Lock()

    def value(self, val: int | None = None) -> int:
        """Читает или пишет значение пина.

        Args:
            val: Новое значение или None для чтения.

        Returns:
            Текущее значение пина.
        """
        with self._lock:
            if val is not None:
                self._value = val
            return self._value
