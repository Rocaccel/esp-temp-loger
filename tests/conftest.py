"""Подменяет MicroPython-модули стабами перед импортом main."""

import sys
from pathlib import Path

STUBS_DIR = Path(__file__).parent / "stubs"
PROJECT_ROOT = Path(__file__).parent.parent

for _path in (str(STUBS_DIR), str(PROJECT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
