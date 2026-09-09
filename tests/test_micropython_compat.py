"""Совместимость device-кода с MicroPython.

В MicroPython встроенные методы, реализованные на C (bytes.decode/encode,
socket.send/recv и т.д.), НЕ принимают keyword-аргументы — любой kwargs
падает на устройстве с 'function doesn't take keyword arguments'.
Тест разбирает AST файлов, заливаемых на ESP32, и запрещает kwargs
у таких методов.
"""

import ast
from pathlib import Path

# Методы, в MicroPython реализованные на C (без поддержки kwargs).
C_METHODS = frozenset(
    {
        "decode",
        "encode",
        "send",
        "sendall",
        "sendto",
        "recv",
        "recvfrom",
        "recv_into",
        "connect",
        "bind",
        "listen",
        "accept",
        "setsockopt",
        "settimeout",
        "setblocking",
        "close",
        "read",
        "write",
        "measure",
    }
)

DEVICE_FILES = ("main.py", "telemetry.py", "fbsync.py", "https.py")


def _kwargs_violations(source: str) -> list:
    """Ищет вызовы C-методов с keyword-аргументами.

    Args:
        source: Исходный код файла.

    Returns:
        Список строк вида 'строка: метод(kw=...)'.
    """
    violations = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in C_METHODS:
            continue
        if node.keywords:
            names = ", ".join(k.arg or "**" for k in node.keywords)
            violations.append(f"строка {node.lineno}: .{func.attr}({names})")
    return violations


def test_device_code_has_no_kwargs_on_c_methods() -> None:
    """В device-файлах нет kwargs у C-методов MicroPython."""
    project_root = Path(__file__).parent.parent
    all_violations = []
    for name in DEVICE_FILES:
        source = (project_root / name).read_text(encoding="utf-8")
        for violation in _kwargs_violations(source):
            all_violations.append(f"{name}: {violation}")
    assert not all_violations, (
        "Keyword-аргументы у C-методов упадут на ESP32 "
        "('function doesn't take keyword arguments'): " + "; ".join(all_violations)
    )
