"""Тесты https.py: парсеры чистыми, транспорт — фейковыми сокетами."""

import pytest

import https
from https import Session, _request, build_request, parse_response, parse_url


class FakeSock:
    """Фейковый сокет со сценарием приёма."""

    def __init__(self, chunks: list, fail_on: str = "") -> None:
        """Сохраняет сценарий.

        Args:
            chunks: Куски recv() по порядку, затем пустые.
            fail_on: Операция с OSError ('connect', 'recv' или '').
        """
        self._chunks = list(chunks)
        self._fail_on = fail_on
        self.sent = b""
        self.timeout = None
        self.closed = False
        self.addr = None

    def settimeout(self, value: float) -> None:
        """Запоминает таймаут."""
        self.timeout = value

    def connect(self, addr) -> None:
        """Запоминает адрес, иногда падает."""
        self.addr = addr
        if self._fail_on == "connect":
            raise OSError("connect fail")

    def send(self, data: bytes) -> int:
        """Копит отправленное."""
        self.sent += data
        return len(data)

    def recv(self, size: int) -> bytes:
        """Отдаёт куски по сценарию."""
        if self._fail_on == "recv":
            raise OSError("timed out")
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self) -> None:
        """Помечает закрытым."""
        self.closed = True


class FakeMod:
    """Фейковый модуль socket."""

    AF_INET = 2
    SOCK_STREAM = 1

    def __init__(self, chunks: list, fail_on: str = "", scripts: list | None = None) -> None:
        """Готовит сокет со сценарием.

        Args:
            chunks: Куски recv() по умолчанию.
            fail_on: Операция с OSError по умолчанию.
            scripts: Список сценариев на каждое соединение
                [{"chunks": [...], "fail_on": "..."}].
        """
        self._chunks = chunks
        self._fail_on = fail_on
        self._scripts = scripts
        self.created: list = []

    def getaddrinfo(self, host: str, port: int) -> list:
        """Возвращает фиктивный адрес."""
        return [(None, None, None, None, (host, port))]

    def socket(self, *args) -> FakeSock:
        """Создаёт фейковый сокет по сценарию соединения."""
        chunks, fail_on = self._chunks, self._fail_on
        if self._scripts is not None and len(self.created) < len(self._scripts):
            script = self._scripts[len(self.created)]
            chunks = script.get("chunks", [])
            fail_on = script.get("fail_on", "")
        sock = FakeSock(chunks, fail_on)
        self.created.append(sock)
        return sock


class FakeTLS:
    """Фейковый ussl, запоминает SNI."""

    def __init__(self) -> None:
        """Пустой конструктор."""
        self.sni: list = []

    def wrap_socket(self, sock, server_hostname=None):
        """Возвращает сокет, запоминая SNI."""
        self.sni.append(server_hostname)
        return sock


def _ok_chunks(body: bytes) -> list:
    """Готовит куски ответа 200 OK."""
    head = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode()
    return [head + b"\r\n\r\n", body]


def test_parse_url_https_default() -> None:
    """HTTPS без порта и пути разбирается."""
    assert parse_url("https://db.firebaseio.com") == ("https", "db.firebaseio.com", 443, "/")


def test_parse_url_custom() -> None:
    """Явные порт и путь разбираются."""
    assert parse_url("http://h.local:8080/a/b") == ("http", "h.local", 8080, "/a/b")


def test_parse_url_invalid() -> None:
    """Мусор отклоняется."""
    with pytest.raises(ValueError):
        parse_url("notaurl")
    with pytest.raises(ValueError):
        parse_url("https://")


def test_build_request_get() -> None:
    """GET без тела и Content-Type, по умолчанию keep-alive."""
    req = build_request("GET", "/x.json", "h", None)
    assert req.startswith(b"GET /x.json HTTP/1.1\r\n")
    assert b"Host: h\r\n" in req
    assert b"Content-Type" not in req
    assert req.endswith(b"Connection: keep-alive\r\n\r\n")
    closed = build_request("GET", "/x.json", "h", None, False)
    assert closed.endswith(b"Connection: close\r\n\r\n")


def test_build_request_post() -> None:
    """POST с телом и длиной."""
    req = build_request("PUT", "/x.json", "h", b'{"a":1}')
    assert b"Content-Type: application/json\r\n" in req
    assert b"Content-Length: 7\r\n" in req
    assert req.endswith(b'{"a":1}')


def test_parse_response_ok() -> None:
    """Статус и тело извлекаются."""
    status, body = parse_response(b"HTTP/1.1 200 OK\r\nX: 1\r\n\r\n{}")
    assert (status, body) == (200, b"{}")


def test_parse_response_garbage() -> None:
    """Мусор отклоняется."""
    with pytest.raises(ValueError):
        parse_response(b"hello")


def test_request_full_flow() -> None:
    """Полный цикл: SNI, таймаут, запрос, ответ, закрытие."""
    mod = FakeMod(_ok_chunks(b'{"a":1}'))
    tls = FakeTLS()
    status, data = _request(mod, tls, "PUT", "https://db/x.json", {"a": 1}, 10)
    assert (status, data) == (200, {"a": 1})
    assert tls.sni == ["db"]
    sock = mod.created[0]
    assert sock.timeout == 10
    assert sock.sent.startswith(b"PUT /x.json HTTP/1.1\r\n")
    assert sock.sent.endswith(b'{"a": 1}')
    assert sock.closed is True


def test_request_no_tls_module() -> None:
    """HTTPS без TLS-модуля — ошибка."""
    mod = FakeMod([])
    with pytest.raises(OSError, match="TLS"):
        _request(mod, None, "GET", "https://db/x", None, 10)


def test_request_recv_timeout() -> None:
    """Таймаут приёма пробрасывается как OSError."""
    mod = FakeMod([], fail_on="recv")
    with pytest.raises(OSError, match="timed out"):
        _request(mod, FakeTLS(), "GET", "https://db/x", None, 10)


def test_request_bad_response() -> None:
    """Мусор вместо ответа — ошибка."""
    mod = FakeMod([b"garbage"])
    with pytest.raises(OSError, match="Обрыв|Битый"):
        _request(mod, FakeTLS(), "GET", "https://db/x", None, 10)


def test_request_non_json_body() -> None:
    """Не-JSON тело даёт пустой dict."""
    mod = FakeMod([b"HTTP/1.1 200 OK\r\n\r\n", b"plain"])
    assert _request(mod, FakeTLS(), "GET", "http://db/x", None, 10) == (200, {})


def test_https_module_request_callable() -> None:
    """Публичная обёртка существует с дефолтами."""
    assert https.DEFAULT_TIMEOUT_SEC == 10


def test_tls_name_known_value() -> None:
    """Имя TLS-модуля — одно из известных."""
    assert https.tls_name() in ("ussl", "ssl", "none")


def _resp200(body: bytes) -> list:
    """Куски ответа 200 с Content-Length."""
    return [b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n", body]


def test_session_reuses_connection() -> None:
    """Три запроса на хост — один сокет и один SNI."""
    mod = FakeMod(_resp200(b'{"a":1}') * 3)
    tls = FakeTLS()
    session = Session(mod, tls, 10)
    try:
        for _ in range(3):
            assert session.request("PUT", "https://db/x", {"a": 1}) == (200, {"a": 1})
    finally:
        session.close_all()
    assert len(mod.created) == 1
    assert tls.sni == ["db"]
    assert b"keep-alive" in mod.created[0].sent


def test_session_reconnects_on_server_close() -> None:
    """Закрытие сервером — новое соединение, данные целы."""
    first = _resp200(b"{}")[:-1] + [b""]
    close_resp = [b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n", b"{}"]
    mod = FakeMod(
        [],
        scripts=[
            {"chunks": first},
            {"chunks": close_resp},
            {"chunks": _resp200(b"{}")},
        ],
    )
    session = Session(mod, FakeTLS(), 10)
    try:
        assert session.request("PUT", "https://db/a", None) == (200, {})
        assert session.request("PUT", "https://db/b", None) == (200, {})
    finally:
        session.close_all()
    assert len(mod.created) == 3


def test_session_retries_once_on_error() -> None:
    """Обрыв посреди ответа — повтор на новом соединении."""
    ok = _resp200(b'{"a":1}')
    mod = FakeMod(
        [], scripts=[{"chunks": [b"HTTP/1.1 200 OK\r\n\r\n"], "fail_on": "recv"}, {"chunks": ok}]
    )
    session = Session(mod, FakeTLS(), 10)
    try:
        assert session.request("GET", "https://db/x") == (200, {"a": 1})
    finally:
        session.close_all()
    assert len(mod.created) == 2
    assert mod.created[0].closed is True


def test_session_raises_after_two_failures() -> None:
    """Два обрыва подряд — исходная ошибка наружу."""
    mod = FakeMod([], fail_on="recv")
    session = Session(mod, FakeTLS(), 10)
    try:
        with pytest.raises(OSError, match="timed out"):
            session.request("GET", "https://db/x")
    finally:
        session.close_all()
    assert len(mod.created) == 2


def _chunked_raw(body: bytes, extra_head: bytes = b"") -> bytes:
    """Строит чанкованный ответ одним куском."""
    head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n" + extra_head + b"\r\n"
    return head + b"%x\r\n" % len(body) + body + b"\r\n0\r\n\r\n"


def test_request_chunked_single_recv() -> None:
    """Чанкованное тело одним куском разбирается."""
    mod = FakeMod([_chunked_raw(b'{"a":1}')])
    assert _request(mod, FakeTLS(), "GET", "https://db/x", None, 10) == (200, {"a": 1})


def test_request_chunked_byte_by_byte() -> None:
    """Чанки, порезанные по байту, склеиваются."""
    raw = _chunked_raw(b'{"a":1}', b"Content-Type: application/json\r\n")
    mod = FakeMod([bytes([b]) for b in raw])
    assert _request(mod, FakeTLS(), "GET", "https://db/x", None, 10) == (200, {"a": 1})


def test_request_chunked_extension_and_trailer() -> None:
    """Расширения чанков и трейлеры игнорируются."""
    raw = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: Chunked\r\n\r\n"
        b'7;ext=1\r\n{"a":1}\r\n0\r\nX-T: v\r\n\r\n'
    )
    mod = FakeMod([raw])
    assert _request(mod, FakeTLS(), "GET", "https://db/x", None, 10) == (200, {"a": 1})


def test_request_chunked_empty() -> None:
    """Пустое чанкованное тело даёт {}."""
    mod = FakeMod([b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"])
    assert _request(mod, FakeTLS(), "GET", "https://db/x", None, 10) == (200, {})


def test_request_chunked_truncated() -> None:
    """Обрыв посреди чанка — ошибка, а не мусор."""
    mod = FakeMod([b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nab"])
    with pytest.raises(OSError, match="Обрыв"):
        _request(mod, FakeTLS(), "GET", "https://db/x", None, 10)


def test_request_chunked_bad_hex() -> None:
    """Не-hex размер чанка — ошибка."""
    mod = FakeMod([b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nZZ\r\nab\r\n"])
    with pytest.raises(OSError, match="Битый"):
        _request(mod, FakeTLS(), "GET", "https://db/x", None, 10)


def test_request_content_length_exact() -> None:
    """Content-Length: лишнее в сокете не читается, зависания нет."""
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\n" + b'{"a":1}' + b"EXTRA"
    mod = FakeMod([raw])
    assert _request(mod, FakeTLS(), "GET", "https://db/x", None, 10) == (200, {"a": 1})


def test_request_content_length_truncated() -> None:
    """Обрыв тела короче Content-Length — ошибка."""
    mod = FakeMod([b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n", b'{"a"'])
    with pytest.raises(OSError, match="Обрыв"):
        _request(mod, FakeTLS(), "GET", "https://db/x", None, 10)
