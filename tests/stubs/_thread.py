"""Стаб модуля _thread для запуска main.py под CPython в тестах."""

import threading


class _Lock:
    """Обёртка threading.Lock с интерфейсом MicroPython."""

    def __init__(self) -> None:
        """Создаёт внутренний threading.Lock."""
        self._lock = threading.Lock()

    def acquire(self, waitflag: int = 1) -> bool:
        """Захватывает блокировку.

        Args:
            waitflag: 1 — ждать, 0 — не ждать.

        Returns:
            True при успешном захвате.
        """
        return self._lock.acquire(blocking=bool(waitflag))

    def release(self) -> None:
        """Освобождает блокировку."""
        self._lock.release()

    def locked(self) -> bool:
        """Проверяет состояние блокировки.

        Returns:
            True если заблокирован.
        """
        return self._lock.locked()

    def __enter__(self) -> "_Lock":
        """Поддержка with (в MicroPython зависит от прошивки).

        Returns:
            Сам объект блокировки.
        """
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        """Освобождает блокировку при выходе из with."""
        self.release()


def allocate_lock() -> _Lock:
    """Создаёт новую блокировку (аналог _thread.allocate_lock).

    Returns:
        Новая блокировка.
    """
    return _Lock()


def start_new_thread(function, args: tuple = ()) -> None:
    """Заглушка запуска потока — ничего не делает в тестах.

    Args:
        function: Функция потока (не вызывается).
        args: Аргументы функции.
    """
    return None
