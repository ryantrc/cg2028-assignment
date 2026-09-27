"""Configuration for the Telegram fall-alert MVP.

All paths are resolved relative to this directory.  The bot token is never
included in the configuration object's representation or in log messages.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parent


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read .env using python-dotenv when available, with a small fallback.

    The fallback keeps tests and first-run diagnostics useful before
    dependencies have been installed.  It intentionally implements only the
    simple KEY=VALUE form used by .env.example.
    """

    try:
        from dotenv import dotenv_values
    except ImportError:
        dotenv_values = None
    if dotenv_values is not None:
        return {
            str(key): str(value)
            for key, value in dotenv_values(path).items()
            if key and value is not None
        }

    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _path_from(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


@dataclass(frozen=True)
class Config:
    root: Path
    telegram_token: str
    serial_port: str
    baud_rate: int
    activity_database_path: Path
    activity_csv_path: Path
    bot_database_path: Path
    trigger_event: str
    alert_cooldown_seconds: float
    serial_retry_seconds: float
    log_level: str

    def __repr__(self) -> str:  # pragma: no cover - a safety guard
        return (
            "Config(root={!r}, telegram_token='<redacted>', serial_port={!r}, "
            "baud_rate={!r}, activity_database_path={!r}, activity_csv_path={!r}, "
            "bot_database_path={!r}, trigger_event={!r}, alert_cooldown_seconds={!r}, "
            "serial_retry_seconds={!r}, log_level={!r})"
        ).format(
            self.root,
            self.serial_port,
            self.baud_rate,
            self.activity_database_path,
            self.activity_csv_path,
            self.bot_database_path,
            self.trigger_event,
            self.alert_cooldown_seconds,
            self.serial_retry_seconds,
            self.log_level,
        )


def load_config(root: Path | str = ROOT, environ: Mapping[str, str] | None = None) -> Config:
    """Load .env first, then environment variables as overrides."""

    root = Path(root).resolve()
    values = _read_dotenv(root / ".env")
    values.update(dict(os.environ if environ is None else environ))

    token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is required in telegram-bot-mvp/.env; "
            "create it with BotFather and do not commit it."
        )

    serial_port = values.get("SERIAL_PORT", "AUTO").strip()
    if not serial_port:
        raise ValueError("SERIAL_PORT must be an explicit device name, AUTO, or SIMULATED")
    if serial_port.upper() in {"AUTO", "SIMULATED"}:
        serial_port = serial_port.upper()

    def integer(name: str, default: str) -> int:
        try:
            number = int(values.get(name, default))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be an integer") from error
        if number <= 0:
            raise ValueError(f"{name} must be positive")
        return number

    def seconds(name: str, default: str) -> float:
        try:
            number = float(values.get(name, default))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be a number") from error
        if number < 0:
            raise ValueError(f"{name} must not be negative")
        return number

    return Config(
        root=root,
        telegram_token=token,
        serial_port=serial_port,
        baud_rate=integer("BAUD_RATE", "115200"),
        activity_database_path=_path_from(
            root, values.get("ACTIVITY_DATABASE_PATH", "runtime/activity_readings.sqlite3")
        ),
        activity_csv_path=_path_from(
            root, values.get("ACTIVITY_CSV_PATH", "runtime/activity_readings.csv")
        ),
        bot_database_path=_path_from(
            root, values.get("BOT_DATABASE_PATH", "runtime/telegram_bot.sqlite3")
        ),
        trigger_event=values.get("TRIGGER_EVENT", "POSSIBLE_FALL").strip() or "POSSIBLE_FALL",
        alert_cooldown_seconds=seconds("ALERT_COOLDOWN_SECONDS", "30"),
        serial_retry_seconds=seconds("SERIAL_RETRY_SECONDS", "5"),
        log_level=values.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )

