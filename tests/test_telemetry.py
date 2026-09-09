"""Тесты чистой логики telemetry (без железа, без мока)."""

from telemetry import UNIX_EPOCH_OFFSET_SEC, hour_start_ts, to_unix_ts


def test_to_unix_ts_adds_epoch_offset() -> None:
    """Метки переводятся из эпохи 2000 г. в эпоху 1970 г."""
    assert to_unix_ts(0.0) == UNIX_EPOCH_OFFSET_SEC
    assert to_unix_ts(3600.0) == UNIX_EPOCH_OFFSET_SEC + 3600


def test_hour_start_ts_truncates() -> None:
    """Метка округляется вниз до начала часа."""
    assert hour_start_ts(3700.0) == 3600
    assert hour_start_ts(3600.0) == 3600
    assert hour_start_ts(7199.9) == 3600
