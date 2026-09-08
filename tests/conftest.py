"""Подменяет MicroPython-модули стабами перед импортом main."""

import importlib.util
import sys
from pathlib import Path

STUBS_DIR = Path(__file__).parent / "stubs"
PROJECT_ROOT = Path(__file__).parent.parent

# Порядок важен: стабы ПЕРЕД корнем, чтобы import machine/network/dht/
# urequests всегда брался из стабов.
for _path in (str(PROJECT_ROOT), str(STUBS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _pin_stub(name: str) -> None:
    """Фиксирует стаб в sys.modules, чтобы его не перебил одноимённый файл.

    Нужно для secrets: сгенерированный secrets.py лежит в корне проекта,
    а pytest при импорте тестов двигает корень вперёд sys.path.

    Args:
        name: Имя модуля-стаба из tests/stubs.
    """
    spec = importlib.util.spec_from_file_location(name, STUBS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


_pin_stub("secrets")
