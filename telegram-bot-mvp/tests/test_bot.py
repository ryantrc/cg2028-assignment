from __future__ import annotations

import asyncio
from dataclasses import replace
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from bot_core import (  # noqa: E402
    ActivityStore,
    BotService,
    BotStateStore,
    DetectorRecord,
    SerialMonitor,
    SimulatedStdinReader,
    UARTProcessor,
    parse_detector_line,
)
from config import load_config  # noqa: E402


VALID_DETECTOR = (
    "DETECTOR TimeMs=12345 State=FALL_LATCHED Alarm=1 Sensors=OK "
    "Event=POSSIBLE_FALL Sustained stillness; reset board to clear alarm."
)
SAMPLE_LINES = (
    "Sample 1 TimeMs=100 SlopeWindow=5\n"
    "Accel EWMA ASM [m/s^2]: X=1.000 Y=2.000 Z=3.000 Magnitude=3.742 "
    "MSD=NA MagnitudeSlope=NA MSDSlope=NA\n"
    "Gyro  EWMA ASM [dps]  : X=4.000 Y=5.000 Z=6.000 Magnitude=8.775 "
    "MSD=NA MagnitudeSlope=NA MSDSlope=NA\n"
)


def make_config(directory: Path, *, serial_port: str = "SIMULATED", cooldown: float = 0) -> object:
    values = {
        "TELEGRAM_BOT_TOKEN": "test-token-never-logged",
        "SERIAL_PORT": serial_port,
        "BAUD_RATE": "115200",
        "ACTIVITY_DATABASE_PATH": "activity.sqlite3",
        "ACTIVITY_CSV_PATH": "activity.csv",
        "BOT_DATABASE_PATH": "bot.sqlite3",
        "ALERT_COOLDOWN_SECONDS": str(cooldown),
        "SERIAL_RETRY_SECONDS": "0.03",
    }
    return load_config(directory, values)


class FakeReader:
    def __init__(self, chunks: list[bytes | None], port: str = "SIMULATED"):
        self.chunks = list(chunks)
        self.port = port
        self.closed = False

    def read(self, timeout: float = 0.25):
        if self.chunks:
            item = self.chunks.pop(0)
            if item is None:
                time.sleep(min(timeout, 0.01))
            return item
        time.sleep(min(timeout, 0.01))
        return None

    def close(self):
        self.closed = True


class FakeBot:
    def __init__(self, failing_chat: int | None = None):
        self.failing_chat = failing_chat
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str):
        if chat_id == self.failing_chat:
            raise RuntimeError(f"blocked chat {chat_id}")
        self.sent.append((chat_id, text))


class DummyMonitor:
    def __init__(self):
        self.status = type(
            "Status",
            (),
            {
                "state": "CONNECTED",
                "configured_port": "SIMULATED",
                "active_port": "SIMULATED",
                "last_error": None,
                "session_id": 7,
            },
        )()
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class BotCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_detector_parser_and_strict_qualification(self):
        record = parse_detector_line(VALID_DETECTOR)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.board_time_ms, 12345)
        self.assertEqual(record.explanation, "Sustained stillness; reset board to clear alarm.")
        self.assertTrue(record.qualified)

        malformed = "DETECTOR POSSIBLE_FALL State=FALL_LATCHED Alarm=1 Sensors=OK Event=POSSIBLE_FALL"
        self.assertIsNone(parse_detector_line(malformed))
        self.assertFalse(
            parse_detector_line(
                VALID_DETECTOR.replace("Event=POSSIBLE_FALL", "Event=NEAR_FALL")
            ).qualified
        )
        self.assertFalse(
            parse_detector_line(VALID_DETECTOR.replace("Alarm=1", "Alarm=0")).qualified
        )
        self.assertFalse(
            parse_detector_line(VALID_DETECTOR.replace("State=FALL_LATCHED", "State=UNCERTAIN")).qualified
        )

    def test_durable_duplicate_suppression_and_cooldown(self):
        state = BotStateStore(self.directory / "bot.sqlite3")
        try:
            first = parse_detector_line(VALID_DETECTOR)
            assert first is not None
            first_id, inserted = state.insert_detector_event(first, 1)
            self.assertTrue(inserted)
            duplicate_id, duplicate_inserted = state.insert_detector_event(first, 1)
            self.assertEqual(first_id, duplicate_id)
            self.assertFalse(duplicate_inserted)
            self.assertEqual(state.latest_detector_event()["id"], first_id)
            self.assertTrue(state.alert_allowed(first_id, 60))

            second = replace(
                first,
                board_time_ms=12346,
                durable_event_identity="v1:second",
                laptop_received_at=first.laptop_received_at,
            )
            second_id, _ = state.insert_detector_event(second, 1)
            self.assertFalse(state.alert_allowed(second_id, 60))
        finally:
            state.close()

    def test_subscribe_unsubscribe_idempotency_and_persistence(self):
        path = self.directory / "bot.sqlite3"
        state = BotStateStore(path)
        state.subscribe(42, "Test User", "tester")
        state.subscribe(42, "Test User", "tester")
        self.assertEqual(state.subscriber_count(), 1)
        state.close()

        reopened = BotStateStore(path)
        try:
            self.assertEqual([row["chat_id"] for row in reopened.subscribers()], [42])
            reopened.unsubscribe(42)
            reopened.unsubscribe(42)
            self.assertEqual(reopened.subscriber_count(), 0)
        finally:
            reopened.close()

    def test_delivery_result_logging_and_failed_subscriber_does_not_block(self):
        state = BotStateStore(self.directory / "bot.sqlite3")
        monitor = DummyMonitor()
        config = make_config(self.directory)
        service = BotService(config, state, monitor)
        bot = FakeBot(failing_chat=2)
        service.attach_bot(bot, asyncio.new_event_loop())
        try:
            state.subscribe(1, "one", "one")
            state.subscribe(2, "two", "two")
            record = parse_detector_line(VALID_DETECTOR)
            assert record is not None
            event_id, _ = state.insert_detector_event(record, 7)
            asyncio.run(service.deliver_event(event_id, record))
            self.assertEqual([chat for chat, _ in bot.sent], [1])
            rows = state.delivery_rows()
            self.assertEqual([row["delivery_status"] for row in rows], ["DELIVERED", "FAILED"])
            self.assertIn("<chat>", rows[1]["sanitized_error_text"])
            self.assertIn("Possible fall detected", bot.sent[0][1])
            self.assertIn("experimental prototype", bot.sent[0][1])
        finally:
            state.close()

    def test_complete_sample_persistence_and_latest_view(self):
        state = BotStateStore(self.directory / "bot.sqlite3")
        activity = ActivityStore(self.directory / "activity.sqlite3", self.directory / "activity.csv", "SIMULATED")
        activity.open()
        try:
            processor = UARTProcessor(activity, state)
            for line in SAMPLE_LINES.splitlines():
                processor.process_line(line)
            latest = ActivityStore.latest_reading(self.directory / "activity.sqlite3")
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest["sample_number"], 1)
            self.assertEqual(latest["board_time_ms"], 100)
            self.assertEqual(len(latest), 22)
            self.assertEqual(len((self.directory / "activity.csv").read_text(encoding="utf-8").splitlines()[0].split(",")), 22)
        finally:
            activity.close()
            state.close()

    def test_latest_handles_empty_readings_view(self):
        state = BotStateStore(self.directory / "bot.sqlite3")
        service = BotService(make_config(self.directory), state, DummyMonitor())
        try:
            self.assertIn("No activity samples", service.latest_text())
        finally:
            state.close()

    def test_simulation_uses_production_monitor_processing_path(self):
        state = BotStateStore(self.directory / "bot.sqlite3")
        config = make_config(self.directory, cooldown=0)
        received = threading.Event()
        factory_calls: list[int] = []

        def factory(port, baud):
            factory_calls.append(1)
            return FakeReader([(SAMPLE_LINES + VALID_DETECTOR + "\n").encode(), b""])

        monitor = SerialMonitor(
            config,
            state,
            on_qualified_event=lambda event_id, record: received.set(),
            reader_factory=factory,
        )
        monitor.start()
        try:
            self.assertTrue(received.wait(2), "simulation did not process the fall line")
            monitor.stop()
            with sqlite3.connect(self.directory / "activity.sqlite3") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
            self.assertEqual(state.latest_detector_event()["event_name"], "POSSIBLE_FALL")
            self.assertGreaterEqual(len(factory_calls), 1)
        finally:
            monitor.stop()
            state.close()

    def test_disconnection_retries_and_clean_shutdown_ends_session(self):
        state = BotStateStore(self.directory / "bot.sqlite3")
        config = make_config(self.directory)
        attempts: list[int] = []
        readers: list[FakeReader] = []

        def factory(port, baud):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("device disconnected")
            reader = FakeReader([None])
            readers.append(reader)
            return reader

        monitor = SerialMonitor(config, state, reader_factory=factory)
        monitor.start()
        try:
            deadline = time.monotonic() + 2
            while len(attempts) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(len(attempts), 2)
            self.assertIn(monitor.status.state, {"CONNECTED", "DISCONNECTED", "CONNECTING"})
            monitor.stop()
            self.assertEqual(monitor.status.state, "STOPPED")
            self.assertTrue(readers[0].closed)
            with sqlite3.connect(self.directory / "activity.sqlite3") as connection:
                self.assertIsNotNone(connection.execute("SELECT ended_at_utc FROM sessions").fetchone()[0])
        finally:
            monitor.stop()
            state.close()

    def test_simulated_stdin_reader_preserves_line_bytes(self):
        reader = SimulatedStdinReader(io.BytesIO(b"one\npartial"))
        try:
            self.assertEqual(reader.read(1), b"one\n")
            self.assertEqual(reader.read(1), b"partial")
        finally:
            reader.close()


if __name__ == "__main__":
    unittest.main()
