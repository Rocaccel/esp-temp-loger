# ESP32 климат-монитор → Firebase → GitHub Pages

ESP32 + DHT22 измеряет температуру и влажность, раз в 60 секунд
просыпается из deep sleep и отправляет данные в Firebase Realtime
Database. Страница показаний живёт на GitHub Pages и обновляется
в реальном времени через подписки Firebase.

Цепочка: `плата → Firebase RTDB → браузер`.

## Железо

- Плата ESP32 (любая с Wi-Fi).
- Датчик DHT22, дата-пин — **GPIO4**, питание — 3.3V, GND — GND.
- Про питание честно: голый модуль в deep sleep ест ~10 мкА,
  а обычный DevKit с USB-UART мостом и AMS1117 — 5–15 мА даже во сне.
  Расчёт ~4–5 дней на 2000 мА·ч относится к честному сну;
  реальный ток меряй тестером.

## Прошивка MicroPython

1. Залей свежую прошивку MicroPython для ESP32.
2. Поставь HTTP-клиент (в REPL):
   ```
   import mip
   mip.install("urequests")
   ```
3. Скопируй в корень файловой системы платы 5 файлов:
   `main.py`, `fbsync.py`, `telemetry.py`, `https.py`, `secrets.py`
   (последний генерируется ниже, остальные — из репозитория).

## Firebase с нуля

1. Создай проект в [консоли Firebase](https://console.firebase.google.com/).
2. Build → Realtime Database → Create Database.
3. Вкладка Rules: вставь содержимое `docs/SECURITY_RULES.json` из
   репозитория и нажми **Publish**. Черновик без Publish не работает.
4. Build → Authentication → Sign-in method → Add new provider →
   **Anonymous → Enable → Save**. Без этого плата получит
   `HTTP 400 OPERATION_NOT_ALLOWED` на signup.
5. Project settings → General: скопируй `apiKey`, ссылку на базу
   (`databaseURL`) — они понадобятся в конфиге.

## Конфигурация

```bash
# 1. Образец -> рабочий конфиг
cp config.example.toml config.toml
# 2. Впиши свои ssid/password/api_key/database_url в config.toml
# 3. Сгенерируй файл для платы
uv run python tools/gen_secrets.py
```

`config.toml` и `secrets.py` в git не коммитятся (см. `.gitignore`).
`apiKey` Firebase публичен по дизайну — доступ регулируют rules,
а не секретность ключа.

## Разработка (ПК)

```bash
uv sync --group dev   # окружение и зависимости
uv run pytest         # тесты (через стабы MicroPython-модулей)
uv run ruff check .   # линтер
uv run ruff format .  # форматирование
uv run pyright        # типы
```

- Код для платы: `main.py` (цикл замера), `fbsync.py` (Firebase REST,
  auth, RTC-состояние), `telemetry.py` (чистая логика времени),
  `https.py` (мини HTTPS-клиент с явными таймаутами вместо urequests —
  зависший TLS без таймаута вешал плату навсегда; keep-alive сессии:
  одно соединение на хост за цикл + один ретрай).
- Тесты в `tests/`, стабы железа — в `tests/stubs/`.
- В device-коде нельзя keyword-аргументы у C-методов MicroPython
  (`bytes.decode`, `socket.*`) — ловит `test_micropython_compat.py`.

## GitHub Pages

1. Запушь репозиторий на GitHub.
2. Settings → Pages → Deploy from branch → ветка, папка `/docs`.
3. При смене Firebase-проекта обнови `docs/firebase-config.js`.

Поправка часового пояса хранится в `localStorage` браузера
(у каждого устройства своя), в базу не пишется.

## Как это работает

- Плата спит (`machine.deepsleep`), просыпается каждые
  `sleep_sec` (по умолчанию 60).
- Замер → Wi-Fi (DHCP или статический IP из секции `[network]`) →
  refresh idToken → `PUT devices/<id>/current` →
  `PUT devices/<id>/hourly/<час>` с бегущим средним из RTC-памяти →
  `PUT devices/<id>/health` с диагностикой цикла → сон.
- При смене часа удаляется бакет старше 24 часов.
- NTP-синхронизация — только на холодном старте.
- Состояние между снами (токены, аккумулятор часа, счётчик
  пробуждений, серия ошибок DHT) — в `RTC.memory()`.
- Сторожевой таймер (`[watchdog]`, по умолчанию 90 сек) перезагружает
  зависший цикл; в deep sleep сторож стоит. Таймаут должен превышать
  сон + цикл.
- После 3 ошибок датчика подряд объект DHT пересоздаётся
  с паузой settle.
- Health-узел: `wake` (номер пробуждения), `rst` (reset_cause),
  `wifi_ms`, `dht_fails`, `mem`, `rssi`, `err` (этап отказа),
  `ts`, `net_ms` (сетевое время цикла). По нему видна посмертная
  сигнатура любого отказа.

## Диагностика по serial-логу

| Строка | Значение |
|---|---|
| `[FB] Отправлено: ...` | цикл успешен |
| `[FB-ERR] Auth: HTTP 400: OPERATION_NOT_ALLOWED (...)` | не включён Anonymous |
| `[FB-ERR] Auth: HTTP 401 ...` | битый/протухший токен, неверные часы |
| `HTTP 403` | правила запрещают (проверь Publish) |
| `HTTP 404` | неверный `databaseURL` (регион базы?) |
| `[SYS] Холодный старт` / `[SYS] Пробуждение из deep sleep` | причина старта |
| `[DHT-ERR]` | датчик не отвечает, цикл пропущен, плата спит дальше |
| `[WDT] Сторож: ...` | сторож запущен; тишина = выключен в конфиге или нет на прошивке |

При любой auth-ошибке токены сбрасываются — следующий цикл делает
полный signup сам, перезагрузка не нужна.

## Структура репозитория

```
main.py                 цикл платы: замер → Firebase → deep sleep
fbsync.py               REST Firebase, auth, RTC-состояние, аккумулятор часа
https.py                мини HTTPS-клиент с таймаутами (сокеты + TLS)
telemetry.py            чистые функции времени (общие)
secrets.py              генерируется, на плату (в git нет)
config.toml             локальный конфиг (в git нет)
config.example.toml     образец конфига
tools/gen_secrets.py    генератор secrets.py из config.toml
docs/                   GitHub Pages: index.html, app.js, firebase-config.js
docs/SECURITY_RULES.json правила RTDB для консоли Firebase
tests/                  pytest-тесты + стабы MicroPython (tests/stubs/)
```
