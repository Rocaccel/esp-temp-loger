"""Стаб urequests: записывает вызовы, отвечает по сценарию."""

calls = []
routes = {}


def reset() -> None:
    """Сбрасывает записанные вызовы и сценарий."""
    calls.clear()
    routes.clear()


def route(method: str, url_part: str, status: int, data: dict) -> None:
    """Добавляет ответ в сценарий.

    Args:
        method: Метод HTTP (POST, PUT, GET, DELETE).
        url_part: Подстрока URL для совпадения.
        status: Статус ответа.
        data: Тело ответа.
    """
    routes.setdefault((method, url_part), []).append((status, data))


class FakeResponse:
    """Минимальный ответ urequests."""

    def __init__(self, status: int, data: dict) -> None:
        """Сохраняет статус и тело.

        Args:
            status: HTTP-статус.
            data: Тело ответа.
        """
        self.status_code = status
        self._data = data
        self.closed = False

    def json(self) -> dict:
        """Возвращает тело ответа.

        Returns:
            Словарь тела.
        """
        return self._data

    def close(self) -> None:
        """Помечает ответ закрытым."""
        self.closed = True


def _dispatch(method: str, url: str, payload: dict | None) -> FakeResponse:
    """Записывает вызов и подбирает ответ из сценария.

    Args:
        method: Метод HTTP.
        url: Полный URL.
        payload: Тело запроса.

    Returns:
        FakeResponse по сценарию или пустой 200.
    """
    calls.append({"method": method, "url": url, "payload": payload})
    for (route_method, part), queue in routes.items():
        if route_method == method and part in url and queue:
            status, data = queue.pop(0)
            return FakeResponse(status, data)
    return FakeResponse(200, {})


def post(url: str, json: dict | None = None) -> FakeResponse:
    """Стаб urequests.post()."""
    return _dispatch("POST", url, json)


def put(url: str, json: dict | None = None) -> FakeResponse:
    """Стаб urequests.put()."""
    return _dispatch("PUT", url, json)


def get(url: str) -> FakeResponse:
    """Стаб urequests.get()."""
    return _dispatch("GET", url, None)


def delete(url: str) -> FakeResponse:
    """Стаб urequests.delete()."""
    return _dispatch("DELETE", url, None)
