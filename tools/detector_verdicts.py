"""Persist actual firmware diagnostics separately from a recording's user label.

SQLite is authoritative. The CSV is an atomic, replaceable export containing
one summary per recording; the events table retains every DETECTOR line.
This module never derives firmware verdicts from labels or sensor-data replay.
"""

import csv
from contextlib import closing
from datetime import datetime, timedelta
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile


CSV_FIELDS = (
    "session_id", "activity", "expected_verdict", "observed_verdict", "verdict_source",
    "recording_status", "sample_count", "notes", "started_at_utc", "ended_at_utc",
    "stop_reason", "historical",
    "diagnostic_count", "valid_diagnostic_count", "invalid_diagnostic_count",
    "status_count", "event_count", "spike_count", "possible_fall_count",
    "near_fall_count", "uncertain_count", "sensor_fault_count",
    "fault_diagnostic_count", "restarted_count", "first_diagnostic_at_utc",
    "last_diagnostic_at_utc", "first_board_time_ms", "last_board_time_ms",
    "first_state", "last_state", "last_alarm", "last_sensors",
    "diagnostic_coverage", "sensor_health", "port", "run_id", "source_database",
)
V1_CSV_FIELDS = tuple(field for field in CSV_FIELDS if field not in ("expected_verdict", "port"))
EXPECTED_VERDICTS = frozenset(("fall", "near-fall", "normal"))
RETIRED_RATE_FIELDS = (
    "accel_avg_rate", "accel_msd_rate", "gyro_avg_rate", "gyro_msd_rate",
)
MEASUREMENT_FIELDS = (
    "accel_x_mps2", "accel_y_mps2", "accel_z_mps2", "accel_magnitude_mps2",
    "gyro_x_dps", "gyro_y_dps", "gyro_z_dps", "gyro_magnitude_dps",
)
METRIC_FIELDS = (
    "board_time_ms", "accel_msd", "gyro_msd", "slope_window_samples",
    "accel_magnitude_slope", "accel_msd_slope", "gyro_magnitude_slope", "gyro_msd_slope",
)
SAMPLE_FIELDS = ("sample_number", *MEASUREMENT_FIELDS, *METRIC_FIELDS)
COUNT_FIELDS = (
    "diagnostic_count", "valid_diagnostic_count", "invalid_diagnostic_count",
    "status_count", "event_count", "spike_count", "possible_fall_count",
    "near_fall_count", "uncertain_count", "sensor_fault_count",
    "fault_diagnostic_count", "restarted_count",
)
DETAIL_FIELDS = (
    "first_diagnostic_at_utc", "last_diagnostic_at_utc", "first_board_time_ms",
    "last_board_time_ms", "first_state", "last_state", "last_alarm", "last_sensors",
)
SUMMARY_FIELDS = (
    "observed_verdict", "verdict_source", *COUNT_FIELDS, *DETAIL_FIELDS,
    "diagnostic_coverage", "sensor_health",
)
STATES = frozenset((
    "WARMUP", "NORMAL", "OBSERVING", "UNCERTAIN", "FALL_LATCHED", "SENSOR_FAULT",
))
EVENT_STATES = {
    "READY": "NORMAL", "SPIKE": "OBSERVING", "NEAR_FALL": "NORMAL",
    "POSSIBLE_FALL": "FALL_LATCHED", "UNCERTAIN": "UNCERTAIN",
    "RESTARTED": "WARMUP",
    "DISTURBANCE_CONFIRMED": "OBSERVING",
    "DISTURBANCE_REJECTED": "NORMAL",
    "DISTURBANCE_UNKNOWN": "WARMUP",
}
EVENTS = frozenset((*EVENT_STATES, "STATUS", "SENSOR_FAULT"))
DIAGNOSTIC = re.compile(
    r"DETECTOR[ \t]+TimeMs=([0-9]{1,10})[ \t]+State=([A-Z_]+)"
    r"[ \t]+Alarm=([01])[ \t]+Sensors=(OK|FAULT)[ \t]+Event=([A-Z_]+)"
    r"(?:[ \t]+([^\r\n]*))?"
)
V1_SCHEMA_ID = "cg2028_firmware_verdicts_v1"
SCHEMA_ID = "cg2028_firmware_verdicts_v2"
TEST_SCHEMA_STATEMENTS = (
    """CREATE TABLE test_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES runs(run_id),
        legacy_record_id INTEGER,
        timestamp_utc TEXT NOT NULL,
        sample_number INTEGER NOT NULL,
        accel_x_mps2 REAL NOT NULL, accel_y_mps2 REAL NOT NULL, accel_z_mps2 REAL NOT NULL,
        accel_magnitude_mps2 REAL NOT NULL,
        gyro_x_dps REAL NOT NULL, gyro_y_dps REAL NOT NULL, gyro_z_dps REAL NOT NULL,
        gyro_magnitude_dps REAL NOT NULL,
        board_time_ms INTEGER, accel_msd REAL, gyro_msd REAL, slope_window_samples INTEGER,
        accel_magnitude_slope REAL, accel_msd_slope REAL, gyro_magnitude_slope REAL, gyro_msd_slope REAL,
        UNIQUE(run_id, legacy_record_id)
    )""",
    "CREATE INDEX test_samples_run_id ON test_samples(run_id,id)",
    """CREATE VIEW test_readings AS
        SELECT p.id AS record_id, r.session_id, p.timestamp_utc, r.activity, r.notes,
               p.sample_number,
               p.accel_x_mps2,p.accel_y_mps2,p.accel_z_mps2,p.accel_magnitude_mps2,
               p.gyro_x_dps,p.gyro_y_dps,p.gyro_z_dps,p.gyro_magnitude_dps,
               p.board_time_ms,p.accel_msd,p.gyro_msd,p.slope_window_samples,
               p.accel_magnitude_slope,p.accel_msd_slope,p.gyro_magnitude_slope,p.gyro_msd_slope,
               p.run_id,p.legacy_record_id,r.expected_verdict,r.port
        FROM test_samples p JOIN runs r ON r.run_id=p.run_id""",
)


def _same_path(first, second):
    first, second = Path(first).expanduser().resolve(), Path(second).expanduser().resolve()
    return first == second or (
        first.exists() and second.exists() and os.path.samefile(first, second)
    )


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("A UTC timestamp string is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid UTC timestamp: " + repr(value)) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must include the UTC timezone")
    return value


def parse_diagnostic(line):
    """Return a parsed diagnostic, or raise ValueError for malformed firmware data."""
    match = DIAGNOSTIC.fullmatch(line.strip(" \t\r\n"))
    if not match:
        raise ValueError("Malformed DETECTOR diagnostic")
    tick, state, alarm, sensors, event, message = match.groups()
    tick, alarm = int(tick), int(alarm)
    if tick > 0xFFFFFFFF or state not in STATES or event not in EVENTS:
        raise ValueError("Unknown detector state/event or out-of-range board time")
    if bool(alarm) != (state == "FALL_LATCHED"):
        raise ValueError("Alarm flag disagrees with detector state")
    if (sensors == "FAULT") != (state == "SENSOR_FAULT" or event == "SENSOR_FAULT"):
        # A latched alarm can retain its state throughout a subsequent fault.
        if not (state == "FALL_LATCHED" and sensors == "FAULT" and event == "STATUS"):
            raise ValueError("Sensor status disagrees with detector state/event")
    if event in EVENT_STATES and state != EVENT_STATES[event]:
        raise ValueError("Detector event disagrees with detector state")
    if event == "SENSOR_FAULT" and state not in ("SENSOR_FAULT", "FALL_LATCHED"):
        raise ValueError("Sensor fault has an incompatible detector state")
    if sensors == "FAULT" and event not in ("STATUS", "SENSOR_FAULT"):
        raise ValueError("A decision cannot be based on a failed acquisition")
    return {
        "board_time_ms": tick, "state": state, "alarm": alarm,
        "sensors": sensors, "event": event, "message": message or "",
    }


def _observation_history(events):
    """Track observed candidates without inventing missing firmware decisions.

    A rejected Prototype 2 candidate is complete, even though it produced no
    fall/near-fall verdict. A later rejection cannot explain an earlier candidate
    whose resolution was missed. UNKNOWN explicitly leaves insufficient evidence
    in this recording, even after the firmware has warmed up again.
    """
    candidate_open = unresolved = unknown = False
    for diagnostic in events:
        event, state = diagnostic["event"], diagnostic["state"]
        if event == "DISTURBANCE_UNKNOWN":
            unresolved = unknown = True
            candidate_open = False
        elif event in ("DISTURBANCE_REJECTED", "NEAR_FALL", "POSSIBLE_FALL"):
            # Accept a resolution even if recording started after its SPIKE.
            candidate_open = False
        elif event == "SPIKE":
            # A new SPIKE while one was open implies a missed resolution.
            unresolved |= candidate_open
            candidate_open = True
        elif state in ("OBSERVING", "UNCERTAIN"):
            # STATUS or CONFIRMED can reveal a candidate whose SPIKE was missed.
            candidate_open = True
        elif candidate_open:
            # NORMAL/WARMUP/FAULT alone do not say how the candidate ended.
            unresolved = True
            candidate_open = False
    return unresolved or candidate_open, unknown


def _summary(historical, events):
    result = dict.fromkeys(COUNT_FIELDS, 0)
    result.update(dict.fromkeys(DETAIL_FIELDS))
    valid = [event for event in events if event["parse_valid"]]
    result["diagnostic_count"] = len(events)
    result["valid_diagnostic_count"] = len(valid)
    result["invalid_diagnostic_count"] = len(events) - len(valid)
    result["status_count"] = sum(event["event"] == "STATUS" for event in valid)
    result["event_count"] = len(valid) - result["status_count"]
    for name, field in (
        ("SPIKE", "spike_count"), ("POSSIBLE_FALL", "possible_fall_count"),
        ("NEAR_FALL", "near_fall_count"), ("UNCERTAIN", "uncertain_count"),
        ("SENSOR_FAULT", "sensor_fault_count"), ("RESTARTED", "restarted_count"),
    ):
        result[field] = sum(event["event"] == name for event in valid)
    result["fault_diagnostic_count"] = sum(event["sensors"] == "FAULT" for event in valid)
    if events:
        result["first_diagnostic_at_utc"] = events[0]["timestamp_utc"]
        result["last_diagnostic_at_utc"] = events[-1]["timestamp_utc"]
    if valid:
        first, last = valid[0], valid[-1]
        result.update(
            first_board_time_ms=first["board_time_ms"], last_board_time_ms=last["board_time_ms"],
            first_state=first["state"], last_state=last["state"],
            last_alarm=last["alarm"], last_sensors=last["sensors"],
        )
    if historical:
        return dict(result, observed_verdict="NOT_RECORDED", verdict_source="NOT_RECORDED",
                    diagnostic_coverage="NOT_RECORDED", sensor_health="NOT_RECORDED")
    if not valid:
        return dict(result, observed_verdict="NO_DETECTOR_DATA", verdict_source="NONE",
                    diagnostic_coverage="INVALID_ONLY" if events else "NONE",
                    sensor_health="UNKNOWN")

    result["diagnostic_coverage"] = (
        "OBSERVED_WITH_INVALID_LINES" if result["invalid_diagnostic_count"] else "OBSERVED"
    )
    result["sensor_health"] = (
        "FAULT_PRESENT" if last["sensors"] == "FAULT" else
        "FAULT_RECOVERED" if result["fault_diagnostic_count"] else "OK_OBSERVED"
    )
    unresolved, unknown = _observation_history(valid)
    # Explicit firmware decisions take priority and survive later health faults.
    fall, near = result["possible_fall_count"], result["near_fall_count"]
    if near and not fall and any(event["alarm"] for event in valid):
        verdict, source = "NEAR_FALL_WITH_LATCHED_ALARM", "FIRMWARE_EVENT_AND_STATUS"
    elif fall or near:
        verdict = "MIXED_DECISIONS" if fall and near else "POSSIBLE_FALL" if fall else "NEAR_FALL"
        source = "FIRMWARE_EVENT"
    elif any(event["alarm"] for event in valid):
        verdict = "PREEXISTING_LATCHED_ALARM" if first["alarm"] else "LATCHED_ALARM_OBSERVED"
        source = "FIRMWARE_STATUS"
    elif last["state"] == "SENSOR_FAULT":
        verdict, source = "SENSOR_FAULT", "FIRMWARE_DIAGNOSTICS"
    elif last["state"] == "OBSERVING":
        verdict, source = "OBSERVATION_INCOMPLETE", "FIRMWARE_DIAGNOSTICS"
    elif last["state"] == "UNCERTAIN":
        verdict, source = "UNCERTAIN", "FIRMWARE_DIAGNOSTICS"
    elif unknown:
        verdict, source = "OBSERVATION_INCOMPLETE", "FIRMWARE_DIAGNOSTICS"
    elif last["state"] == "WARMUP":
        verdict, source = "WARMUP_INCOMPLETE", "FIRMWARE_DIAGNOSTICS"
    elif unresolved:
        verdict, source = "OBSERVATION_INCOMPLETE", "FIRMWARE_DIAGNOSTICS"
    else:
        # This means no decision was observed, not that the person was normal.
        verdict, source = "NO_EVENT_OBSERVED", "FIRMWARE_DIAGNOSTICS"
    return dict(result, observed_verdict=verdict, verdict_source=source)


class VerdictRecorder:
    """Durable test recordings: expected labels, telemetry and actual decisions."""

    def __init__(self, db_path, csv_path):
        self.db_path = Path(db_path).expanduser().resolve()
        self.csv_path = Path(csv_path).expanduser().resolve()
        self.connection = None
        self.migration_backups = []
        self._csv_is_v1 = False
        if _same_path(self.db_path, self.csv_path):
            raise ValueError("Verdict SQLite and CSV must be different files")
        existing_csv = self._read_existing_csv()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = sqlite3.connect(self.db_path)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA synchronous = FULL")
            tables = {row[0] for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if tables:
                if "verdict_metadata" not in tables:
                    raise ValueError("This is not a dedicated firmware-verdict database")
                marker = self.connection.execute(
                    "SELECT value FROM verdict_metadata WHERE key='schema_id'"
                ).fetchone()
                if marker is None or marker[0] not in (V1_SCHEMA_ID, SCHEMA_ID):
                    raise ValueError("Unknown firmware-verdict database schema")
                required = {"verdict_metadata", "runs", "events"}
                if marker[0] == SCHEMA_ID:
                    required.add("test_samples")
                if tables != required:
                    raise ValueError("Firmware-verdict database tables do not match its schema")
                self._validate_existing_csv(existing_csv)
                if marker[0] == V1_SCHEMA_ID:
                    self._migrate_v1()
                elif self._csv_is_v1:
                    # Recovery after SQLite committed its upgrade but the
                    # process stopped before replacing the old CSV export.
                    self._backup_csv()
            elif existing_csv:
                raise ValueError("Refusing to replace verdict CSV rows without their SQLite database")
            else:
                self._create_schema()
            self._validate_existing_csv(existing_csv)
            self.export_csv()
        except BaseException:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            raise

    def _read_existing_csv(self):
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            return []
        try:
            with self.csv_path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames not in (list(CSV_FIELDS), list(V1_CSV_FIELDS)):
                    raise ValueError("Refusing to overwrite a CSV with another schema")
                self._csv_is_v1 = reader.fieldnames == list(V1_CSV_FIELDS)
                rows = list(reader)
                if any(None in row or any(value is None for value in row.values()) for row in rows):
                    raise ValueError("Malformed existing verdict CSV")
                return rows
        except (UnicodeError, csv.Error) as error:
            raise ValueError("The verdict CSV is not a valid UTF-8 CSV") from error

    def _backup_csv(self):
        if not self.csv_path.exists():
            return None
        backup_directory = self.csv_path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=backup_directory, prefix=self.csv_path.name + ".before-v2.",
            suffix=".bak", delete=False,
        ) as target:
            backup = Path(target.name)
            with self.csv_path.open("rb") as source:
                shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        self.migration_backups.append(backup)
        return backup

    def _migrate_v1(self):
        if self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Cannot migrate a damaged firmware-verdict database")
        backup_directory = self.db_path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=backup_directory, prefix=self.db_path.name + ".before-v2.",
            suffix=".bak", delete=False,
        ) as stream:
            backup_path = Path(stream.name)
        with closing(sqlite3.connect(backup_path)) as backup:
            self.connection.backup(backup)
            backup.execute("PRAGMA journal_mode=DELETE")
            if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("The pre-migration SQLite backup did not verify")
        self.migration_backups.append(backup_path)
        csv_backup = self._backup_csv()
        # ALTER/CREATE/marker changes share one transaction. Existing run and
        # event rows are untouched; old expectations intentionally stay NULL.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("ALTER TABLE runs ADD COLUMN expected_verdict TEXT "
                                    "CHECK(expected_verdict IS NULL OR expected_verdict IN ('fall','near-fall','normal'))")
            self.connection.execute("ALTER TABLE runs ADD COLUMN port TEXT")
            for statement in TEST_SCHEMA_STATEMENTS:
                self.connection.execute(statement)
            self.connection.execute("UPDATE verdict_metadata SET value=? WHERE key='schema_id'", (SCHEMA_ID,))
            self.connection.execute("INSERT INTO verdict_metadata VALUES ('v1_database_backup',?)", (str(backup_path),))
            if csv_backup is not None:
                self.connection.execute("INSERT INTO verdict_metadata VALUES ('v1_csv_backup',?)", (str(csv_backup),))

    def _create_schema(self):
        count_columns = ",\n".join(f"{field} INTEGER NOT NULL DEFAULT 0" for field in COUNT_FIELDS)
        detail_columns = ",\n".join(
            f"{field} {'INTEGER' if field.endswith('_ms') or field == 'last_alarm' else 'TEXT'}"
            for field in DETAIL_FIELDS
        )
        self.connection.executescript(f"""
            BEGIN;
            CREATE TABLE verdict_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_database TEXT NOT NULL,
                session_id INTEGER NOT NULL,
                activity TEXT NOT NULL,
                expected_verdict TEXT CHECK(expected_verdict IS NULL OR expected_verdict IN ('fall','near-fall','normal')),
                notes TEXT NOT NULL,
                port TEXT,
                started_at_utc TEXT NOT NULL,
                ended_at_utc TEXT,
                recording_status TEXT NOT NULL DEFAULT 'OPEN',
                sample_count INTEGER,
                stop_reason TEXT,
                historical INTEGER NOT NULL CHECK(historical IN (0,1)),
                observed_verdict TEXT NOT NULL,
                verdict_source TEXT NOT NULL,
                {count_columns},
                {detail_columns},
                diagnostic_coverage TEXT NOT NULL,
                sensor_health TEXT NOT NULL,
                UNIQUE(source_database, session_id, started_at_utc)
            );
            CREATE TABLE events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(run_id),
                timestamp_utc TEXT NOT NULL,
                raw_line TEXT NOT NULL,
                parse_valid INTEGER NOT NULL CHECK(parse_valid IN (0,1)),
                parse_error TEXT,
                board_time_ms INTEGER,
                state TEXT,
                alarm INTEGER,
                sensors TEXT,
                event TEXT,
                message TEXT
            );
            CREATE INDEX events_run_id ON events(run_id, event_id);
            {';'.join(TEST_SCHEMA_STATEMENTS)};
            INSERT INTO verdict_metadata VALUES ('schema_id', '{SCHEMA_ID}');
            COMMIT;
        """)

    def _validate_existing_csv(self, rows):
        seen = set()
        immutable = ("source_database", "session_id", "started_at_utc", "activity", "notes", "historical")
        for row in rows:
            try:
                run_id = int(row["run_id"])
            except ValueError as error:
                raise ValueError("Invalid run ID in verdict CSV") from error
            if run_id in seen:
                raise ValueError("Duplicate run ID in verdict CSV")
            seen.add(run_id)
            saved = self.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if saved is None or any(row[field] != str(saved[field]) for field in immutable):
                raise ValueError("Verdict CSV does not belong to this SQLite database")
            for field in ("expected_verdict", "port"):
                if field in row:
                    value = saved[field] if field in saved.keys() else None
                    if row[field] != ("" if value is None else str(value)):
                        if field == "port" and row[field] == "" and value is not None:
                            continue  # A committed legacy-port enrichment may precede its CSV export.
                        raise ValueError("Verdict CSV has conflicting expected-verdict or port metadata")
            # Dynamic summary fields may lag a committed SQLite update if the
            # previous process stopped before replacing the CSV. SQLite wins.

    def _require_open(self):
        if self.connection is None:
            raise ValueError("Verdict recorder is closed")

    def get_run(self, run_id):
        self._require_open()
        row = self.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown verdict run ID")
        return dict(row)

    def _update_summary(self, run_id, historical):
        events = self.connection.execute(
            "SELECT * FROM events WHERE run_id=? ORDER BY event_id", (run_id,)
        ).fetchall()
        summary = _summary(historical, events)
        assignments = ",".join(f"{field}=?" for field in SUMMARY_FIELDS)
        self.connection.execute(
            f"UPDATE runs SET {assignments} WHERE run_id=?",
            tuple(summary[field] for field in SUMMARY_FIELDS) + (run_id,),
        )

    def start_run(self, source_database, session_id, activity, notes, started_at_utc, *,
                  historical=False, expected_verdict=None, port=None):
        """Create/reopen an external legacy run without inferring an expectation."""
        return self._start_run(source_database, session_id, activity, notes, started_at_utc,
                               historical=historical, expected_verdict=expected_verdict, port=port)

    def _start_run(self, source_database, session_id, activity, notes, started_at_utc, *,
                   historical=False, expected_verdict=None, port=None, self_source=False):
        self._require_open()
        source = Path(source_database).expanduser().resolve()
        if (_same_path(source, self.db_path) and not self_source) or _same_path(source, self.csv_path):
            raise ValueError("Verdict outputs must not replace the source measurement database")
        if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id < 1:
            raise ValueError("Source session ID must be a positive integer")
        if not isinstance(activity, str) or not isinstance(notes, str):
            raise ValueError("Activity and notes must be strings")
        if expected_verdict is not None and expected_verdict not in EXPECTED_VERDICTS:
            raise ValueError("Expected verdict must be fall, near-fall, or normal")
        if historical and expected_verdict is not None:
            raise ValueError("Historical runs have no recorded structured expectation")
        if port is not None and not isinstance(port, str):
            raise ValueError("Port must be a string or None")
        _timestamp(started_at_utc)
        previous = self.connection.execute(
            "SELECT * FROM runs WHERE source_database=? AND session_id=? AND started_at_utc=?",
            (str(source), session_id, started_at_utc),
        ).fetchone()
        if previous is not None:
            if (previous["activity"], previous["notes"], previous["historical"], previous["expected_verdict"]) != (
                    activity, notes, int(historical), expected_verdict):
                raise ValueError("Existing verdict run has different immutable source metadata")
            if port is not None and previous["port"] not in (None, port):
                raise ValueError("Existing verdict run has a different port")
            if port is not None and previous["port"] is None:
                with self.connection:
                    self.connection.execute("UPDATE runs SET port=? WHERE run_id=?", (port, previous["run_id"]))
                self.export_csv()
            return previous["run_id"]
        empty = _summary(historical, [])
        with self.connection:
            result = self.connection.execute("""
                INSERT INTO runs(source_database,session_id,activity,notes,started_at_utc,historical,
                                 observed_verdict,verdict_source,diagnostic_coverage,sensor_health,
                                 expected_verdict,port,sample_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (str(source), session_id, activity, notes, started_at_utc, int(historical),
                  empty["observed_verdict"], empty["verdict_source"],
                  empty["diagnostic_coverage"], empty["sensor_health"], expected_verdict, port,
                  0 if self_source else None))
            run_id = result.lastrowid
        self.export_csv()
        return run_id

    def start_test(self, name, expected_verdict, notes, started_at_utc, port=None):
        """Create a new test whose metadata, samples and diagnostics share this DB."""
        self._require_open()
        if expected_verdict not in EXPECTED_VERDICTS:
            raise ValueError("Expected verdict must be fall, near-fall, or normal")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError("Test name must be nonempty text or None")
        # Allocate globally within this verdict database, including legacy IDs.
        # BEGIN IMMEDIATE prevents simultaneous recorders choosing the same ID.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            session_id = self.connection.execute("SELECT COALESCE(MAX(session_id),0)+1 FROM runs").fetchone()[0]
            name = name.strip() if name is not None else f"test-{session_id}"
            return self._start_run(self.db_path, session_id, f"{name}-{expected_verdict}", notes,
                                   started_at_utc, expected_verdict=expected_verdict,
                                   port=port, self_source=True)
        except BaseException:
            self.connection.rollback()
            raise

    @staticmethod
    def _sample_values(sample):
        sample = dict(sample)
        if any(field in sample for field in RETIRED_RATE_FIELDS):
            raise ValueError(
                "Retired AvgRate/MSDRate fields cannot be saved in a test. "
                "Flash the Magnitude/MagnitudeSlope firmware before recording."
            )
        for field in ("sample_number", *MEASUREMENT_FIELDS):
            if field not in sample:
                raise ValueError("Missing sample field: " + field)
        result = {field: sample.get(field) for field in SAMPLE_FIELDS}
        for field in ("sample_number", "board_time_ms", "slope_window_samples"):
            value = result[field]
            if value is None and field != "sample_number":
                continue
            minimum = 2 if field == "slope_window_samples" else 0
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= 0xFFFFFFFF:
                raise ValueError("Invalid integer sample field: " + field)
        for field in (*MEASUREMENT_FIELDS, "accel_msd", "gyro_msd",
                      "accel_magnitude_slope", "accel_msd_slope", "gyro_magnitude_slope", "gyro_msd_slope"):
            value = result[field]
            if value is None and field not in MEASUREMENT_FIELDS:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("Invalid numeric sample field: " + field)
            if field in ("accel_magnitude_mps2", "gyro_magnitude_dps", "accel_msd", "gyro_msd") and value < 0:
                raise ValueError("Negative magnitude/MSD sample field: " + field)
        return result

    def _insert_sample(self, run_id, values, timestamp_utc, legacy_record_id=None):
        columns = ("run_id", "legacy_record_id", "timestamp_utc", *SAMPLE_FIELDS)
        placeholders = ",".join("?" for _ in columns)
        result = self.connection.execute(
            f"INSERT INTO test_samples({','.join(columns)}) VALUES ({placeholders})",
            (run_id, legacy_record_id, timestamp_utc, *(values[field] for field in SAMPLE_FIELDS)),
        )
        return result.lastrowid

    def append_sample(self, run_id, sample, timestamp_utc):
        """Commit one live test sample and update its durable running count."""
        run = self.get_run(run_id)
        if run["historical"] or run["expected_verdict"] is None or run["recording_status"] != "OPEN":
            raise ValueError("Live telemetry requires an open structured test run")
        _timestamp(timestamp_utc)
        values = self._sample_values(sample)
        with self.connection:
            sample_id = self._insert_sample(run_id, values, timestamp_utc)
            self.connection.execute("UPDATE runs SET sample_count=(SELECT COUNT(*) FROM test_samples "
                                    "WHERE run_id=?) WHERE run_id=?", (run_id, run_id))
        self.export_csv()
        return sample_id

    def count_samples(self, run_id):
        self.get_run(run_id)
        return self.connection.execute("SELECT COUNT(*) FROM test_samples WHERE run_id=?", (run_id,)).fetchone()[0]

    def import_sample(self, run_id, row_from_old_readings_view):
        """Copy a legacy reading exactly; repeat imports must match all values."""
        run = self.get_run(run_id)
        row = dict(row_from_old_readings_view)
        for field in ("session_id", "activity", "notes"):
            if row.get(field) != run[field]:
                raise ValueError("Legacy sample does not belong to this run: " + field)
        legacy_id = row.get("record_id")
        if not isinstance(legacy_id, int) or isinstance(legacy_id, bool) or legacy_id < 1:
            raise ValueError("Legacy sample needs its original positive record_id")
        timestamp = _timestamp(row.get("timestamp_utc"))
        values = self._sample_values(row)
        previous = self.connection.execute(
            "SELECT * FROM test_samples WHERE run_id=? AND legacy_record_id=?", (run_id, legacy_id)
        ).fetchone()
        if previous is not None:
            if previous["timestamp_utc"] != timestamp or any(previous[field] != values[field] for field in SAMPLE_FIELDS):
                raise ValueError("A conflicting legacy sample has already been imported")
            return previous["id"]
        with self.connection:
            sample_id = self._insert_sample(run_id, values, timestamp, legacy_id)
        # Historical summary counts and verdicts remain exactly as recorded.
        # The caller verifies count_samples() and exports once after importing.
        return sample_id

    def append_line(self, run_id, line, timestamp_utc):
        """Store a DETECTOR line; return True only if it parsed successfully.

        Unrelated lines return False without a write. Malformed diagnostics are
        retained with parse_valid=0; they cannot create or change a verdict.
        """
        if not isinstance(line, str):
            raise ValueError("Diagnostic line must be text")
        if not line.lstrip().startswith("DETECTOR"):
            return False
        run = self.get_run(run_id)
        if run["historical"] or run["recording_status"] != "OPEN":
            raise ValueError("Cannot append diagnostics to a historical or finished run")
        _timestamp(timestamp_utc)
        raw_line = line.rstrip("\r\n")
        try:
            parsed = parse_diagnostic(raw_line)
            error = None
        except ValueError as problem:
            parsed, error = {}, str(problem)
        parsed_fields = ("board_time_ms", "state", "alarm", "sensors", "event", "message")
        with self.connection:
            self.connection.execute("""
                INSERT INTO events(run_id,timestamp_utc,raw_line,parse_valid,parse_error,
                                   board_time_ms,state,alarm,sensors,event,message)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (run_id, timestamp_utc, raw_line, int(error is None), error,
                  *(parsed.get(field) for field in parsed_fields)))
            self._update_summary(run_id, False)
        self.export_csv()
        return error is None

    def finish_run(self, run_id, ended_at_utc, stop_reason, sample_count):
        run = self.get_run(run_id)
        _timestamp(ended_at_utc)
        if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
            raise ValueError("Sample count must be a nonnegative integer")
        if not isinstance(stop_reason, str) or not stop_reason:
            raise ValueError("A stop reason is required")
        if run["expected_verdict"] is not None and sample_count != self.count_samples(run_id):
            raise ValueError("Finished test count must match its stored telemetry")
        if run["recording_status"] == "FINISHED":
            if (run["ended_at_utc"], run["stop_reason"], run["sample_count"]) != (ended_at_utc, stop_reason, sample_count):
                raise ValueError("Finished verdict run has different completion metadata")
            return
        with self.connection:
            self.connection.execute("""
                UPDATE runs SET ended_at_utc=?,stop_reason=?,sample_count=?,recording_status='FINISHED'
                WHERE run_id=?
            """, (ended_at_utc, stop_reason, sample_count, run_id))
        self.export_csv()

    def export_csv(self):
        self._require_open()
        # Recheck collisions immediately before replacement, including aliases
        # introduced after construction. Never replace a source database.
        sources = self.connection.execute("SELECT DISTINCT source_database FROM runs")
        if _same_path(self.db_path, self.csv_path) or any(
            _same_path(row[0], self.csv_path) for row in sources
        ):
            raise ValueError("Verdict CSV path collides with a SQLite database")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", newline="", encoding="utf-8", dir=self.csv_path.parent,
                prefix="." + self.csv_path.name + ".", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
                writer.writeheader()
                for row in self.connection.execute("SELECT * FROM runs ORDER BY run_id"):
                    writer.writerow(dict(row))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.csv_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def close(self):
        if self.connection is not None:
            try:
                self.export_csv()
            finally:
                self.connection.close()
                self.connection = None
