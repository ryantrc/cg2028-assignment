"""Testable core for the Telegram fall-alert bot.

The Telegram-specific wiring lives in bot.py.  This module contains parsing,
durable state, serial monitoring, recording, and delivery logic so the risky
parts can be tested without Telegram network access.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
import queue
import re
import sqlite3
import sys
from pathlib import Path
import threading
import time
from typing import Any, Callable, IO

from config import Config
from recorder_adapter import CSVRecorder, RecordingDatabase, SampleParser


LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


DETECTOR_RE = re.compile(
    r"^DETECTOR\s+"
    r"TimeMs=(?P<board_time_ms>\d{1,10})\s+"
    r"State=(?P<detector_state>[A-Za-z0-9_]+)\s+"
    r"Alarm=(?P<alarm>[01])\s+"
    r"Sensors=(?P<sensor_status>[A-Za-z0-9_]+)\s+"
    r"Event=(?P<event_name>[A-Za-z0-9_]+)"
    r"(?:\s+(?P<explanation>.*?))?\s*$"
)


@dataclass(frozen=True)
class DetectorRecord:
    board_time_ms: int
    detector_state: str
    alarm: int
    sensor_status: str
    event_name: str
    explanation: str
    raw_line: str
    laptop_received_at: str
    durable_event_identity: str
    qualified: bool


def parse_detector_line(line: str, trigger_event: str = "POSSIBLE_FALL") -> DetectorRecord | None:
    """Parse one complete DETECTOR line using named fields and strict tokens."""

    raw_line = line.rstrip("\r\n")
    match = DETECTOR_RE.fullmatch(raw_line.strip())
    if match is None:
        return None
    fields = match.groupdict()
    board_time_ms = int(fields["board_time_ms"])
    if board_time_ms > 0xFFFFFFFF:
        return None
    explanation = (fields.get("explanation") or "").strip()
    state = fields["detector_state"]
    event_name = fields["event_name"]
    alarm = int(fields["alarm"])
    sensor_status = fields["sensor_status"]
    identity_material = "|".join(
        (str(board_time_ms), state, str(alarm), sensor_status, event_name)
    ).encode("utf-8")
    identity = "v1:" + hashlib.sha256(identity_material).hexdigest()
    qualified = event_name == trigger_event and state == "FALL_LATCHED" and alarm == 1
    return DetectorRecord(
        board_time_ms=board_time_ms,
        detector_state=state,
        alarm=alarm,
        sensor_status=sensor_status,
        event_name=event_name,
        explanation=explanation,
        raw_line=raw_line,
        laptop_received_at=utc_now(),
        durable_event_identity=identity,
        qualified=qualified,
    )


def sanitize_error(error: BaseException, *, secret: str = "", chat_id: int | None = None) -> str:
    text = str(error).replace("\x00", " ").strip()
    if secret:
        text = text.replace(secret, "<redacted>")
    if chat_id is not None:
        text = text.replace(str(chat_id), "<chat>")
    text = re.sub(r"(?i)(token|bot)\s*[:=]\s*\S+", r"\1=<redacted>", text)
    return text[:500] or error.__class__.__name__


class BotStateStore:
    """SQLite state separate from the 22-column activity recording database."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path, timeout=30, check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        self._closed = False
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_subscribers (
                    chat_id INTEGER PRIMARY KEY,
                    subscribed_at TEXT NOT NULL,
                    display_name TEXT,
                    username TEXT
                );
                CREATE TABLE IF NOT EXISTS detector_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recording_session_id INTEGER,
                    laptop_received_at TEXT NOT NULL,
                    board_time_ms INTEGER NOT NULL CHECK (board_time_ms BETWEEN 0 AND 4294967295),
                    detector_state TEXT NOT NULL,
                    alarm INTEGER NOT NULL CHECK (alarm IN (0, 1)),
                    sensor_status TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    explanatory_message TEXT NOT NULL,
                    raw_uart_line TEXT NOT NULL,
                    qualified_for_alert INTEGER NOT NULL CHECK (qualified_for_alert IN (0, 1)),
                    durable_event_identity TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS detector_events_board_time
                    ON detector_events(board_time_ms);
                CREATE INDEX IF NOT EXISTS detector_events_qualified
                    ON detector_events(qualified_for_alert, laptop_received_at);
                CREATE TABLE IF NOT EXISTS telegram_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    detector_event_id INTEGER NOT NULL REFERENCES detector_events(id),
                    subscriber_chat_id INTEGER NOT NULL,
                    attempted_at TEXT NOT NULL,
                    delivery_status TEXT NOT NULL CHECK (delivery_status IN ('DELIVERED', 'FAILED')),
                    sanitized_error_text TEXT,
                    UNIQUE(detector_event_id, subscriber_chat_id)
                );
                CREATE INDEX IF NOT EXISTS telegram_deliveries_subscriber
                    ON telegram_deliveries(subscriber_chat_id, attempted_at);
                """
            )

    def subscribe(self, chat_id: int, display_name: str | None = None, username: str | None = None) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO telegram_subscribers(chat_id, subscribed_at, display_name, username)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET display_name=excluded.display_name,
                                                   username=excluded.username
                """,
                (chat_id, utc_now(), display_name, username),
            )

    def unsubscribe(self, chat_id: int) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "DELETE FROM telegram_subscribers WHERE chat_id = ?", (chat_id,)
            )

    def subscribers(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    "SELECT chat_id, display_name, username FROM telegram_subscribers ORDER BY chat_id"
                )
            )

    def subscriber_count(self) -> int:
        with self._lock:
            return int(
                self.connection.execute("SELECT COUNT(*) FROM telegram_subscribers").fetchone()[0]
            )

    def insert_detector_event(
        self, record: DetectorRecord, recording_session_id: int | None
    ) -> tuple[int, bool]:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO detector_events(
                    recording_session_id, laptop_received_at, board_time_ms,
                    detector_state, alarm, sensor_status, event_name,
                    explanatory_message, raw_uart_line, qualified_for_alert,
                    durable_event_identity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(durable_event_identity) DO NOTHING
                """,
                (
                    recording_session_id,
                    record.laptop_received_at,
                    record.board_time_ms,
                    record.detector_state,
                    record.alarm,
                    record.sensor_status,
                    record.event_name,
                    record.explanation,
                    record.raw_line,
                    int(record.qualified),
                    record.durable_event_identity,
                ),
            )
            row = self.connection.execute(
                "SELECT id FROM detector_events WHERE durable_event_identity = ?",
                (record.durable_event_identity,),
            ).fetchone()
            if row is None:  # pragma: no cover - guarded by the transaction
                raise RuntimeError("detector event was not stored")
            inserted = self.connection.execute(
                "SELECT changes()"
            ).fetchone()[0] == 1
            return int(row[0]), bool(inserted)

    def record_delivery(
        self,
        event_id: int,
        chat_id: int,
        status: str,
        error_text: str | None = None,
    ) -> None:
        if status not in {"DELIVERED", "FAILED"}:
            raise ValueError("delivery status must be DELIVERED or FAILED")
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO telegram_deliveries(
                    detector_event_id, subscriber_chat_id, attempted_at,
                    delivery_status, sanitized_error_text
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(detector_event_id, subscriber_chat_id) DO UPDATE SET
                    attempted_at=excluded.attempted_at,
                    delivery_status=excluded.delivery_status,
                    sanitized_error_text=excluded.sanitized_error_text
                """,
                (event_id, chat_id, utc_now(), status, error_text),
            )

    def latest_detector_event(self) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT * FROM detector_events ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def alert_allowed(self, event_id: int, cooldown_seconds: float) -> bool:
        """Return whether a newly qualified event is outside the durable cooldown."""

        if cooldown_seconds <= 0:
            return True
        with self._lock:
            current = self.connection.execute(
                "SELECT laptop_received_at FROM detector_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            previous = self.connection.execute(
                """
                SELECT laptop_received_at FROM detector_events
                WHERE qualified_for_alert = 1 AND id < ?
                ORDER BY id DESC LIMIT 1
                """,
                (event_id,),
            ).fetchone()
        if current is None or previous is None:
            return True
        try:
            elapsed = (
                datetime.fromisoformat(current["laptop_received_at"])
                - datetime.fromisoformat(previous["laptop_received_at"])
            ).total_seconds()
        except ValueError:
            return True
        return elapsed >= cooldown_seconds

    def delivery_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    "SELECT * FROM telegram_deliveries ORDER BY id"
                )
            )

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.connection.close()
                self._closed = True


class ActivityStore:
    """Owns the existing RecordingDatabase/CSVRecorder in one serial thread."""

    def __init__(self, database_path: Path | str, csv_path: Path | str, port: str):
        self.database_path = Path(database_path).resolve()
        self.csv_path = Path(csv_path).resolve()
        self.port = port
        self.database: Any | None = None
        self.csv: Any | None = None
        self.session_id: int | None = None
        self._closed = False

    def open(self) -> int:
        if self.database is not None:
            return int(self.session_id or 0)
        self.database = RecordingDatabase(self.database_path)
        try:
            self.csv = CSVRecorder(self.csv_path, self.database)
            self.session_id = self.database.start_session(
                "telegram-bot-mvp",
                "STM32 UART recording owned by the Telegram fall-alert bot",
                self.port,
            )
            return int(self.session_id)
        except BaseException:
            if self.csv is not None:
                self.csv.close()
            self.database.close()
            self.database = None
            self.csv = None
            raise

    def append_sample(self, sample: dict[str, Any]) -> dict[str, Any]:
        if self.database is None or self.csv is None or self.session_id is None:
            raise RuntimeError("activity store is not open")
        row = self.database.append_sample(self.session_id, sample)
        self.csv.append(row)
        return row

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.database is None:
            return
        try:
            if self.session_id is not None:
                self.database.finish_session(self.session_id)
        finally:
            try:
                if self.csv is not None:
                    self.csv.close()
            finally:
                self.database.close()

    @staticmethod
    def latest_reading(path: Path | str) -> dict[str, Any] | None:
        path = Path(path).resolve()
        if not path.is_file():
            return None
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM readings ORDER BY record_id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row is not None else None
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return None
            raise
        finally:
            connection.close()


class UARTProcessor:
    """The single processing path used by real serial and simulation input."""

    def __init__(
        self,
        activity: ActivityStore,
        state: BotStateStore,
        trigger_event: str = "POSSIBLE_FALL",
        alert_cooldown_seconds: float = 0,
        on_qualified_event: Callable[[int, DetectorRecord], None] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.activity = activity
        self.state = state
        self.trigger_event = trigger_event
        self.alert_cooldown_seconds = alert_cooldown_seconds
        self.on_qualified_event = on_qualified_event
        self.logger = logger or LOGGER
        self.parser = SampleParser()
        self.samples_saved = 0

    def process_line(self, line: str) -> DetectorRecord | None:
        sample = self.parser.feed(line)
        if sample is not None:
            self.activity.append_sample(sample)
            self.samples_saved += 1

        record = parse_detector_line(line, self.trigger_event)
        if record is None:
            return None
        event_id, inserted = self.state.insert_detector_event(
            record, self.activity.session_id
        )
        if (
            inserted
            and record.qualified
            and self.state.alert_allowed(event_id, self.alert_cooldown_seconds)
            and self.on_qualified_event is not None
        ):
            self.on_qualified_event(event_id, record)
        return record


@dataclass(frozen=True)
class SerialStatus:
    state: str
    configured_port: str
    active_port: str | None
    last_error: str | None
    session_id: int | None


class SimulatedStdinReader:
    """Line input adapter with a timeout, so monitor shutdown remains prompt."""

    def __init__(self, stream: IO[bytes] | None = None):
        self.stream = stream or getattr(sys.stdin, "buffer", sys.stdin)
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._closed = threading.Event()
        self._feeder = threading.Thread(target=self._feed, name="simulation-input", daemon=True)
        self._feeder.start()
        self.port = "SIMULATED"

    def _feed(self) -> None:
        try:
            while not self._closed.is_set():
                data = self.stream.readline()
                if not data:
                    self._queue.put(b"")
                    return
                if isinstance(data, str):
                    data = data.encode("ascii", errors="replace")
                self._queue.put(bytes(data))
        except BaseException:
            self._queue.put(b"")

    def read(self, timeout: float = 0.25) -> bytes | None:
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        return item

    def close(self) -> None:
        self._closed.set()


class PySerialReader:
    """pyserial adapter for a single 115200 8N1 owner connection."""

    def __init__(self, requested_port: str, baud_rate: int):
        import serial

        selected = requested_port
        if requested_port.upper() == "AUTO":
            from serial.tools import list_ports

            ports = sorted({item.device for item in list_ports.comports()})
            if len(ports) != 1:
                if not ports:
                    raise OSError("No serial port found; connect the STM32 or set SERIAL_PORT explicitly")
                raise OSError("Multiple serial ports found; set SERIAL_PORT to the STM32 device")
            selected = ports[0]

        self.port = selected
        kwargs = {"baudrate": baud_rate, "timeout": 0.25}
        try:
            self.serial = serial.Serial(selected, exclusive=True, **kwargs)
        except (TypeError, ValueError):
            # exclusive is unsupported on some pyserial/platform combinations.
            self.serial = serial.Serial(selected, **kwargs)

    def read(self, timeout: float = 0.25) -> bytes | None:
        self.serial.timeout = timeout
        data = self.serial.read(4096)
        return data or None

    def close(self) -> None:
        if getattr(self, "serial", None) is not None and self.serial.is_open:
            self.serial.close()


class SerialMonitor:
    """Background UART owner with retrying connections and line framing."""

    def __init__(
        self,
        config: Config,
        state: BotStateStore,
        on_qualified_event: Callable[[int, DetectorRecord], None] | None = None,
        reader_factory: Callable[[str, int], Any] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self.state = state
        self.on_qualified_event = on_qualified_event
        self.reader_factory = reader_factory or self._default_reader_factory
        self.logger = logger or LOGGER
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader: Any | None = None
        self._reader_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status = SerialStatus(
            "STOPPED", config.serial_port, None, None, None
        )
        self._activity: ActivityStore | None = None

    def _default_reader_factory(self, port: str, baud_rate: int) -> Any:
        if port.upper() == "SIMULATED":
            return SimulatedStdinReader()
        return PySerialReader(port, baud_rate)

    @property
    def status(self) -> SerialStatus:
        with self._status_lock:
            return self._status

    def _set_status(
        self,
        state: str,
        *,
        active_port: str | None = None,
        last_error: str | None = None,
    ) -> None:
        with self._status_lock:
            session_id = self._activity.session_id if self._activity is not None else None
            self._status = SerialStatus(
                state, self.config.serial_port, active_port, last_error, session_id
            )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="uart-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._reader_lock:
            reader = self._reader
            self._reader = None
        if reader is not None:
            try:
                reader.close()
            except BaseException:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        if self._thread is None or not self._thread.is_alive():
            self._set_status("STOPPED")

    def _run(self) -> None:
        activity = ActivityStore(
            self.config.activity_database_path,
            self.config.activity_csv_path,
            self.config.serial_port,
        )
        self._activity = activity
        try:
            activity.open()
            self._set_status("DISCONNECTED", last_error="not connected")
            processor = UARTProcessor(
                activity,
                self.state,
                self.config.trigger_event,
                self.config.alert_cooldown_seconds,
                self.on_qualified_event,
                self.logger,
            )
            pending = b""
            while not self._stop.is_set():
                reader = None
                try:
                    self._set_status("CONNECTING", last_error=None)
                    reader = self.reader_factory(
                        self.config.serial_port, self.config.baud_rate
                    )
                    with self._reader_lock:
                        self._reader = reader
                    self._set_status(
                        "CONNECTED", active_port=getattr(reader, "port", self.config.serial_port)
                    )
                    while not self._stop.is_set():
                        chunk = reader.read(timeout=0.25)
                        if chunk is None:
                            continue
                        if isinstance(chunk, str):
                            chunk = chunk.encode("ascii", errors="replace")
                        if chunk == b"":
                            raise OSError("serial input closed or disconnected")
                        pending += bytes(chunk)
                        while b"\n" in pending:
                            raw_line, pending = pending.split(b"\n", 1)
                            processor.process_line(
                                raw_line.decode("ascii", errors="replace").rstrip("\r")
                            )
                except BaseException as error:
                    if self._stop.is_set():
                        break
                    text = sanitize_error(error)
                    self.logger.warning("Serial connection lost; retrying: %s", text)
                    self._set_status("DISCONNECTED", last_error=text)
                finally:
                    with self._reader_lock:
                        if self._reader is reader:
                            self._reader = None
                    if reader is not None:
                        try:
                            reader.close()
                        except BaseException:
                            pass
                if not self._stop.is_set():
                    self._stop.wait(self.config.serial_retry_seconds)
        except BaseException as error:
            text = sanitize_error(error)
            self.logger.exception("Serial monitor stopped: %s", text)
            self._set_status("DISCONNECTED", last_error=text)
        finally:
            activity.close()
            self._set_status("STOPPED" if self._stop.is_set() else "DISCONNECTED")


class BotService:
    """Commands, alert text, and Telegram delivery coordination."""

    def __init__(
        self,
        config: Config,
        state: BotStateStore,
        monitor: SerialMonitor,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self.state = state
        self.monitor = monitor
        self.logger = logger or LOGGER
        self.bot: Any | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def attach_bot(self, bot: Any, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self.bot = bot
        self.loop = loop or asyncio.get_running_loop()

    def start(self) -> None:
        self.monitor.start()

    def stop(self) -> None:
        self.monitor.stop()

    def _schedule_delivery(self, event_id: int, record: DetectorRecord) -> None:
        if self.loop is None or self.bot is None:
            self.logger.warning("Qualified fall event received before Telegram delivery was ready")
            return
        future = asyncio.run_coroutine_threadsafe(
            self.deliver_event(event_id, record), self.loop
        )
        future.add_done_callback(self._delivery_done)

    def _delivery_done(self, future: Any) -> None:
        try:
            future.result()
        except BaseException as error:
            self.logger.error("Alert delivery task failed: %s", sanitize_error(error))

    def subscribe(self, chat_id: int, display_name: str | None, username: str | None) -> None:
        self.state.subscribe(chat_id, display_name, username)

    def unsubscribe(self, chat_id: int) -> None:
        self.state.unsubscribe(chat_id)

    def status_text(self) -> str:
        status = self.monitor.status
        latest = self.state.latest_detector_event()
        latest_state = latest["detector_state"] if latest is not None else "none"
        session = status.session_id if status.session_id is not None else "not started"
        error = f"; last error: {status.last_error}" if status.last_error else ""
        return (
            "Bot status\n"
            f"Serial: {status.state} (configured {status.configured_port})\n"
            f"Recording session: {session}\n"
            f"Latest detector state: {latest_state}\n"
            f"Subscribers: {self.state.subscriber_count()}{error}"
        )

    def latest_text(self) -> str:
        row = ActivityStore.latest_reading(self.config.activity_database_path)
        if row is None:
            return "No activity samples have been recorded yet."
        board_time = row.get("board_time_ms")
        board_text = "unknown" if board_time is None else str(board_time)
        return (
            "Latest activity sample\n"
            f"Sample: {row['sample_number']}\n"
            f"Laptop time: {row['timestamp_utc']}\n"
            f"Board TimeMs: {board_text}\n"
            f"Acceleration magnitude: {row['accel_magnitude_mps2']:.3f} m/s²\n"
            f"Gyroscope magnitude: {row['gyro_magnitude_dps']:.3f} dps"
        )

    @staticmethod
    def alert_text(record: DetectorRecord) -> str:
        explanation = record.explanation or "The firmware did not include an explanation."
        return (
            "Possible fall detected\n"
            f"Laptop receipt time: {record.laptop_received_at}\n"
            f"Board TimeMs: {record.board_time_ms}\n"
            f"Detector state: {record.detector_state}\n"
            f"Sensor status: {record.sensor_status}\n"
            f"Firmware explanation: {explanation}\n\n"
            "This is an experimental prototype and not a medically validated emergency system."
        )

    async def deliver_event(self, event_id: int, record: DetectorRecord) -> None:
        if self.bot is None:
            return
        message = self.alert_text(record)
        for subscriber in self.state.subscribers():
            chat_id = int(subscriber["chat_id"])
            try:
                await self.bot.send_message(chat_id=chat_id, text=message)
            except BaseException as error:
                safe = sanitize_error(error, secret=self.config.telegram_token, chat_id=chat_id)
                self.state.record_delivery(event_id, chat_id, "FAILED", safe)
                self.logger.warning("Alert delivery failed for one subscriber: %s", safe)
            else:
                self.state.record_delivery(event_id, chat_id, "DELIVERED")

    async def command_start(self, update: Any, context: Any) -> None:
        if update.message is not None:
            await update.message.reply_text(
                "This bot records STM32 activity and alerts subscribed chats when the "
                "firmware reports a possible fall.\n\n"
                "/subscribe — receive fall alerts\n"
                "/unsubscribe — stop receiving alerts\n"
                "/status — show bot and serial status\n"
                "/latest — show the newest recorded sample\n\n"
                "This is an experimental prototype, not a medically validated emergency system."
            )

    @staticmethod
    def _chat_metadata(update: Any) -> tuple[int, str | None, str | None] | None:
        chat = getattr(update, "effective_chat", None)
        if chat is None:
            return None
        user = getattr(update, "effective_user", None)
        display = getattr(user, "full_name", None) if user is not None else None
        username = getattr(user, "username", None) if user is not None else None
        return int(chat.id), display, username

    async def command_subscribe(self, update: Any, context: Any) -> None:
        metadata = self._chat_metadata(update)
        if metadata is not None and update.message is not None:
            chat_id, display, username = metadata
            self.subscribe(chat_id, display, username)
            await update.message.reply_text("Subscribed. You will receive qualified fall alerts.")

    async def command_unsubscribe(self, update: Any, context: Any) -> None:
        metadata = self._chat_metadata(update)
        if metadata is not None and update.message is not None:
            self.unsubscribe(metadata[0])
            await update.message.reply_text("Unsubscribed. You will not receive fall alerts.")

    async def command_status(self, update: Any, context: Any) -> None:
        if update.message is not None:
            await update.message.reply_text(self.status_text())

    async def command_latest(self, update: Any, context: Any) -> None:
        if update.message is not None:
            await update.message.reply_text(self.latest_text())
