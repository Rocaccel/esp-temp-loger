"""Стаб модуля dht для запуска main.py под CPython в тестах."""

import random


class DHT22:
    """Минимальный стаб dht.DHT22 с псевдослучайными показаниями."""

    def __init__(self, pin) -> None:
        """Привязывает стаб к пину.

        Args:
            pin: Объект пина (игнорируется, хранится для отладки).
        """
        self.pin = pin
        self._temperature = 22.5
        self._humidity = 55.0

    def measure(self) -> None:
        """Имитирует опрос датчика с небольшим дрейфом значений."""
        self._temperature += random.uniform(-0.3, 0.3)
        self._humidity += random.uniform(-1.0, 1.0)
        self._humidity = min(100.0, max(0.0, self._humidity))

    def temperature(self) -> float:
        """Возвращает последнее измерение температуры.

        Returns:
            Температура в градусах Цельсия.
        """
        return self._temperature

    def humidity(self) -> float:
        """Возвращает последнее измерение влажности.

        Returns:
            Относительная влажность в процентах.
        """
        return self._humidity
