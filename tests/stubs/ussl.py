"""Стаб модуля ussl для тестов https.py."""

wrapped = []


def wrap_socket(sock, server_hostname=None):
    """Стаб ussl.wrap_socket(): возвращает сокет как есть.

    Args:
        sock: Сырой сокет.
        server_hostname: SNI-хост (запоминается).

    Returns:
        Тот же сокет.
    """
    wrapped.append(server_hostname)
    return sock
