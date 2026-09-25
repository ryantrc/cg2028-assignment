#!/usr/bin/env python3
"""Record the STM32's printed EWMA readings to SQLite and CSV on macOS/Linux.

Uses Python's standard library only. Run with --help for examples and options.
"""

import argparse
import csv
from datetime import datetime, timezone
import glob
import math
import os
from pathlib import Path
import re
import select
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from detector_verdicts import VerdictRecorder, parse_diagnostic


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DURATION_SECONDS = 30
MEASUREMENTS = (
    "accel_x_mps2", "accel_y_mps2", "accel_z_mps2", "accel_magnitude_mps2",
    "gyro_x_dps", "gyro_y_dps", "gyro_z_dps", "gyro_magnitude_dps",
)
LEGACY_MEASUREMENTS = tuple(field.replace("magnitude", "avg") for field in MEASUREMENTS)
RETIRED_RATE_FIELDS = (
    "accel_avg_rate", "accel_msd_rate", "gyro_avg_rate", "gyro_msd_rate",
)
CUMULATIVE_METRIC_FIELDS = (
    "board_time_ms", "accel_msd", "accel_avg_rate", "accel_msd_rate",
    "gyro_msd", "gyro_avg_rate", "gyro_msd_rate",
)
METRIC_FIELDS = ("board_time_ms", "accel_msd", "gyro_msd")
SLOPE_FIELDS = (
    "slope_window_samples", "accel_magnitude_slope", "accel_msd_slope",
    "gyro_magnitude_slope", "gyro_msd_slope",
)
LEGACY_SLOPE_FIELDS = tuple(field.replace("magnitude", "avg") for field in SLOPE_FIELDS)
BASE_FIELDS = (
    "record_id", "session_id", "timestamp_utc", "activity", "notes",
    "sample_number",
)
LEGACY_AVERAGE_FIELDS = ("accel_avg_mps2", "gyro_avg_dps", "accel_avg_slope", "gyro_avg_slope")
LEGACY_CSV_FIELDS = (*BASE_FIELDS, *LEGACY_MEASUREMENTS)
CUMULATIVE_CSV_FIELDS = (*LEGACY_CSV_FIELDS, *CUMULATIVE_METRIC_FIELDS)
PREVIOUS_CSV_FIELDS = (*CUMULATIVE_CSV_FIELDS, *LEGACY_SLOPE_FIELDS)
AVERAGE_CSV_FIELDS = (*LEGACY_CSV_FIELDS, *METRIC_FIELDS, *LEGACY_SLOPE_FIELDS)
CSV_FIELDS = (*BASE_FIELDS, *MEASUREMENTS, *METRIC_FIELDS, *SLOPE_FIELDS)
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
VALUES = rf"X=\s*({NUMBER})\s+Y=\s*({NUMBER})\s+Z=\s*({NUMBER})\s+Magnitude=\s*({NUMBER})"
METRIC_VALUE = rf"(?:{NUMBER}|NA)"
METRICS = (rf"(?:\s+MSD=\s*({METRIC_VALUE})\s+AvgRate=\s*({METRIC_VALUE})"
           rf"\s+MSDRate=\s*({METRIC_VALUE}))?")
SLOPE_METRICS = (rf"\s+MSD=\s*({METRIC_VALUE})\s+MagnitudeSlope=\s*({METRIC_VALUE})"
                 rf"\s+MSDSlope=\s*({METRIC_VALUE})")
ACCEL = re.compile(r"Accel\s+EWMA\s+ASM\s+\[m/s\^2\]\s*:\s*" + VALUES + METRICS)
GYRO = re.compile(r"Gyro\s+EWMA\s+ASM\s+\[dps\]\s*:\s*" + VALUES + METRICS)
ACCEL_SLOPE = re.compile(r"Accel\s+EWMA\s+ASM\s+\[m/s\^2\]\s*:\s*" + VALUES + SLOPE_METRICS)
GYRO_SLOPE = re.compile(r"Gyro\s+EWMA\s+ASM\s+\[dps\]\s*:\s*" + VALUES + SLOPE_METRICS)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SampleParser:
    """Emit one row only after a complete Sample/Accel/Gyro sequence."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = None
        self.accel = None
        self.board_time_ms = None
        self.accel_metrics = None
        self.slope_window_samples = None

    def _sensor_values(self, match):
        if not match:
            return None
        groups = match.groups()
        extended = groups[4] is not None
        if extended != (self.board_time_ms is not None):
            return None
        values = tuple(map(float, groups[:4]))
        if not all(map(math.isfinite, values)) or values[3] < 0:
            return None
        metrics = None
        if extended:
            metrics = tuple(None if value == "NA" else float(value) for value in groups[4:])
            if any(value is not None and not math.isfinite(value) for value in metrics):
                return None
            # MSD and the old absolute-change rates cannot be negative. New
            # regression slopes can: a negative slope means a falling trend.
            nonnegative = metrics[:1] if self.slope_window_samples is not None else metrics
            if any(value is not None and value < 0 for value in nonnegative):
                return None
        return values, metrics

    def feed(self, line):
        line = line.strip()
        if line.startswith(("Accel", "Gyro")) and re.search(r"\bAvg\s*=", line):
            self.reset()
            raise ValueError(
                "The board is still sending Avg. Rebuild and flash CG2028_Assignment "
                "in CubeIDE, then resume it: the updated firmware prints Magnitude "
                "and MagnitudeSlope. Saved recordings are retained."
            )
        if line.startswith("Sample"):
            self.reset()
            header = re.fullmatch(
                r"Sample\s+(\d{1,10})(?:\s+TimeMs=(\d{1,10})(?:\s+SlopeWindow=(\d{1,10}))?)?",
                line,
            )
            if header and int(header.group(1)) <= 0xFFFFFFFF:
                self.pending = int(header.group(1))
                if header.group(2) is not None:
                    self.board_time_ms = int(header.group(2))
                    if self.board_time_ms > 0xFFFFFFFF:
                        self.reset()
                        return None
                if header.group(3) is not None:
                    self.slope_window_samples = int(header.group(3))
                    if not 2 <= self.slope_window_samples <= 0xFFFFFFFF:
                        self.reset()
            return None
        if self.pending is None:
            return None
        if line.startswith("Accel"):
            pattern = ACCEL_SLOPE if self.slope_window_samples is not None else ACCEL
            parsed = self._sensor_values(pattern.fullmatch(line))
            if parsed is None or self.accel is not None:
                self.reset()
            else:
                self.accel, self.accel_metrics = parsed
            return None
        if line.startswith("Gyro"):
            pattern = GYRO_SLOPE if self.slope_window_samples is not None else GYRO
            parsed = self._sensor_values(pattern.fullmatch(line))
            result = None
            if parsed is not None and self.accel is not None:
                gyro, gyro_metrics = parsed
                result = {"sample_number": self.pending}
                result.update(zip(MEASUREMENTS, self.accel + gyro))
                if self.slope_window_samples is not None:
                    result.update({
                        "board_time_ms": self.board_time_ms,
                        "accel_msd": self.accel_metrics[0],
                        "gyro_msd": gyro_metrics[0],
                    })
                    result.update(zip(SLOPE_FIELDS, (
                        self.slope_window_samples, *self.accel_metrics[1:], *gyro_metrics[1:]
                    )))
                elif self.board_time_ms is not None:
                    result.update(zip(CUMULATIVE_METRIC_FIELDS, (
                        self.board_time_ms, *self.accel_metrics, *gyro_metrics
                    )))
            self.reset()
            return result
        # Ignore unrelated diagnostic messages; never synthesize missing axes.
        return None


class RecordingDatabase:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        if self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='verdict_metadata'"
        ).fetchone():
            self.connection.close()
            raise ValueError("This is a test-verdict database. Use --test or choose another --db path.")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        try:
            self._initialize()
        except BaseException:
            self.connection.close()
            raise

    def _initialize(self):
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at_utc TEXT NOT NULL,
                ended_at_utc TEXT,
                activity TEXT NOT NULL,
                notes TEXT NOT NULL,
                port TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                timestamp_utc TEXT NOT NULL,
                sample_number INTEGER NOT NULL,
                accel_x_mps2 REAL NOT NULL,
                accel_y_mps2 REAL NOT NULL,
                accel_z_mps2 REAL NOT NULL,
                accel_magnitude_mps2 REAL NOT NULL,
                gyro_x_dps REAL NOT NULL,
                gyro_y_dps REAL NOT NULL,
                gyro_z_dps REAL NOT NULL,
                gyro_magnitude_dps REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS samples_session ON samples(session_id);
        """)
        existing = {row[1] for row in self.connection.execute("PRAGMA table_info(samples)")}
        retired = [field for field in RETIRED_RATE_FIELDS if field in existing]
        if retired:
            populated = " OR ".join(f"{field} IS NOT NULL" for field in retired)
            if self.connection.execute(f"SELECT 1 FROM samples WHERE {populated} LIMIT 1").fetchone():
                raise ValueError(
                    "This database contains recorded cumulative rates in retired columns. "
                    "They have been preserved. Choose new --db and --csv files for the current firmware."
                )
        old_average = [field for field in LEGACY_AVERAGE_FIELDS if field in existing]
        backup = self._backup_original() if old_average else None
        with self.connection:
            # sqlite3's context manager does not begin a transaction for DDL.
            self.connection.execute("BEGIN")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS recording_metadata (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            )""")
            self.connection.execute("DROP VIEW IF EXISTS readings")
            for field in retired:
                self.connection.execute(f"ALTER TABLE samples DROP COLUMN {field}")
            for field in (*MEASUREMENTS, *METRIC_FIELDS, *SLOPE_FIELDS):
                if field not in existing:
                    kind = "INTEGER" if field in ("board_time_ms", "slope_window_samples") else "REAL"
                    self.connection.execute(f"ALTER TABLE samples ADD COLUMN {field} {kind}")
            if old_average:
                self._recompute_magnitudes()
                for field in old_average:
                    self.connection.execute(f"ALTER TABLE samples DROP COLUMN {field}")
                last_id = self.connection.execute("SELECT COALESCE(MAX(id), 0) FROM samples").fetchone()[0]
                self.connection.executemany(
                    "INSERT OR REPLACE INTO recording_metadata (key, value) VALUES (?, ?)",
                    [("legacy_average_backup", str(backup.relative_to(self.path.parent))),
                     ("magnitude_recomputed_through_record_id", str(last_id)),
                     ("magnitude_migration", "Derived from saved rounded XYZ; slopes use contiguous per-session board timestamps.")],
                )
            fields = ", ".join(f"p.{field}" for field in (*MEASUREMENTS, *METRIC_FIELDS, *SLOPE_FIELDS))
            self.connection.execute(f"""CREATE VIEW readings AS
                SELECT p.id AS record_id, p.session_id, p.timestamp_utc,
                       s.activity, s.notes, p.sample_number, {fields}
                FROM samples p JOIN sessions s ON p.session_id = s.id
            """)

    def _backup_original(self):
        """Keep every old value, including Avg, before the transactional migration."""
        directory = self.path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=f"{self.path.stem}.before-magnitude-{stamp}-",
            suffix=".sqlite3", delete=False,
        ) as handle:
            backup = Path(handle.name)
        try:
            destination = sqlite3.connect(backup)
            try:
                self.connection.backup(destination)
                # Make the archive a self-contained file, without WAL sidecars.
                destination.execute("PRAGMA journal_mode = DELETE")
            finally:
                destination.close()
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        return backup

    def _recompute_magnitudes(self):
        """Historical data has only printed XYZ; never relabel an Avg or AvgSlope."""
        previous = None
        history = []
        elapsed_ms = 0
        rows = self.connection.execute("SELECT * FROM samples ORDER BY session_id, id").fetchall()
        for row in rows:
            magnitudes = tuple(math.sqrt(sum(float(row[f"{sensor}_{axis}_{unit}"]) ** 2
                                            for axis in "xyz"))
                               for sensor, unit in (("accel", "mps2"), ("gyro", "dps")))
            if not all(map(math.isfinite, magnitudes)):
                raise ValueError("Existing XYZ values are not finite; migration cancelled. Original backup retained.")
            timestamp = row["board_time_ms"]
            window = row["slope_window_samples"]
            valid_time = timestamp is not None and 0 <= timestamp <= 0xFFFFFFFF
            valid_window = window is not None and window >= 2
            delta_ms = None
            if previous is not None and valid_time and previous["board_time_ms"] is not None:
                delta_ms = (timestamp - previous["board_time_ms"]) & 0xFFFFFFFF
            continuous = (
                previous is not None and row["session_id"] == previous["session_id"]
                and row["sample_number"] == previous["sample_number"] + 1
                and window == previous["slope_window_samples"]
                and delta_ms is not None and 0 < delta_ms < 0x80000000
            )
            if not continuous:
                history = []
                elapsed_ms = 0
            else:
                elapsed_ms += delta_ms
            slopes = (None, None)
            if valid_time and valid_window:
                history.append((elapsed_ms, *magnitudes))
                history = history[-window:]
                if len(history) == window:
                    seconds = [(point[0] - history[0][0]) / 1000.0 for point in history]
                    mean_t = sum(seconds) / window
                    centered = [value - mean_t for value in seconds]
                    denominator = sum(value * value for value in centered)
                    if denominator > 0:
                        means = [sum(point[axis] for point in history) / window for axis in (1, 2)]
                        slopes = tuple(
                            sum(t * (point[axis] - mean)
                                for t, point in zip(centered, history)) / denominator
                            for axis, mean in zip((1, 2), means)
                        )
            else:
                history = []
            self.connection.execute(
                """UPDATE samples SET accel_magnitude_mps2=?, gyro_magnitude_dps=?,
                       accel_magnitude_slope=?, gyro_magnitude_slope=? WHERE id=?""",
                (*magnitudes, *slopes, row["id"]),
            )
            previous = row

    def csv_reference_rows(self, fields):
        """Validate old Avg CSVs against their preserved original, even after restart."""
        if not any(field in LEGACY_AVERAGE_FIELDS for field in fields):
            yield from self.connection.execute("SELECT * FROM readings ORDER BY record_id")
            return
        item = self.connection.execute(
            "SELECT value FROM recording_metadata WHERE key='legacy_average_backup'"
        ).fetchone()
        backup = self.path.parent / item[0] if item else None
        if backup is None or not backup.is_file():
            raise ValueError("Cannot validate an old Avg CSV without its original database backup. Choose a new --csv path.")
        original = sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
        original.row_factory = sqlite3.Row
        try:
            old_rows = iter(original.execute("SELECT * FROM readings ORDER BY record_id"))
            for current in self.connection.execute("SELECT * FROM readings ORDER BY record_id"):
                old = next(old_rows, None)
                if old is None:
                    raise ValueError("Old Avg CSV extends beyond the preserved original database. Choose a new --csv path.")
                shared = set(current.keys()) & set(old.keys())
                if any(current[field] != old[field] for field in shared):
                    raise ValueError("Existing database differs from its original backup. Choose a new --csv path.")
                # Empty retired fields may be absent in a newer legacy backup.
                yield {field: old[field] if field in old.keys() else None for field in fields}
        finally:
            original.close()

    def start_session(self, activity, notes, port):
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO sessions (started_at_utc, activity, notes, port) VALUES (?, ?, ?, ?)",
                (utc_now(), activity, notes, port),
            )
        return cursor.lastrowid

    def append_sample(self, session_id, sample):
        if any(field in sample for field in LEGACY_AVERAGE_FIELDS):
            raise ValueError("The board is sending Avg. Rebuild and flash the Magnitude/MagnitudeSlope firmware.")
        if any(field in sample for field in RETIRED_RATE_FIELDS):
            raise ValueError(
                "The board is sending retired AvgRate/MSDRate values. "
                "Rebuild and flash the current firmware that prints MagnitudeSlope/MSDSlope. "
                "Already saved readings are retained."
            )
        columns = ("session_id", "timestamp_utc", "sample_number", *MEASUREMENTS,
                   *METRIC_FIELDS, *SLOPE_FIELDS)
        values = (session_id, utc_now(), sample["sample_number"],
                  *(sample[key] for key in MEASUREMENTS),
                  *(sample.get(key) for key in (*METRIC_FIELDS, *SLOPE_FIELDS)))
        with self.connection:
            cursor = self.connection.execute(
                f"INSERT INTO samples ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
        return dict(self.connection.execute(
            "SELECT * FROM readings WHERE record_id = ?", (cursor.lastrowid,)
        ).fetchone())

    def finish_session(self, session_id):
        with self.connection:
            self.connection.execute(
                "UPDATE sessions SET ended_at_utc = ? WHERE id = ?", (utc_now(), session_id)
            )

    def append_diagnostic(self, session_id, line):
        """Keep free-mode messages in its own DB; malformed lines cannot stop it."""
        try:
            parsed = parse_diagnostic(line)
            error = None
        except ValueError as problem:
            parsed, error = None, str(problem)
        with self.connection:
            self.connection.execute("""CREATE TABLE IF NOT EXISTS detector_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                timestamp_utc TEXT NOT NULL, raw_line TEXT NOT NULL,
                parse_valid INTEGER NOT NULL, parse_error TEXT,
                board_time_ms INTEGER, state TEXT, event TEXT, alarm INTEGER, sensors TEXT
            )""")
            fields = ("board_time_ms", "state", "event", "alarm", "sensors")
            self.connection.execute("""INSERT INTO detector_events
                (session_id,timestamp_utc,raw_line,parse_valid,parse_error,
                 board_time_ms,state,event,alarm,sensors) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (session_id, utc_now(), line, int(parsed is not None), error,
                 *((parsed or {}).get(field) for field in fields)))
        return parsed

    def close(self):
        self.connection.close()


class CSVRecorder:
    """Keep the CSV in sync with the database, including recovery after a crash."""

    def __init__(self, path, database):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        expected_last = 0
        needs_newline = False
        upgrade_csv = False
        # A generated CSV must have the same row IDs and field values as this DB.
        # Verify existing rows before appending so a different recording cannot
        # silently be mixed in. Missing tail rows are recovered from SQLite.
        if self.path.exists() and self.path.stat().st_size:
            with self.path.open(newline="", encoding="utf-8") as existing:
                reader = csv.DictReader(existing)
                if reader.fieldnames not in (
                    list(CSV_FIELDS), list(PREVIOUS_CSV_FIELDS),
                    list(AVERAGE_CSV_FIELDS), list(CUMULATIVE_CSV_FIELDS), list(LEGACY_CSV_FIELDS)
                ):
                    raise ValueError(f"CSV header does not match: {self.path}. Choose a new --csv path.")
                upgrade_csv = reader.fieldnames != list(CSV_FIELDS)
                db_rows = database.csv_reference_rows(reader.fieldnames)
                try:
                    for row in reader:
                        db_row = next(db_rows, None)
                        if db_row is None or row != {
                            key: "" if db_row[key] is None else str(db_row[key])
                            for key in reader.fieldnames
                        }:
                            raise ValueError(f"CSV differs from this database: {self.path}. Choose a new --csv path to export its records.")
                        expected_last = db_row["record_id"]
                finally:
                    db_rows.close()
            with self.path.open("rb") as existing:
                existing.seek(-1, os.SEEK_END)
                needs_newline = existing.read(1) != b"\n"
        if upgrade_csv:
            # Retain the original columns and values for inspection. This copy
            # happens only after the entire old CSV passes validation.
            backup_dir = self.path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            with tempfile.NamedTemporaryFile(
                dir=backup_dir, prefix=f"{self.path.stem}.before-magnitude-{stamp}-",
                suffix=".csv", delete=False,
            ) as backup:
                with self.path.open("rb") as original:
                    shutil.copyfileobj(original, backup)
                backup.flush()
                os.fsync(backup.fileno())
            # Replace only after validating the old CSV against its database.
            # A same-directory temporary file makes the header upgrade atomic.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", newline="", encoding="utf-8", dir=self.path.parent,
                    prefix=self.path.name + ".", suffix=".tmp", delete=False
                ) as handle:
                    temporary = Path(handle.name)
                    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                    writer.writeheader()
                    for row in database.connection.execute("SELECT * FROM readings ORDER BY record_id"):
                        writer.writerow(dict(row))
                        expected_last = row["record_id"]
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                needs_newline = False
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        self.handle = self.path.open("a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.handle, fieldnames=CSV_FIELDS)
        if self.path.stat().st_size == 0:
            self.writer.writeheader()
        elif needs_newline:
            self.handle.write("\n")
        for row in database.connection.execute(
            "SELECT * FROM readings WHERE record_id > ? ORDER BY record_id", (expected_last,)
        ):
            self.writer.writerow(dict(row))
        self.handle.flush()

    def append(self, row):
        self.writer.writerow(row)
        self.handle.flush()

    def close(self):
        self.handle.close()


def serial_port(requested):
    if requested != "auto":
        path = str(Path(requested).expanduser())
        if not Path(path).exists():
            raise FileNotFoundError(f"Serial port not found: {path}. Use --port auto to detect its current name.")
        return path
    ports = sorted({path for pattern in (
        "/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/ttyACM*", "/dev/ttyUSB*"
    ) for path in glob.glob(pattern)})
    if not ports:
        raise FileNotFoundError("No USB serial port found. Connect the board using its ST-LINK USB connector.")
    if len(ports) > 1:
        raise ValueError("Multiple serial ports found; choose one with --port:\n  " + "\n  ".join(ports))
    return ports[0]


class SerialReader:
    """115200 8N1 serial input with no third-party Python dependencies."""

    def __init__(self, path):
        import fcntl
        import termios

        lsof = shutil.which("lsof")
        if lsof:
            result = subprocess.run([lsof, "-n", "-Fpc", path], capture_output=True, text=True)
            if result.stdout.strip():
                owners = []
                screen_pid = None
                pid = "unknown"
                for field in result.stdout.splitlines():
                    if field.startswith("p"):
                        pid = field[1:]
                    elif field.startswith("c"):
                        owners.append(f"{field[1:]} (PID {pid})")
                        if field[1:] == "screen":
                            screen_pid = pid
                hint = (f"Close that session from another Terminal tab: screen -S {screen_pid} -X quit"
                        if screen_pid else "Close the other serial viewer first.")
                raise ValueError(f"Serial port is already in use by {', '.join(owners) or 'another process'}.\n{hint}")
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.original = None
        self.termios = termios
        self.fcntl = fcntl
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.original = termios.tcgetattr(self.fd)
            settings = termios.tcgetattr(self.fd)
            settings[0] = 0
            settings[1] = 0
            settings[2] = termios.CLOCAL | termios.CREAD | termios.CS8
            settings[3] = 0
            settings[4] = termios.B115200
            settings[5] = termios.B115200
            settings[6][termios.VMIN] = 0
            settings[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
            if hasattr(termios, "TIOCEXCL"):
                fcntl.ioctl(self.fd, termios.TIOCEXCL)
            # Discard bytes left queued by an earlier serial viewer/run. A
            # previous run's decision must not be recorded as a new event.
            termios.tcflush(self.fd, termios.TCIFLUSH)
        except BaseException:
            self.close()
            raise

    def read(self, timeout=1.0):
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            if not Path(self.path).exists():
                raise OSError("Serial device disconnected")
            return None
        chunk = os.read(self.fd, 4096)
        if not chunk:
            raise OSError("Serial device disconnected")
        return chunk

    def close(self):
        try:
            if self.original is not None:
                self.termios.tcsetattr(self.fd, self.termios.TCSANOW, self.original)
        except (OSError, self.termios.error):
            pass
        try:
            if hasattr(self.termios, "TIOCNXCL"):
                self.fcntl.ioctl(self.fd, self.termios.TIOCNXCL)
        except OSError:
            pass
        finally:
            os.close(self.fd)


def prepare_console():
    """Enable Ctrl+C and normal line output on an interactive console.

    A shell can pass along damaged terminal modes from an earlier program.
    Repair only the settings this logger needs; leave redirected streams alone.
    Keep these corrected modes on exit so the shell can retain the repair.
    This is separate from configuring the board's serial device.
    """
    import termios

    for stream, is_input in ((sys.stdin, True), (sys.stdout, False), (sys.stderr, False)):
        if stream is None or not stream.isatty():
            continue
        fd = stream.fileno()
        original = termios.tcgetattr(fd)
        settings = original[:]
        settings[6] = original[6][:]
        if is_input:
            settings[3] |= termios.ISIG
            settings[6][termios.VINTR] = b"\x03"
        else:
            settings[1] |= termios.OPOST | termios.ONLCR
        if settings != original:
            termios.tcsetattr(fd, termios.TCSANOW, settings)


class RecordingProgress:
    """One live terminal line, or plain snapshots when output is redirected."""

    def __init__(self, stream=None):
        self.stream = sys.stdout if stream is None else stream
        self.interactive = self.stream.isatty()
        self.count = 0
        self.state = "UNKNOWN"
        self.alarm = "UNKNOWN"
        self.last_line = None
        self.visible = False
        self.started = False

    def update(self, count=None, diagnostic=None):
        if count is not None:
            self.count = count
        if diagnostic is not None:
            self.state = diagnostic["state"]
            self.alarm = diagnostic["alarm"]
        self.started = True
        self._render()

    def _render(self, force=False):
        line = f"reading no. = {self.count} State = {self.state} Alarm = {self.alarm}"
        if not force and line == self.last_line:
            return
        if self.interactive:
            # Clear the whole old line, including a longer previous state or
            # an echoed Ctrl+C, then redraw without scrolling the terminal.
            self.stream.write("\r\x1b[2K" + line)
            self.visible = True
        else:
            self.stream.write(line + "\n")
        self.stream.flush()
        self.last_line = line

    def message(self, text, *, file=None):
        if self.interactive and self.visible:
            self.stream.write("\r\x1b[2K")
            self.stream.flush()
            self.visible = False
        print(text, file=self.stream if file is None else file, flush=True)
        if self.interactive and self.started:
            self._render(force=True)

    def finish(self, count=None):
        if not self.started:
            return
        self.update(count=count)
        if self.interactive and self.visible:
            self.stream.write("\n")
            self.stream.flush()
            self.visible = False


def record(args):
    if sys.platform == "win32":
        raise ValueError("Serial recording currently supports macOS and Linux. SQLite/CSV files are portable.")
    validate_output_paths([args.db, args.csv])
    mode = getattr(args, "mode", "calibration")
    prepare_console()
    path = serial_port(args.port)
    reader = SerialReader(path)
    database = None
    csv_file = None
    verdicts = None
    verdict_run_id = None
    session_id = None
    count = 0
    stop_reason = "ERROR"
    fall_detected = False
    progress = RecordingProgress()
    try:
        if mode == "test":
            verdicts = VerdictRecorder(args.db, args.csv)
            verdict_run_id = verdicts.start_test(
                args.activity, args.verdict, args.notes, utc_now(), path
            )
            run = verdicts.get_run(verdict_run_id)
            session_id, run_label = run["session_id"], run["activity"]
        else:
            database = RecordingDatabase(args.db)
            set_recording_dataset(database, "prototype" if mode == "free" else "calibration")
            csv_file = CSVRecorder(args.csv, database)
            run_label = args.activity or mode
            session_id = database.start_session(run_label, args.notes, path)
        start = time.monotonic()
        deadline = start + args.duration if args.duration else None
        duration_reached = False
        print(f"Recording session {session_id} ({mode}): {run_label} from {path} at 115200 baud", flush=True)
        print(f"SQLite: {Path(args.db).expanduser().resolve()}\nCSV:    {Path(args.csv).expanduser().resolve()}", flush=True)
        if verdicts is not None:
            print(f"Expected verdict: {args.verdict} (does not control the detector)", flush=True)
        if args.duration:
            print(f"Recording for {args.duration} seconds. Press Ctrl+C to stop early.", flush=True)
        else:
            print("Running until a fall is detected. Press Ctrl+C to stop early.", flush=True)
        print("Waiting for complete Sample / Accel / Gyro readings...", flush=True)
        progress.update(count=0)
        parser = SampleParser()
        pending_bytes = b""
        last_sample_time = start
        last_number = None
        idle_notice = False
        while not args.samples or count < args.samples:
            timeout = 1.0
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    duration_reached = True
                    break
                timeout = min(timeout, remaining)
            try:
                chunk = reader.read(timeout=timeout)
            except OSError as error:
                stop_reason = "DISCONNECTED"
                progress.message(f"Serial connection lost: {error}. Saved rows are retained. Reconnect and rerun the command.", file=sys.stderr)
                return 1
            if deadline is not None and time.monotonic() >= deadline:
                duration_reached = True
                break
            if chunk:
                pending_bytes += chunk
                while b"\n" in pending_bytes:
                    if deadline is not None and time.monotonic() >= deadline:
                        duration_reached = True
                        break
                    line, pending_bytes = pending_bytes.split(b"\n", 1)
                    sample = parser.feed(line.decode("ascii", errors="replace"))
                    if sample is None:
                        if line.startswith(b"WARNING:"):
                            progress.message(line.decode("ascii", errors="replace").strip(), file=sys.stderr)
                        elif line.startswith(b"DETECTOR "):
                            diagnostic = line.decode("ascii", errors="replace").strip()
                            try:
                                parsed = parse_diagnostic(diagnostic)
                            except ValueError:
                                parsed = None
                            if verdicts is not None:
                                verdicts.append_line(verdict_run_id, diagnostic, utc_now())
                            elif mode == "free":
                                database.append_diagnostic(session_id, diagnostic)
                            if parsed is None:
                                progress.message("WARNING: Invalid detector diagnostic ignored for display: " + diagnostic,
                                                 file=sys.stderr)
                            else:
                                # State can change after the final sensor frame,
                                # including the alarm that stops a free run.
                                progress.update(diagnostic=parsed)
                                if mode == "free" and parsed["alarm"] == 1 and parsed["state"] == "FALL_LATCHED":
                                    fall_detected = True
                                    stop_reason = "FALL_DETECTED"
                                    if parsed["event"] == "POSSIBLE_FALL":
                                        progress.message("Fall detected. Saving data and stopping the free run.")
                                    else:
                                        progress.message("Fall detected: the board reports an existing latched alarm. "
                                                         "Stopping; reset the board before a fresh run.")
                                    break
                                if parsed["event"] != "STATUS":
                                    progress.message(diagnostic)
                        continue
                    if last_number is not None and sample["sample_number"] <= last_number:
                        progress.message("Board sample counter restarted; keeping new rows with unique database IDs.")
                    last_number = sample["sample_number"]
                    if verdicts is not None:
                        verdicts.append_sample(verdict_run_id, sample, utc_now())
                    else:
                        row = database.append_sample(session_id, sample)
                        csv_file.append(row)
                    count += 1
                    last_sample_time = time.monotonic()
                    idle_notice = False
                    progress.update(count=count)
                    if args.samples and count >= args.samples:
                        break
                if len(pending_bytes) > 65536:
                    pending_bytes = b""
                    parser.reset()
            if duration_reached or fall_detected:
                break
            if not idle_notice and time.monotonic() - last_sample_time > 10:
                progress.message("No complete sample for 10 seconds. Resume the board in CubeIDE and check that its output includes Magnitude.")
                idle_notice = True
        if duration_reached:
            stop_reason = "DURATION"
            progress.message(f"{args.duration}-second recording complete. Keeping all saved readings.")
        elif not fall_detected:
            stop_reason = "SAMPLE_LIMIT"
    except KeyboardInterrupt:
        stop_reason = "INTERRUPTED"
        progress.message("Stopping recording.")
    finally:
        # Always close every resource even if finalizing one output fails.
        # Each database has already committed its received rows independently.
        try:
            try:
                reader.close()
            finally:
                try:
                    if csv_file is not None:
                        csv_file.close()
                finally:
                    try:
                        if database is not None:
                            try:
                                if session_id is not None:
                                    # SQLite may have committed the final sample
                                    # before a CSV write failed or Ctrl+C arrived.
                                    # Report the durable count, not loop progress.
                                    count = database.connection.execute(
                                        "SELECT COUNT(*) FROM samples WHERE session_id = ?", (session_id,)
                                    ).fetchone()[0]
                                    database.finish_session(session_id)
                            finally:
                                database.close()
                    finally:
                        if verdicts is not None:
                            try:
                                if verdict_run_id is not None:
                                    count = verdicts.count_samples(verdict_run_id)
                                    verdicts.finish_run(verdict_run_id, utc_now(), stop_reason, count)
                            finally:
                                verdicts.close()
        finally:
            progress.finish(count=count)
        if session_id is not None:
            print(f"Session {session_id} ended. Saved {count} sample(s) this run.", flush=True)
    return 0


def metric_text(sample, sensor):
    if "board_time_ms" not in sample:
        return ""
    values = []
    if "slope_window_samples" in sample:
        labels = (("msd", "MSD"), ("magnitude_slope", "MagnitudeSlope"), ("msd_slope", "MSDSlope"))
    else:
        labels = (("msd", "MSD"), ("avg_rate", "AvgRate"), ("msd_rate", "MSDRate"))
    for suffix, label in labels:
        value = sample[f"{sensor}_{suffix}"]
        values.append(f" {label}=" + ("NA" if value is None else f"{value:.6g}"))
    return "".join(values)


def show_summary(path, mode="calibration"):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No recordings yet: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        if mode == "test":
            fields = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            expected = "expected_verdict" if "expected_verdict" in fields else "NULL"
            rows = connection.execute(f"SELECT session_id,activity,{expected},observed_verdict,sample_count "
                                      "FROM runs ORDER BY run_id")
            print("Session | Name | Expected | Observed | Samples")
            for row in rows:
                print(" | ".join("" if value is None else str(value) for value in row))
            return
        rows = connection.execute("""
            SELECT s.id, s.activity, s.started_at_utc, COUNT(p.id)
            FROM sessions s LEFT JOIN samples p ON p.session_id = s.id
            GROUP BY s.id ORDER BY s.id
        """)
        print("Session | Activity | Started (UTC) | Samples")
        for row in rows:
            print(" | ".join(map(str, row)))
        print("Total samples:", connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
    finally:
        connection.close()


def positive(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def validate_output_paths(paths):
    resolved = [Path(path).expanduser().resolve() for path in paths]
    for index, path in enumerate(resolved):
        for other in resolved[:index]:
            if path == other or (path.exists() and other.exists() and path.samefile(other)):
                raise ValueError("Measurements and verdicts must use different database/CSV files.")


def set_recording_dataset(database, dataset):
    """Prevent a custom path from silently mixing the two purposes again."""
    row = database.connection.execute(
        "SELECT value FROM recording_metadata WHERE key = 'recording_dataset'"
    ).fetchone()
    if row is not None and row[0] != dataset:
        raise ValueError(f"This database contains {row[0]} recordings, not {dataset}. "
                         "Choose the matching mode or different output files.")
    with database.connection:
        database.connection.execute(
            "INSERT OR IGNORE INTO recording_metadata (key, value) VALUES ('recording_dataset', ?)",
            (dataset,),
        )


def configure_output_paths(args):
    """Validate the mode before opening files or the serial port."""
    if args.mode != "test" and args.verdict is not None:
        raise ValueError("--verdict is only allowed with --test.")
    if args.mode == "test" and not args.summary and args.verdict is None:
        raise ValueError("--test requires --verdict fall, near-fall, or normal.")
    if args.activity is not None and not args.activity.strip():
        raise ValueError("--name must not be empty.")
    if args.mode == "calibration":
        if args.duration not in (None, DEFAULT_DURATION_SECONDS) or args.samples is not None:
            raise ValueError("--calibration records for 30 seconds; omit --duration and --samples.")
        args.duration = DEFAULT_DURATION_SECONDS
    elif args.mode == "free":
        if args.duration is not None or args.samples is not None:
            raise ValueError("--free runs until a fall is detected; omit --duration and --samples.")
    elif args.duration is None:
        args.duration = DEFAULT_DURATION_SECONDS
    stem = {"test": "prototype_verdicts", "free": "prototype_readings",
            "calibration": "calibration_readings"}[args.mode]
    if args.db is None:
        args.db = DATA_DIR / f"{stem}.sqlite3"
    if args.csv is None:
        args.csv = args.db.with_suffix(".csv")
    validate_output_paths([args.db, args.csv])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--test", dest="mode", action="store_const", const="test",
                       help="Record a test with an expected verdict (30 seconds by default)")
    modes.add_argument("--calibration", dest="mode", action="store_const", const="calibration",
                       help="Record calibration measurements for 30 seconds")
    modes.add_argument("--free", dest="mode", action="store_const", const="free",
                       help="Record freely until the board reports a fall")
    parser.add_argument("--port", default="auto", help="USB serial device, or auto (default)")
    parser.add_argument("--name", "--activity", dest="activity",
                        help="Run name; test names automatically end with the expected verdict")
    parser.add_argument("--verdict", choices=("fall", "near-fall", "normal"),
                        help="Expected test result; never influences the firmware")
    parser.add_argument("--notes", default="", help="Optional context, e.g. board placement")
    parser.add_argument("--db", type=Path, help="Override the selected mode's database")
    parser.add_argument("--csv", type=Path, help="Override its CSV (otherwise derived from --db)")
    parser.add_argument("--duration", type=positive, help="Test duration in seconds (default: 30)")
    parser.add_argument("--samples", type=positive, help="Optional early sample limit for --test")
    parser.add_argument("--summary", action="store_true", help="Show saved sessions without opening the serial port")
    args = parser.parse_args()
    try:
        configure_output_paths(args)
        if args.summary:
            show_summary(args.db, args.mode)
            return 0
        return record(args)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
