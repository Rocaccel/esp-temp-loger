"""Стаб модуля network для запуска main.py под CPython в тестах."""

STA_IF = 0
AP_IF = 1

events = []
_current_hostname = "esp32"


def hostname(name: str | None = None) -> str:
    """Стаб network.hostname(): запоминает имя и порядок вызовов.

    Args:
        name: Новое имя хоста или None для чтения.

    Returns:
        Текущее имя хоста.
    """
    global _current_hostname
    events.append("hostname")
    if name is not None:
        _current_hostname = name
    return _current_hostname


class WLAN:
    """Минимальный стаб network.WLAN в режиме STA."""

    STA_IF = 0
    AP_IF = 1

    def __init__(self, interface_id: int = 0) -> None:
        """Создаёт неподключённый интерфейс.

        Args:
            interface_id: Идентификатор интерфейса (игнорируется).
        """
        self._connected = False
        self._active = False
        self.static_cfg = None

    def active(self, state: bool | None = None) -> bool:
        """Включает/выключает интерфейс или возвращает состояние.

        Args:
            state: Новое состояние или None для чтения.

        Returns:
            Текущее состояние активности.
        """
        if state is not None:
            events.append("active")
            self._active = bool(state)
        return self._active

    def connect(self, ssid: str, password: str) -> None:
        """Имитирует мгновенное подключение.

        Args:
            ssid: Имя сети (игнорируется).
            password: Пароль (игнорируется).
        """
        self._connected = True

    def isconnected(self) -> bool:
        """Проверяет подключение.

        Returns:
            True после вызова connect().
        """
        return self._connected

    def ifconfig(self, cfg: tuple | None = None) -> tuple:
        """Возвращает или задаёт сетевую конфигурацию.

        Args:
            cfg: Кортеж (ip, mask, gateway, dns) для статики или None.

        Returns:
            Кортеж (ip, mask, gateway, dns).
        """
        if cfg is not None:
            self.static_cfg = tuple(cfg)
            return self.static_cfg
        if self.static_cfg is not None:
            return self.static_cfg
        return ("192.168.1.100", "255.255.255.0", "192.168.1.1", "8.8.8.8")

    def status(self, param: str = "") -> int:
        """Возвращает фиктивный статус.

        Args:
            param: Имя параметра (поддерживается 'rssi').

        Returns:
            -55 для rssi, иначе 0.
        """
        if param == "rssi":
            return -55
        return 0
