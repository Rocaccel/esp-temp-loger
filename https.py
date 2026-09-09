"""Минимальный HTTPS-клиент с явными таймаутами (замена urequests).

urequests не умеет таймауты: зависший TLS-хендшейк блокирует плату
навсегда. Здесь каждый сокет получает settimeout, чтение идёт до
закрытия соединения сервером (Connection: close) с лимитом памяти.
"""

import json
import socket

try:
    import ussl

    TLS_NAME = "ussl"
except ImportError:
    try:
        import ssl as ussl

        TLS_NAME = "ssl"
    except ImportError:
        ussl = None
        TLS_NAME = "none"

MAX_BODY_BYTES = 32768
DEFAULT_TIMEOUT_SEC = 10


def tls_name() -> str:
    """Имя связанного TLS-модуля для диагностики.

    Returns:
        'ussl', 'ssl' или 'none' если TLS недоступен.
    """
    return TLS_NAME


def parse_url(url: str) -> tuple:
    """Разбирает URL на схему, хост, порт и путь.

    Args:
        url: URL вида https://host[:port]/path.

    Returns:
        Кортеж (scheme, host, port, path).

    Raises:
        ValueError: Если URL без схемы или хоста.
    """
    parts = url.split("://", 1)
    if len(parts) != 2 or not parts[1]:
        raise ValueError("URL без схемы или хоста")
    scheme, rest = parts
    host_part = rest.split("/", 1)
    host_port = host_part[0].split(":", 1)
    host = host_port[0]
    if not host:
        raise ValueError("URL без хоста")
    if len(host_port) == 2 and host_port[1]:
        port = int(host_port[1])
    elif scheme == "https":
        port = 443
    else:
        port = 80
    path = "/" + host_part[1] if len(host_part) == 2 else "/"
    return (scheme, host, port, path)


def build_request(method: str, path: str, host: str, body=None, keep_alive=True) -> bytes:
    """Строит сырой HTTP-запрос.

    Args:
        method: Метод (GET, PUT, POST, DELETE).
        path: Путь запроса.
        host: Хост для заголовка Host.
        body: Тело запроса байтами или None.
        keep_alive: True для Connection: keep-alive.

    Returns:
        Байты запроса.
    """
    lines = [method + " " + path + " HTTP/1.1", "Host: " + host]
    if body is not None:
        lines.append("Content-Type: application/json")
        lines.append("Content-Length: " + str(len(body)))
    if keep_alive:
        lines.append("Connection: keep-alive")
    else:
        lines.append("Connection: close")
    lines.append("")
    lines.append("")
    head = "\r\n".join(lines).encode("ascii")
    if body is not None:
        return head + body
    return head


def parse_status(head: bytes) -> int:
    """Извлекает статус из заголовков ответа.

    Args:
        head: Байты заголовков без завершающего CRLF CRLF.

    Returns:
        HTTP-статус числом.

    Raises:
        ValueError: Если первая строка не похожа на HTTP.
    """
    first_line = head.split(b"\r\n", 1)[0]
    tokens = first_line.split(b" ", 2)
    if len(tokens) < 2 or not tokens[0].startswith(b"HTTP/"):
        raise ValueError("Не HTTP-ответ")
    return int(tokens[1])


def parse_headers(head: bytes) -> dict:
    """Разбирает заголовки ответа в словарь.

    Args:
        head: Байты заголовков без завершающего CRLF CRLF.

    Returns:
        Словарь {имя в нижнем регистре: значение}.
    """
    headers = {}
    lines = head.split(b"\r\n")
    for line in lines[1:]:
        pair = line.split(b":", 1)
        if len(pair) == 2:
            headers[pair[0].strip().lower()] = pair[1].strip()
    return headers


class _Reader:
    """Буферизованное чтение из сокета (границы recv произвольны)."""

    def __init__(self, sock) -> None:
        """Сохраняет сокет и пустой буфер.

        Args:
            sock: Сокет с recv(n).
        """
        self._sock = sock
        self._buf = b""

    def _fill(self) -> bytes:
        """Дочитывает один кусок в буфер.

        Returns:
            Кусок (пустой при закрытии).
        """
        chunk = self._sock.recv(1024)
        if chunk:
            self._buf = self._buf + chunk
        return chunk

    def read_until(self, marker: bytes, limit: int) -> bytes:
        """Читает до маркера (маркер съедается).

        Args:
            marker: Байты-разделитель.
            limit: Максимум байтов буфера.

        Returns:
            Байты до маркера.

        Raises:
            OSError: При обрыве или превышении лимита.
        """
        while self._buf.find(marker) < 0:
            if len(self._buf) > limit:
                raise OSError("Заголовки слишком длинные")
            if not self._fill():
                raise OSError("Обрыв до маркера")
        parts = self._buf.split(marker, 1)
        self._buf = parts[1]
        return parts[0]

    def read_exact(self, count: int) -> bytes:
        """Читает ровно count байтов.

        Args:
            count: Сколько байтов нужно.

        Returns:
            Байты запрошенной длины.

        Raises:
            OSError: При раннем закрытии.
        """
        while len(self._buf) < count:
            if not self._fill():
                raise OSError("Обрыв тела")
        out = self._buf[:count]
        self._buf = self._buf[count:]
        return out

    def read_rest(self, limit: int) -> bytes:
        """Читает до закрытия соединения.

        Args:
            limit: Максимум байтов.

        Returns:
            Все прочитанные байты.
        """
        out = [self._buf]
        total = len(self._buf)
        self._buf = b""
        while True:
            chunk = self._sock.recv(1024)
            if not chunk:
                break
            out.append(chunk)
            total = total + len(chunk)
            if total > limit:
                break
        return b"".join(out)


def read_chunked(reader) -> bytes:
    """Декодирует тело в chunked transfer encoding.

    Args:
        reader: _Reader после заголовков.

    Returns:
        Собранное тело байтами.

    Raises:
        OSError: При битых чанках и обрывах.
    """
    out = []
    total = 0
    while True:
        try:
            line = reader.read_until(b"\r\n", 512)
            size = int(line.split(b";", 1)[0].strip().decode("ascii"), 16)
        except ValueError:
            raise OSError("Битый размер чанка")
        if size == 0:
            while reader.read_until(b"\r\n", 512) != b"":
                pass
            break
        out.append(reader.read_exact(size))
        total = total + size
        if total > MAX_BODY_BYTES:
            break
        if reader.read_exact(2) != b"\r\n":
            raise OSError("Битый конец чанка")
    return b"".join(out)


def read_body(reader, headers: dict) -> bytes:
    """Читает тело по стратегии из заголовков.

    Args:
        reader: _Reader после заголовков.
        headers: Словарь заголовков из parse_headers().

    Returns:
        Тело ответа байтами.

    Raises:
        OSError: При обрывах и битых длинах.
        ValueError: При нечисловом Content-Length.
    """
    if b"content-length" in headers:
        return reader.read_exact(int(headers[b"content-length"]))
    encoding = headers.get(b"transfer-encoding", b"").lower()
    if encoding.find(b"chunked") >= 0:
        return read_chunked(reader)
    return reader.read_rest(MAX_BODY_BYTES)


def parse_response(raw: bytes) -> tuple:
    """Разбирает сырой HTTP-ответ.

    Args:
        raw: Байты ответа (заголовки + тело).

    Returns:
        Кортеж (статус int, тело bytes).

    Raises:
        ValueError: Если ответ не похож на HTTP.
    """
    parts = raw.split(b"\r\n\r\n", 1)
    head = parts[0]
    body = parts[1] if len(parts) == 2 else b""
    first_line = head.split(b"\r\n", 1)[0]
    tokens = first_line.split(b" ", 2)
    if len(tokens) < 2 or not tokens[0].startswith(b"HTTP/"):
        raise ValueError("Не HTTP-ответ")
    return (int(tokens[1]), body)


def close_quiet(sock) -> None:
    """Закрывает сокет, глотая ошибки.

    Args:
        sock: Сокет или None.
    """
    if sock is None:
        return
    try:
        sock.close()
    except Exception:
        pass


def open_conn(sock_mod, tls_mod, scheme: str, host: str, port: int, timeout: int):
    """Открывает (TLS-)соединение с таймаутом.

    Args:
        sock_mod: Модуль сокетов.
        tls_mod: Модуль TLS или None для http.
        scheme: 'http' или 'https'.
        host: Хост.
        port: Порт.
        timeout: Таймаут операций в секундах.

    Returns:
        Подключённый сокет.

    Raises:
        OSError: При сетевых ошибках и таймауте, отсутствии TLS.
    """
    addr = sock_mod.getaddrinfo(host, port)[0][-1]
    sock = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(addr)
        if scheme == "https":
            if tls_mod is None:
                raise OSError("Нет TLS-модуля для https")
            sock = tls_mod.wrap_socket(sock, server_hostname=host)
        return sock
    except Exception:
        close_quiet(sock)
        raise


def exchange(sock, method: str, path: str, host: str, payload=None) -> tuple:
    """Один запрос-ответ на открытом соединении.

    Args:
        sock: Открытое соединение.
        method: Метод запроса.
        path: Путь запроса.
        host: Хост для заголовка.
        payload: Словарь тела или None.

    Returns:
        Кортеж (статус int, dict ответа, server_close bool).

    Raises:
        OSError: При сетевых ошибках, таймауте и битом ответе.
    """
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = build_request(method, path, host, body, True)
    sent = 0
    while sent < len(req):
        sent = sent + sock.send(req[sent:])
    reader = _Reader(sock)
    head = reader.read_until(b"\r\n\r\n", 8192)
    try:
        status = parse_status(head)
        headers = parse_headers(head)
        body_raw = read_body(reader, headers)
    except ValueError:
        raise OSError("Битый HTTP-ответ")
    try:
        data = json.loads(body_raw) if body_raw else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    conn = headers.get(b"connection", b"").lower()
    framed = b"content-length" in headers
    framed = framed or headers.get(b"transfer-encoding", b"").lower().find(b"chunked") >= 0
    return (status, data, conn == b"close" or not framed)


class Session:
    """Переиспользуемые соединения: одно на хост, один ретрай."""

    def __init__(
        self, sock_mod=None, tls_mod: object = "default", timeout: int = DEFAULT_TIMEOUT_SEC
    ) -> None:
        """Сохраняет модули и таймаут.

        Args:
            sock_mod: Модуль сокетов (по умолчанию системный).
            tls_mod: Модуль TLS, None — без TLS, 'default' — системный.
            timeout: Таймаут операций в секундах.
        """
        if sock_mod is None:
            sock_mod = socket
        if tls_mod == "default":
            tls_mod = ussl
        self._sock_mod = sock_mod
        self._tls_mod = tls_mod
        self._timeout = timeout
        self._conns = {}

    def _drop(self, key) -> None:
        """Закрывает и забывает соединение.

        Args:
            key: Ключ (scheme, host, port).
        """
        close_quiet(self._conns.pop(key, None))

    def close_all(self) -> None:
        """Закрывает все соединения."""
        for key in list(self._conns.keys()):
            self._drop(key)

    def request(self, method: str, url: str, payload=None) -> tuple:
        """Запрос с reuse соединения и одним ретраем.

        Args:
            method: Метод запроса.
            url: Полный URL.
            payload: Словарь тела или None.

        Returns:
            Кортеж (статус int, dict ответа).

        Raises:
            OSError: Если обе попытки провалились.
            ValueError: При некорректном URL.
        """
        scheme, host, port, path = parse_url(url)
        key = (scheme, host, port)
        last_error = OSError("Повтор не удался")
        for attempt in (0, 1):
            sock = self._conns.get(key)
            if sock is None:
                try:
                    sock = open_conn(
                        self._sock_mod, self._tls_mod, scheme, host, port, self._timeout
                    )
                except OSError as error:
                    last_error = error
                    if attempt == 0:
                        continue
                    raise
                self._conns[key] = sock
            try:
                status, data, server_close = exchange(sock, method, path, host, payload)
            except OSError as error:
                self._drop(key)
                last_error = error
                if attempt == 0:
                    continue
                raise
            if server_close:
                self._drop(key)
            return (status, data)
        raise last_error


def _request(sock_mod, tls_mod, method: str, url: str, payload=None, timeout: int = 10) -> tuple:
    """Выполняет HTTPS-запрос через инжектируемые модули.

    Args:
        sock_mod: Модуль сокетов (socket или фейк в тестах).
        tls_mod: Модуль TLS (ussl, фейк или None для http).
        method: Метод запроса.
        url: Полный URL.
        payload: Словарь тела или None.
        timeout: Таймаут операций сокета в секундах.

    Returns:
        Кортеж (статус int, распарсенный JSON dict, {} если тела нет).

    Raises:
        OSError: При сетевых ошибках, таймауте и битом ответе.
        ValueError: При некорректном URL.
    """
    session = Session(sock_mod, tls_mod, timeout)
    try:
        return session.request(method, url, payload)
    finally:
        session.close_all()


def request(method: str, url: str, payload=None, timeout: int = DEFAULT_TIMEOUT_SEC) -> tuple:
    """Выполняет HTTP-запрос системными socket/ussl.

    Args:
        method: Метод запроса.
        url: Полный URL.
        payload: Словарь тела или None.
        timeout: Таймаут в секундах.

    Returns:
        Кортеж (статус int, dict ответа).
    """
    return _request(socket, ussl, method, url, payload, timeout)
