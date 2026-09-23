"""Host-side checks for complete serial samples and durable SQLite recording.

Run with: python3 -m unittest discover -s tools -p 'test_record_activity.py'
"""

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from record_activity import CSV_FIELDS, CSVRecorder, RecordingDatabase, SampleParser


ACCEL = "Accel EWMA ASM [m/s^2]: X=   1.250 Y=  -2.500 Z=   9.800 Avg=   2.850"
GYRO = "Gyro  EWMA ASM [dps]  : X= -30.000 Y=  15.000 Z=   0.000 Avg=  -5.000"
ACCEL_EXTENDED = ACCEL + " MSD=0.125 AvgRate=2.500 MSDRate=0.750"
GYRO_EXTENDED = GYRO + " MSD=25.000 AvgRate=50.000 MSDRate=100.000"
ACCEL_SLOPE = ACCEL + " MSD=0.125 AvgSlope=-2.500 MSDSlope=0.750"
GYRO_SLOPE = GYRO + " MSD=25.000 AvgSlope=50.000 MSDSlope=-100.000"
LEGACY_FIELDS = (
    "record_id", "session_id", "timestamp_utc", "activity", "notes", "sample_number",
    "accel_x_mps2", "accel_y_mps2", "accel_z_mps2", "accel_avg_mps2",
    "gyro_x_dps", "gyro_y_dps", "gyro_z_dps", "gyro_avg_dps",
)
EXTRA_FIELDS = (
    "board_time_ms", "accel_msd", "accel_avg_rate", "accel_msd_rate",
    "gyro_msd", "gyro_avg_rate", "gyro_msd_rate",
)
SLOPE_FIELDS = (
    "slope_window_samples", "accel_avg_slope", "accel_msd_slope",
    "gyro_avg_slope", "gyro_msd_slope",
)


def expected_sample(number):
    return {
        "sample_number": number,
        "accel_x_mps2": 1.25,
        "accel_y_mps2": -2.5,
        "accel_z_mps2": 9.8,
        "accel_avg_mps2": 2.85,
        "gyro_x_dps": -30.0,
        "gyro_y_dps": 15.0,
        "gyro_z_dps": 0.0,
        "gyro_avg_dps": -5.0,
    }


def expected_extended_sample(number, board_time_ms):
    return {
        **expected_sample(number),
        "board_time_ms": board_time_ms,
        "accel_msd": 0.125,
        "accel_avg_rate": 2.5,
        "accel_msd_rate": 0.75,
        "gyro_msd": 25.0,
        "gyro_avg_rate": 50.0,
        "gyro_msd_rate": 100.0,
    }


def expected_slope_sample(number, board_time_ms, window=5):
    return {
        **expected_sample(number),
        "board_time_ms": board_time_ms,
        "accel_msd": 0.125,
        "gyro_msd": 25.0,
        "slope_window_samples": window,
        "accel_avg_slope": -2.5,
        "accel_msd_slope": 0.75,
        "gyro_avg_slope": 50.0,
        "gyro_msd_slope": -100.0,
    }


class SampleParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = SampleParser()

    def feed_all(self, lines):
        samples = []
        for line in lines:
            sample = self.parser.feed(line)
            if sample is not None:
                samples.append(sample)
        return samples

    def test_emits_only_complete_sample_and_preserves_signed_values(self):
        self.assertIsNone(self.parser.feed("Sample 42\r\n"))
        self.assertIsNone(self.parser.feed(ACCEL + "\r\n"))
        self.assertEqual(self.parser.feed(GYRO + "\r\n"), expected_sample(42))

    def test_sensor_lines_before_header_are_ignored(self):
        self.assertEqual(self.feed_all([ACCEL, GYRO]), [])
        self.assertEqual(
            self.feed_all(["Sample 0", ACCEL, GYRO]), [expected_sample(0)]
        )

    def test_new_header_discards_previous_partial_sample(self):
        self.assertEqual(
            self.feed_all(["Sample 10", ACCEL, "Sample 11", GYRO]), []
        )
        self.assertEqual(
            self.feed_all(["Sample 12", ACCEL, GYRO]), [expected_sample(12)]
        )

    def test_missing_sensor_line_does_not_reuse_previous_sample(self):
        self.assertEqual(
            self.feed_all(
                ["Sample 1", ACCEL, GYRO, "Sample 2", GYRO, "Sample 3", ACCEL]
            ),
            [expected_sample(1)],
        )

    def test_malformed_sensor_line_discards_cycle_then_recovers(self):
        malformed_cycles = {
            "accel missing average": [ACCEL.rsplit(" Avg=", 1)[0], GYRO],
            "gyro missing average": [ACCEL, GYRO.rsplit(" Avg=", 1)[0]],
            "accel invalid number": [ACCEL.replace("-2.500", "broken"), GYRO],
            "gyro invalid number": [ACCEL, GYRO.replace("15.000", "broken")],
        }
        for description, lines in malformed_cycles.items():
            with self.subTest(description=description):
                self.parser = SampleParser()
                self.assertEqual(self.feed_all(["Sample 4"] + lines), [])
                self.assertEqual(
                    self.feed_all(["Sample 5", ACCEL, GYRO]), [expected_sample(5)]
                )

    def test_malformed_header_cannot_attach_gyro_to_old_sample(self):
        self.assertEqual(
            self.feed_all(["Sample 7", ACCEL, "Sample nope", GYRO]), []
        )
        self.assertEqual(
            self.feed_all(["Sample 8", ACCEL, GYRO]), [expected_sample(8)]
        )

    def test_counter_restart_is_another_complete_sample(self):
        self.assertEqual(
            self.feed_all(
                ["Sample 99", ACCEL, GYRO, "Sample 0", ACCEL, GYRO]
            ),
            [expected_sample(99), expected_sample(0)],
        )

    def test_completed_sample_is_not_emitted_twice(self):
        self.assertEqual(
            self.feed_all(["Sample 6", ACCEL, GYRO, GYRO]), [expected_sample(6)]
        )

    def test_extended_metrics_preserve_values_and_full_uint32_board_time(self):
        for timestamp in (0, 100, 4294967295):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(
                    self.feed_all([
                        f"Sample 12 TimeMs={timestamp}", ACCEL_EXTENDED, GYRO_EXTENDED,
                    ]),
                    [expected_extended_sample(12, timestamp)],
                )

    def test_undefined_startup_metrics_become_none_then_accept_second_sample(self):
        first = expected_extended_sample(0, 0)
        for field in EXTRA_FIELDS[1:]:
            first[field] = None
        undefined = " MSD=NA AvgRate=NA MSDRate=NA"
        self.assertEqual(
            self.feed_all(["Sample 0 TimeMs=0", ACCEL + undefined, GYRO + undefined]),
            [first],
        )

        second = expected_extended_sample(1, 100)
        second["accel_msd_rate"] = None
        second["gyro_msd_rate"] = None
        self.assertEqual(
            self.feed_all([
                "Sample 1 TimeMs=100",
                ACCEL_EXTENDED.replace("MSDRate=0.750", "MSDRate=NA"),
                GYRO_EXTENDED.replace("MSDRate=100.000", "MSDRate=NA"),
            ]),
            [second],
        )

    def test_malformed_or_negative_metric_extensions_discard_frame(self):
        bad_suffixes = [
            " MSD=1", " MSD=1 AvgRate=2", " MSD=1 MSDRate=3",
            " MSD=-1 AvgRate=2 MSDRate=3", " MSD=1 AvgRate=-2 MSDRate=3",
            " MSD=1 AvgRate=2 MSDRate=-3", " MSD=nan AvgRate=2 MSDRate=3",
            " MSD=1 AvgRate=inf MSDRate=3", " MSD=1 AvgRate=2 MSDRate=1e999",
        ]
        for base, other, is_accel in ((ACCEL, GYRO_EXTENDED, True), (GYRO, ACCEL_EXTENDED, False)):
            for suffix in bad_suffixes:
                with self.subTest(is_accel=is_accel, suffix=suffix):
                    sensor_lines = [base + suffix, other] if is_accel else [other, base + suffix]
                    self.assertEqual(
                        self.feed_all(["Sample 5 TimeMs=500"] + sensor_lines), []
                    )
                    self.assertEqual(
                        self.feed_all(["Sample 6 TimeMs=600", ACCEL_EXTENDED, GYRO_EXTENDED]),
                        [expected_extended_sample(6, 600)],
                    )

    def test_header_and_both_sensors_must_agree_on_legacy_or_extended_format(self):
        mismatches = [
            ["Sample 1", ACCEL_EXTENDED, GYRO_EXTENDED],
            ["Sample 1 TimeMs=100", ACCEL, GYRO],
            ["Sample 1 TimeMs=100", ACCEL_EXTENDED, GYRO],
            ["Sample 1 TimeMs=100", ACCEL, GYRO_EXTENDED],
            ["Sample 1", ACCEL_EXTENDED, GYRO],
            ["Sample 1", ACCEL, GYRO_EXTENDED],
        ]
        for lines in mismatches:
            with self.subTest(lines=lines):
                self.assertEqual(self.feed_all(lines), [])

    def test_invalid_board_timestamp_invalidates_prior_partial_frame(self):
        for timestamp in ("-1", "4294967296", "1.5", "NA", "", "100 extra"):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(
                    self.feed_all([
                        "Sample 10 TimeMs=1000", ACCEL_EXTENDED,
                        f"Sample 11 TimeMs={timestamp}", GYRO_EXTENDED,
                    ]),
                    [],
                )

    def test_oversized_digit_headers_discard_frame_without_stopping_parser(self):
        digits = "9" * 5000
        for header in (f"Sample {digits}", f"Sample 11 TimeMs={digits}"):
            with self.subTest(header_kind="timestamp" if "TimeMs=" in header else "sample"):
                self.assertEqual(
                    self.feed_all([
                        "Sample 10 TimeMs=1000", ACCEL_EXTENDED, header, GYRO_EXTENDED,
                    ]),
                    [],
                )
                self.assertEqual(
                    self.feed_all(["Sample 12 TimeMs=1200", ACCEL_EXTENDED, GYRO_EXTENDED]),
                    [expected_extended_sample(12, 1200)],
                )

    def test_slope_frame_preserves_signed_values_without_cumulative_rate_keys(self):
        self.assertEqual(
            self.feed_all(["Sample 6 TimeMs=600 SlopeWindow=5", ACCEL_SLOPE, GYRO_SLOPE]),
            [expected_slope_sample(6, 600)],
        )

    def test_slope_warmup_accepts_na_until_each_metric_is_available(self):
        for sample_number, avg_slope_available in ((0, False), (4, True)):
            with self.subTest(sample_number=sample_number):
                first = sample_number == 0
                expected = expected_slope_sample(sample_number, sample_number * 100)
                if first:
                    expected["accel_msd"] = expected["gyro_msd"] = None
                expected["accel_msd_slope"] = expected["gyro_msd_slope"] = None
                if not avg_slope_available:
                    expected["accel_avg_slope"] = expected["gyro_avg_slope"] = None
                accel_suffix = (
                    f" MSD={'NA' if first else '0.125'}"
                    f" AvgSlope={'-2.500' if avg_slope_available else 'NA'} MSDSlope=NA"
                )
                gyro_suffix = (
                    f" MSD={'NA' if first else '25.000'}"
                    f" AvgSlope={'50.000' if avg_slope_available else 'NA'} MSDSlope=NA"
                )
                self.assertEqual(
                    self.feed_all([
                        f"Sample {sample_number} TimeMs={sample_number * 100} SlopeWindow=5",
                        ACCEL + accel_suffix, GYRO + gyro_suffix,
                    ]),
                    [expected],
                )

    def test_slope_marker_and_both_sensor_formats_must_match(self):
        mismatches = [
            ["Sample 1", ACCEL_SLOPE, GYRO_SLOPE],
            ["Sample 1 TimeMs=100", ACCEL_SLOPE, GYRO_SLOPE],
            ["Sample 1 SlopeWindow=5", ACCEL_SLOPE, GYRO_SLOPE],
            ["Sample 1 TimeMs=100 SlopeWindow=5", ACCEL, GYRO],
            ["Sample 1 TimeMs=100 SlopeWindow=5", ACCEL_EXTENDED, GYRO_EXTENDED],
            ["Sample 1 TimeMs=100 SlopeWindow=5", ACCEL_SLOPE, GYRO_EXTENDED],
            ["Sample 1 TimeMs=100 SlopeWindow=5", ACCEL_EXTENDED, GYRO_SLOPE],
        ]
        for lines in mismatches:
            with self.subTest(lines=lines):
                self.assertEqual(self.feed_all(lines), [])

    def test_slope_window_size_is_bounded_but_not_assumed_to_be_five(self):
        for window in (2, 5, 4294967295):
            with self.subTest(valid_window=window):
                self.assertEqual(
                    self.feed_all([f"Sample 1 TimeMs=100 SlopeWindow={window}", ACCEL_SLOPE, GYRO_SLOPE]),
                    [expected_slope_sample(1, 100, window)],
                )
        for window in ("0", "1", "-5", "5.0", "4294967296", "9" * 5000, "NA"):
            with self.subTest(invalid_window=window[:20]):
                self.assertEqual(
                    self.feed_all([f"Sample 1 TimeMs=100 SlopeWindow={window}", ACCEL_SLOPE, GYRO_SLOPE]),
                    [],
                )

    def test_partial_nonfinite_slopes_or_negative_msd_are_rejected(self):
        suffixes = [
            " MSD=1 AvgSlope=-2", " MSD=1 MSDSlope=-3",
            " MSD=-1 AvgSlope=-2 MSDSlope=-3", " MSD=1 AvgSlope=nan MSDSlope=-3",
            " MSD=1 AvgSlope=-2 MSDSlope=1e999", " MSD=inf AvgSlope=-2 MSDSlope=-3",
        ]
        for suffix in suffixes:
            for bad_accel in (True, False):
                with self.subTest(suffix=suffix, bad_accel=bad_accel):
                    lines = [ACCEL + suffix, GYRO_SLOPE] if bad_accel else [ACCEL_SLOPE, GYRO + suffix]
                    self.assertEqual(self.feed_all(["Sample 7 TimeMs=700 SlopeWindow=5"] + lines), [])
                    self.assertEqual(
                        self.feed_all(["Sample 8 TimeMs=800 SlopeWindow=5", ACCEL_SLOPE, GYRO_SLOPE]),
                        [expected_slope_sample(8, 800)],
                    )


class RecordingDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = str(Path(self.temp_dir.name) / "activity.sqlite3")

    def test_reopen_appends_distinct_sessions_and_preserves_recorded_values(self):
        first = RecordingDatabase(self.path)
        try:
            normal = first.start_session("normal", "Walking indoors", "test-port")
            first.append_sample(normal, expected_sample(0))
            first.append_sample(normal, expected_sample(1))
            first.finish_session(normal)
        finally:
            first.close()

        second = RecordingDatabase(self.path)
        try:
            near_fall = second.start_session("near_fall", "Second run", "test-port")
            second.append_sample(near_fall, expected_sample(0))
            second.finish_session(near_fall)
        finally:
            second.close()

        self.assertNotEqual(normal, near_fall)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2)
            rows = connection.execute(
                "SELECT id, session_id, sample_number, accel_x_mps2, accel_y_mps2, "
                "accel_z_mps2, accel_avg_mps2, gyro_x_dps, gyro_y_dps, gyro_z_dps, "
                "gyro_avg_dps FROM samples ORDER BY id"
            ).fetchall()

        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row[0] for row in rows}), 3)
        self.assertEqual([row[1:3] for row in rows], [(normal, 0), (normal, 1), (near_fall, 0)])
        for row in rows:
            self.assertEqual(row[3:], (1.25, -2.5, 9.8, 2.85, -30.0, 15.0, 0.0, -5.0))

    def test_counter_reset_inside_session_does_not_overwrite_prior_samples(self):
        database = RecordingDatabase(self.path)
        try:
            session = database.start_session("normal", "Device restarted", "test-port")
            database.append_sample(session, expected_sample(0))
            changed = expected_sample(0)
            changed["accel_x_mps2"] = -7.0
            database.append_sample(session, changed)
            database.finish_session(session)
        finally:
            database.close()

        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT sample_number, accel_x_mps2 FROM samples ORDER BY id"
            ).fetchall()
        self.assertEqual(rows, [(0, 1.25), (0, -7.0)])


class CSVRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        directory = Path(self.temp_dir.name)
        self.path = directory / "activity.csv"
        self.database = RecordingDatabase(directory / "activity.sqlite3")
        self.addCleanup(self.database.close)
        self.session = self.database.start_session(
            "normal", 'Walking, "indoors"\nSecond line', "test-port"
        )

    def append_to_database(self, number):
        return self.database.append_sample(self.session, expected_sample(number))

    def read_rows(self):
        with self.path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def expected_csv_rows(self, rows):
        return [
            {key: "" if row[key] is None else str(row[key]) for key in CSV_FIELDS}
            for row in rows
        ]

    def write_csv(self, rows, fields=CSV_FIELDS):
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_new_csv_backfills_existing_database_including_quoted_notes(self):
        rows = [self.append_to_database(0), self.append_to_database(1)]
        recorder = CSVRecorder(self.path, self.database)
        recorder.close()
        self.assertEqual(self.read_rows(), self.expected_csv_rows(rows))

    def test_reopen_recovers_database_rows_missing_from_csv_tail(self):
        first = self.append_to_database(0)
        recorder = CSVRecorder(self.path, self.database)
        recorder.close()
        original_prefix = self.path.read_bytes()

        # Simulate a stop after the durable database write but before CSV append.
        rows = [first, self.append_to_database(1), self.append_to_database(2)]
        recovered = CSVRecorder(self.path, self.database)
        recovered.close()

        self.assertTrue(self.path.read_bytes().startswith(original_prefix))
        self.assertEqual(self.read_rows(), self.expected_csv_rows(rows))

    def test_reopen_complete_csv_does_not_duplicate_or_rewrite_records(self):
        first = self.append_to_database(0)
        recorder = CSVRecorder(self.path, self.database)
        second = self.append_to_database(1)
        recorder.append(second)
        recorder.close()
        before = self.path.read_bytes()

        reopened = CSVRecorder(self.path, self.database)
        reopened.close()

        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.read_rows(), self.expected_csv_rows([first, second]))

    def test_rejects_edited_value_or_mismatched_record_without_modifying_csv(self):
        row = self.append_to_database(0)
        for field, changed_value in (("gyro_x_dps", 999.0), ("record_id", 999)):
            with self.subTest(field=field):
                edited = dict(row)
                edited[field] = changed_value
                self.write_csv([edited])
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    CSVRecorder(self.path, self.database)
                self.assertEqual(self.path.read_bytes(), before)

    def test_rejects_mismatched_header_without_modifying_csv(self):
        row = self.append_to_database(0)
        self.write_csv([row], fields=tuple(reversed(CSV_FIELDS)))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            CSVRecorder(self.path, self.database)
        self.assertEqual(self.path.read_bytes(), before)

    def test_header_without_trailing_newline_can_receive_database_backfill(self):
        self.write_csv([])
        self.path.write_bytes(self.path.read_bytes().rstrip(b"\r\n"))
        row = self.append_to_database(0)

        recorder = CSVRecorder(self.path, self.database)
        recorder.close()

        self.assertEqual(self.read_rows(), self.expected_csv_rows([row]))

    def test_last_record_without_trailing_newline_can_receive_next_live_sample(self):
        first = self.append_to_database(0)
        recorder = CSVRecorder(self.path, self.database)
        recorder.close()
        self.path.write_bytes(self.path.read_bytes().rstrip(b"\r\n"))

        reopened = CSVRecorder(self.path, self.database)
        second = self.append_to_database(1)
        reopened.append(second)
        reopened.close()

        self.assertEqual(self.read_rows(), self.expected_csv_rows([first, second]))


class LegacyRecordingMigrationTests(unittest.TestCase):
    """Start from the actual previous schema, without using the updated API."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.directory = Path(self.temp_dir.name)
        self.db_path = self.directory / "old.sqlite3"
        self.csv_path = self.directory / "old.csv"
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript("""
                CREATE TABLE sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at_utc TEXT NOT NULL,
                    ended_at_utc TEXT,
                    activity TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    port TEXT NOT NULL
                );
                CREATE TABLE samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id),
                    timestamp_utc TEXT NOT NULL,
                    sample_number INTEGER NOT NULL,
                    accel_x_mps2 REAL NOT NULL,
                    accel_y_mps2 REAL NOT NULL,
                    accel_z_mps2 REAL NOT NULL,
                    accel_avg_mps2 REAL NOT NULL,
                    gyro_x_dps REAL NOT NULL,
                    gyro_y_dps REAL NOT NULL,
                    gyro_z_dps REAL NOT NULL,
                    gyro_avg_dps REAL NOT NULL
                );
                CREATE INDEX samples_session ON samples(session_id);
                CREATE VIEW readings AS
                    SELECT p.id AS record_id, p.session_id, p.timestamp_utc,
                           s.activity, s.notes, p.sample_number,
                           p.accel_x_mps2, p.accel_y_mps2, p.accel_z_mps2,
                           p.accel_avg_mps2, p.gyro_x_dps, p.gyro_y_dps,
                           p.gyro_z_dps, p.gyro_avg_dps
                    FROM samples p JOIN sessions s ON p.session_id = s.id;
            """)
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                (7, "2026-09-23T00:00:00+00:00", "2026-09-23T00:01:00+00:00",
                 "normal", 'Original, "label"\nSecond line', "old-port"),
            )
            for row_id, number in ((41, 10), (42, 11)):
                connection.execute(
                    "INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row_id, 7, f"2026-09-23T00:00:{number}+00:00", number,
                     1.25, -2.5, 9.8, 2.85, -30.0, 15.0, 0.0, -5.0),
                )
            connection.commit()
            self.legacy_rows = [
                dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")
            ]
            self.legacy_session = dict(connection.execute("SELECT * FROM sessions").fetchone())
        finally:
            connection.close()

    def open_database(self):
        database = RecordingDatabase(self.db_path)
        self.addCleanup(database.close)
        return database

    def write_legacy_csv(self, rows):
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=LEGACY_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def read_csv(self):
        with self.csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return tuple(reader.fieldnames), list(reader)

    def test_migrates_real_old_schema_without_replacing_rows_or_inventing_metrics(self):
        database = self.open_database()
        columns = {
            row["name"]: row for row in database.connection.execute("PRAGMA table_info(samples)")
        }
        for field in EXTRA_FIELDS + SLOPE_FIELDS:
            self.assertIn(field, columns)
            self.assertEqual(columns[field]["notnull"], 0)

        migrated = [dict(row) for row in database.connection.execute("SELECT * FROM readings ORDER BY record_id")]
        self.assertEqual(len(migrated), 2)
        for old, new in zip(self.legacy_rows, migrated):
            self.assertEqual({key: new[key] for key in LEGACY_FIELDS}, old)
            self.assertEqual({key: new[key] for key in EXTRA_FIELDS}, dict.fromkeys(EXTRA_FIELDS))
            self.assertTrue(all(new[key] is None for key in SLOPE_FIELDS))
        self.assertEqual(
            dict(database.connection.execute("SELECT * FROM sessions WHERE id = 7").fetchone()),
            self.legacy_session,
        )

        new_session = database.start_session("near_fall", "New firmware", "new-port")
        self.assertGreater(new_session, 7)
        new_sample = expected_extended_sample(0, 100)
        row = database.append_sample(new_session, new_sample)
        self.assertGreater(row["record_id"], 42)
        for key, value in new_sample.items():
            self.assertEqual(row[key], value)
        legacy_format_row = database.append_sample(new_session, expected_sample(1))
        self.assertTrue(all(legacy_format_row[key] is None for key in EXTRA_FIELDS))
        database.finish_session(new_session)
        database.close()

        reopened = self.open_database()
        persisted = [dict(row) for row in reopened.connection.execute("SELECT * FROM readings ORDER BY record_id")]
        self.assertEqual(persisted, migrated + [row, legacy_format_row])

    def test_upgrades_original_csv_header_and_backfills_missing_old_and_new_rows(self):
        self.write_legacy_csv(self.legacy_rows[:1])
        database = self.open_database()
        new_session = database.start_session("normal", "Upgraded firmware", "new-port")
        extended = database.append_sample(new_session, expected_extended_sample(0, 100))

        recorder = CSVRecorder(self.csv_path, database)
        recorder.close()
        header, rows = self.read_csv()

        self.assertEqual(header, LEGACY_FIELDS + EXTRA_FIELDS + SLOPE_FIELDS)
        self.assertEqual(header, tuple(CSV_FIELDS))
        self.assertEqual([row["record_id"] for row in rows], ["41", "42", str(extended["record_id"])])
        for old, new in zip(self.legacy_rows, rows):
            self.assertEqual({key: new[key] for key in LEGACY_FIELDS}, {key: str(value) for key, value in old.items()})
            self.assertTrue(all(new[key] == "" for key in EXTRA_FIELDS))
            self.assertTrue(all(new[key] == "" for key in SLOPE_FIELDS))
        for key in EXTRA_FIELDS:
            self.assertEqual(rows[-1][key], str(extended[key]))

        before_reopen = self.csv_path.read_bytes()
        reopened = CSVRecorder(self.csv_path, database)
        reopened.close()
        self.assertEqual(self.csv_path.read_bytes(), before_reopen)

    def test_edited_legacy_csv_is_rejected_before_any_upgrade(self):
        database = self.open_database()
        for field, wrong_value in (("gyro_y_dps", 900.0), ("record_id", 80)):
            with self.subTest(field=field):
                edited = dict(self.legacy_rows[0])
                edited[field] = wrong_value
                self.write_legacy_csv([edited])
                before = self.csv_path.read_bytes()
                with self.assertRaises(ValueError):
                    CSVRecorder(self.csv_path, database)
                self.assertEqual(self.csv_path.read_bytes(), before)

    def test_failed_atomic_upgrade_preserves_original_csv_and_cleans_temporary_file(self):
        self.write_legacy_csv(self.legacy_rows)
        database = self.open_database()
        before = self.csv_path.read_bytes()
        existing_files = {path.name for path in self.directory.iterdir()}
        with mock.patch("record_activity.os.replace", side_effect=OSError("injected replace failure")):
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                CSVRecorder(self.csv_path, database)
        self.assertEqual(self.csv_path.read_bytes(), before)
        self.assertEqual({path.name for path in self.directory.iterdir()}, existing_files)

    def test_failed_schema_migration_rolls_back_new_columns_and_original_view(self):
        class FailViewCreationConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.lstrip().upper().startswith("CREATE VIEW READINGS AS"):
                    raise sqlite3.OperationalError("injected view creation failure")
                return super().execute(sql, parameters)

        connection = sqlite3.connect(self.db_path, factory=FailViewCreationConnection)
        try:
            with mock.patch("record_activity.sqlite3.connect", return_value=connection):
                with self.assertRaisesRegex(sqlite3.OperationalError, "injected view creation failure"):
                    RecordingDatabase(self.db_path)
            self.assertFalse(connection.in_transaction)
        finally:
            connection.close()

        original = sqlite3.connect(self.db_path)
        original.row_factory = sqlite3.Row
        try:
            columns = {row["name"] for row in original.execute("PRAGMA table_info(samples)")}
            self.assertTrue(columns.isdisjoint(EXTRA_FIELDS + SLOPE_FIELDS))
            self.assertEqual(
                [dict(row) for row in original.execute("SELECT * FROM readings ORDER BY record_id")],
                self.legacy_rows,
            )
            self.assertEqual(
                dict(original.execute("SELECT * FROM sessions WHERE id = 7").fetchone()),
                self.legacy_session,
            )
        finally:
            original.close()


class CumulativeRecordingMigrationTests(unittest.TestCase):
    """Also preserve the intermediate format containing cumulative rates."""

    open_database = LegacyRecordingMigrationTests.open_database
    read_csv = LegacyRecordingMigrationTests.read_csv

    def setUp(self):
        LegacyRecordingMigrationTests.setUp(self)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            for field in EXTRA_FIELDS:
                kind = "INTEGER" if field == "board_time_ms" else "REAL"
                connection.execute(f"ALTER TABLE samples ADD COLUMN {field} {kind}")
            connection.execute("UPDATE samples SET board_time_ms = 0 WHERE id = 41")
            connection.execute("""
                UPDATE samples SET board_time_ms = 100,
                    accel_msd = 0.125, accel_avg_rate = 2.5, accel_msd_rate = 0.75,
                    gyro_msd = 25, gyro_avg_rate = 50, gyro_msd_rate = 100
                WHERE id = 42
            """)
            connection.execute("DROP VIEW readings")
            connection.execute("""
                CREATE VIEW readings AS
                    SELECT p.id AS record_id, p.session_id, p.timestamp_utc,
                           s.activity, s.notes, p.sample_number,
                           p.accel_x_mps2, p.accel_y_mps2, p.accel_z_mps2,
                           p.accel_avg_mps2, p.gyro_x_dps, p.gyro_y_dps,
                           p.gyro_z_dps, p.gyro_avg_dps,
                           p.board_time_ms, p.accel_msd, p.accel_avg_rate,
                           p.accel_msd_rate, p.gyro_msd, p.gyro_avg_rate, p.gyro_msd_rate
                    FROM samples p JOIN sessions s ON p.session_id = s.id
            """)
            connection.commit()
            self.cumulative_rows = [
                dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")
            ]
        finally:
            connection.close()

    def write_cumulative_csv(self, rows):
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=LEGACY_FIELDS + EXTRA_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_existing_rates_are_preserved_and_new_slopes_use_separate_nullable_columns(self):
        database = self.open_database()
        migrated = [dict(row) for row in database.connection.execute("SELECT * FROM readings ORDER BY record_id")]
        for old, new in zip(self.cumulative_rows, migrated):
            self.assertEqual({key: new[key] for key in LEGACY_FIELDS + EXTRA_FIELDS}, old)
            self.assertTrue(all(new[key] is None for key in SLOPE_FIELDS))

        session = database.start_session("normal", "Sliding regression", "test-port")
        slope_sample = expected_slope_sample(0, 100)
        row = database.append_sample(session, slope_sample)
        for key, value in slope_sample.items():
            self.assertEqual(row[key], value)
        for field in ("accel_avg_rate", "accel_msd_rate", "gyro_avg_rate", "gyro_msd_rate"):
            self.assertIsNone(row[field])
        self.assertGreater(row["record_id"], 42)
        database.close()

        reopened = self.open_database()
        persisted = [dict(row) for row in reopened.connection.execute("SELECT * FROM readings ORDER BY record_id")]
        self.assertEqual(persisted, migrated + [row])

    def test_cumulative_csv_upgrades_and_recovers_tail_without_reinterpreting_rates(self):
        self.write_cumulative_csv(self.cumulative_rows[:1])
        database = self.open_database()
        session = database.start_session("normal", "Sliding regression", "test-port")
        slope_row = database.append_sample(session, expected_slope_sample(0, 100))

        recorder = CSVRecorder(self.csv_path, database)
        recorder.close()
        header, rows = self.read_csv()
        self.assertEqual(header, LEGACY_FIELDS + EXTRA_FIELDS + SLOPE_FIELDS)
        self.assertEqual([row["record_id"] for row in rows], ["41", "42", str(slope_row["record_id"])])
        for old, new in zip(self.cumulative_rows, rows):
            for key in LEGACY_FIELDS + EXTRA_FIELDS:
                self.assertEqual(new[key], "" if old[key] is None else str(old[key]))
            self.assertTrue(all(new[key] == "" for key in SLOPE_FIELDS))
        for key in SLOPE_FIELDS:
            self.assertEqual(rows[-1][key], str(slope_row[key]))
        for key in ("accel_avg_rate", "accel_msd_rate", "gyro_avg_rate", "gyro_msd_rate"):
            self.assertEqual(rows[-1][key], "")

        before_reopen = self.csv_path.read_bytes()
        reopened = CSVRecorder(self.csv_path, database)
        reopened.close()
        self.assertEqual(self.csv_path.read_bytes(), before_reopen)

    def test_edited_historical_rate_prevents_csv_upgrade_without_changing_file(self):
        edited = dict(self.cumulative_rows[1])
        edited["accel_avg_rate"] = 999.0
        self.write_cumulative_csv([self.cumulative_rows[0], edited])
        before = self.csv_path.read_bytes()
        database = self.open_database()
        with self.assertRaises(ValueError):
            CSVRecorder(self.csv_path, database)
        self.assertEqual(self.csv_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
