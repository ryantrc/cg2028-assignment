"""Host-side checks for complete serial samples and durable SQLite recording.

Run with: python3 -m unittest discover -s tools -p 'test_record_activity.py'
"""

import csv
import errno
import io
import math
import os
import select
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import record_activity
from record_activity import CSV_FIELDS, CSVRecorder, RecordingDatabase, SampleParser


ACCEL = "Accel EWMA ASM [m/s^2]: X=   1.250 Y=  -2.500 Z=   9.800 Magnitude=  10.191"
GYRO = "Gyro  EWMA ASM [dps]  : X= -30.000 Y=  15.000 Z=   0.000 Magnitude=  33.541"
ACCEL_EXTENDED = ACCEL + " MSD=0.125 AvgRate=2.500 MSDRate=0.750"
GYRO_EXTENDED = GYRO + " MSD=25.000 AvgRate=50.000 MSDRate=100.000"
ACCEL_SLOPE = ACCEL + " MSD=0.125 MagnitudeSlope=-2.500 MSDSlope=0.750"
GYRO_SLOPE = GYRO + " MSD=25.000 MagnitudeSlope=50.000 MSDSlope=-100.000"
MAGNITUDE_FIELDS = (
    "record_id", "session_id", "timestamp_utc", "activity", "notes", "sample_number",
    "accel_x_mps2", "accel_y_mps2", "accel_z_mps2", "accel_magnitude_mps2",
    "gyro_x_dps", "gyro_y_dps", "gyro_z_dps", "gyro_magnitude_dps",
)
EXTRA_FIELDS = (
    "board_time_ms", "accel_msd", "accel_avg_rate", "accel_msd_rate",
    "gyro_msd", "gyro_avg_rate", "gyro_msd_rate",
)
SLOPE_FIELDS = (
    "slope_window_samples", "accel_magnitude_slope", "accel_msd_slope",
    "gyro_magnitude_slope", "gyro_msd_slope",
)
RETIRED_RATE_FIELDS = (
    "accel_avg_rate", "accel_msd_rate", "gyro_avg_rate", "gyro_msd_rate",
)
CURRENT_METRIC_FIELDS = ("board_time_ms", "accel_msd", "gyro_msd")
LEGACY_FIELDS = tuple(field.replace("magnitude", "avg") for field in MAGNITUDE_FIELDS)
LEGACY_SLOPE_FIELDS = tuple(field.replace("magnitude", "avg") for field in SLOPE_FIELDS)
LEGACY_AVERAGE_FIELDS = ("accel_avg_mps2", "gyro_avg_dps", "accel_avg_slope", "gyro_avg_slope")
CLEAN_CSV_FIELDS = MAGNITUDE_FIELDS + CURRENT_METRIC_FIELDS + SLOPE_FIELDS
PREVIOUS_CSV_FIELDS = LEGACY_FIELDS + EXTRA_FIELDS + LEGACY_SLOPE_FIELDS
AVERAGE_CSV_FIELDS = LEGACY_FIELDS + CURRENT_METRIC_FIELDS + LEGACY_SLOPE_FIELDS


def expected_sample(number):
    return {
        "sample_number": number,
        "accel_x_mps2": 1.25,
        "accel_y_mps2": -2.5,
        "accel_z_mps2": 9.8,
        "accel_magnitude_mps2": 10.191,
        "gyro_x_dps": -30.0,
        "gyro_y_dps": 15.0,
        "gyro_z_dps": 0.0,
        "gyro_magnitude_dps": 33.541,
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
        "accel_magnitude_slope": -2.5,
        "accel_msd_slope": 0.75,
        "gyro_magnitude_slope": 50.0,
        "gyro_msd_slope": -100.0,
    }


def expected_legacy_slope_sample(number, board_time_ms, window=5):
    """Fixture from the old Avg firmware, not from the current storage API."""
    sample = {
        key.replace("magnitude", "avg"): value
        for key, value in expected_slope_sample(number, board_time_ms, window).items()
    }
    sample["accel_avg_mps2"] = 2.85
    sample["gyro_avg_dps"] = -5.0
    return sample


def expected_migrated_row(old, accel_slope=None, gyro_slope=None):
    """Recompute geometry independently; never reinterpret an old average."""
    result = {key: old.get(key) for key in CLEAN_CSV_FIELDS}
    result["accel_magnitude_mps2"] = math.sqrt(sum(
        old[f"accel_{axis}_mps2"] ** 2 for axis in "xyz"
    ))
    result["gyro_magnitude_dps"] = math.sqrt(sum(
        old[f"gyro_{axis}_dps"] ** 2 for axis in "xyz"
    ))
    result["accel_magnitude_slope"] = accel_slope
    result["gyro_magnitude_slope"] = gyro_slope
    return result


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
            "accel missing magnitude": [ACCEL.rsplit(" Magnitude=", 1)[0], GYRO],
            "gyro missing magnitude": [ACCEL, GYRO.rsplit(" Magnitude=", 1)[0]],
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

    def test_old_average_firmware_fails_clearly_even_without_header(self):
        old_accel = ACCEL.replace("Magnitude=  10.191", "Avg=   2.850")
        old_gyro = GYRO.replace("Magnitude=  33.541", "Avg=  -5.000")
        for header in (None, "Sample 1", "Sample 1 TimeMs=100 SlopeWindow=5"):
            for sensor in (old_accel, old_gyro, old_accel + " MSD=1 AvgSlope=2 MSDSlope=3"):
                with self.subTest(header=header, sensor=sensor):
                    if header:
                        self.parser.feed(header)
                    with self.assertRaisesRegex(ValueError, r"(?i)rebuild.*flash"):
                        self.parser.feed(sensor)
                    # Failed old data must not complete a subsequent gyro frame.
                    self.assertIsNone(self.parser.feed(GYRO))
                    self.assertEqual(
                        self.feed_all(["Sample 2", ACCEL, GYRO]), [expected_sample(2)]
                    )

    def test_magnitude_is_finite_and_nonnegative_while_axes_remain_signed(self):
        for invalid in ("-1.0", "nan", "inf", "1e999"):
            for bad_accel in (True, False):
                with self.subTest(invalid=invalid, bad_accel=bad_accel):
                    accel = ACCEL.replace("10.191", invalid) if bad_accel else ACCEL
                    gyro = GYRO if bad_accel else GYRO.replace("33.541", invalid)
                    self.assertEqual(self.feed_all(["Sample 1", accel, gyro]), [])
        expected = expected_sample(2)
        expected["accel_magnitude_mps2"] = expected["gyro_magnitude_dps"] = 0.0
        self.assertEqual(
            self.feed_all(["Sample 2", ACCEL.replace("10.191", "0"), GYRO.replace("33.541", "0")]),
            [expected],
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
        for sample_number, magnitude_slope_available in ((0, False), (4, True)):
            with self.subTest(sample_number=sample_number):
                first = sample_number == 0
                expected = expected_slope_sample(sample_number, sample_number * 100)
                if first:
                    expected["accel_msd"] = expected["gyro_msd"] = None
                expected["accel_msd_slope"] = expected["gyro_msd_slope"] = None
                if not magnitude_slope_available:
                    expected["accel_magnitude_slope"] = expected["gyro_magnitude_slope"] = None
                accel_suffix = (
                    f" MSD={'NA' if first else '0.125'}"
                    f" MagnitudeSlope={'-2.500' if magnitude_slope_available else 'NA'} MSDSlope=NA"
                )
                gyro_suffix = (
                    f" MSD={'NA' if first else '25.000'}"
                    f" MagnitudeSlope={'50.000' if magnitude_slope_available else 'NA'} MSDSlope=NA"
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
            " MSD=1 MagnitudeSlope=-2", " MSD=1 MSDSlope=-3",
            " MSD=-1 MagnitudeSlope=-2 MSDSlope=-3", " MSD=1 MagnitudeSlope=nan MSDSlope=-3",
            " MSD=1 MagnitudeSlope=-2 MSDSlope=1e999", " MSD=inf MagnitudeSlope=-2 MSDSlope=-3",
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
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2)
            rows = connection.execute(
                "SELECT id, session_id, sample_number, accel_x_mps2, accel_y_mps2, "
                "accel_z_mps2, accel_magnitude_mps2, gyro_x_dps, gyro_y_dps, gyro_z_dps, "
                "gyro_magnitude_dps FROM samples ORDER BY id"
            ).fetchall()

        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row[0] for row in rows}), 3)
        self.assertEqual([row[1:3] for row in rows], [(normal, 0), (normal, 1), (near_fall, 0)])
        for row in rows:
            self.assertEqual(row[3:], (1.25, -2.5, 9.8, 10.191, -30.0, 15.0, 0.0, 33.541))

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

        with closing(sqlite3.connect(self.path)) as connection, connection:
            rows = connection.execute(
                "SELECT sample_number, accel_x_mps2 FROM samples ORDER BY id"
            ).fetchall()
        self.assertEqual(rows, [(0, 1.25), (0, -7.0)])

    def test_fresh_database_has_only_magnitude_fields_and_rejects_old_average_keys(self):
        database = RecordingDatabase(self.path)
        self.addCleanup(database.close)
        columns = {row["name"] for row in database.connection.execute("PRAGMA table_info(samples)")}
        self.assertTrue(columns.isdisjoint(LEGACY_AVERAGE_FIELDS + RETIRED_RATE_FIELDS))
        self.assertTrue(set(SLOPE_FIELDS).issubset(columns))
        self.assertFalse((Path(self.path).parent / "backups").exists())
        session = database.start_session("normal", "Magnitude firmware", "test-port")
        for field in LEGACY_AVERAGE_FIELDS:
            with self.subTest(field=field):
                sample = {**expected_slope_sample(0, 0), field: None}
                with self.assertRaisesRegex(ValueError, r"(?i)rebuild.*flash"):
                    database.append_sample(session, sample)
        self.assertEqual(database.connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 0)
        row = database.append_sample(session, expected_slope_sample(0, 0))
        # Live printed values are recorded exactly, unlike a historical backfill.
        self.assertEqual(row["accel_magnitude_mps2"], 10.191)
        self.assertEqual(row["gyro_magnitude_dps"], 33.541)


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

    def test_migrates_real_old_schema_recomputing_magnitude_without_inventing_missing_metrics(self):
        database = self.open_database()
        columns = {
            row["name"]: row for row in database.connection.execute("PRAGMA table_info(samples)")
        }
        for field in CURRENT_METRIC_FIELDS + SLOPE_FIELDS:
            self.assertIn(field, columns)
            self.assertEqual(columns[field]["notnull"], 0)
        self.assertTrue(set(columns).isdisjoint(RETIRED_RATE_FIELDS + LEGACY_AVERAGE_FIELDS))

        migrated = [dict(row) for row in database.connection.execute("SELECT * FROM readings ORDER BY record_id")]
        self.assertEqual(len(migrated), 2)
        for old, new in zip(self.legacy_rows, migrated):
            self.assertEqual(new, expected_migrated_row(old))
            self.assertEqual({key: new[key] for key in CURRENT_METRIC_FIELDS}, dict.fromkeys(CURRENT_METRIC_FIELDS))
            self.assertTrue(all(new[key] is None for key in SLOPE_FIELDS))
        self.assertEqual(
            dict(database.connection.execute("SELECT * FROM sessions WHERE id = 7").fetchone()),
            self.legacy_session,
        )

        new_session = database.start_session("near_fall", "New firmware", "new-port")
        self.assertGreater(new_session, 7)
        new_sample = expected_slope_sample(0, 100)
        row = database.append_sample(new_session, new_sample)
        self.assertGreater(row["record_id"], 42)
        for key, value in new_sample.items():
            self.assertEqual(row[key], value)
        legacy_format_row = database.append_sample(new_session, expected_sample(1))
        self.assertTrue(all(legacy_format_row[key] is None for key in CURRENT_METRIC_FIELDS + SLOPE_FIELDS))
        database.finish_session(new_session)
        database.close()

        reopened = self.open_database()
        persisted = [dict(row) for row in reopened.connection.execute("SELECT * FROM readings ORDER BY record_id")]
        self.assertEqual(persisted, migrated + [row, legacy_format_row])

    def test_upgrades_original_csv_header_and_backfills_missing_old_and_new_rows(self):
        self.write_legacy_csv(self.legacy_rows[:1])
        database = self.open_database()
        new_session = database.start_session("normal", "Upgraded firmware", "new-port")
        extended = database.append_sample(new_session, expected_slope_sample(0, 100))

        recorder = CSVRecorder(self.csv_path, database)
        recorder.close()
        header, rows = self.read_csv()

        self.assertEqual(header, CLEAN_CSV_FIELDS)
        self.assertEqual(header, tuple(CSV_FIELDS))
        self.assertEqual([row["record_id"] for row in rows], ["41", "42", str(extended["record_id"])])
        for old, new in zip(self.legacy_rows, rows):
            expected = expected_migrated_row(old)
            self.assertEqual(new, {key: "" if value is None else str(value) for key, value in expected.items()})
            self.assertTrue(all(new[key] == "" for key in CURRENT_METRIC_FIELDS))
            self.assertTrue(all(new[key] == "" for key in SLOPE_FIELDS))
        for key in CURRENT_METRIC_FIELDS + SLOPE_FIELDS:
            self.assertEqual(rows[-1][key], str(extended[key]))

        before_reopen = self.csv_path.read_bytes()
        reopened = CSVRecorder(self.csv_path, database)
        reopened.close()
        self.assertEqual(self.csv_path.read_bytes(), before_reopen)

    def test_edited_legacy_csv_is_rejected_before_any_upgrade(self):
        database = self.open_database()
        for field, wrong_value in (("gyro_y_dps", 900.0), ("record_id", 80), ("accel_avg_mps2", 10.191)):
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

        connect = sqlite3.connect
        connection = connect(self.db_path, factory=FailViewCreationConnection)

        def connect_with_injected_failure(path, *args, **kwargs):
            if Path(path).resolve() == self.db_path.resolve():
                return connection
            return connect(path, *args, **kwargs)

        try:
            with mock.patch("record_activity.sqlite3.connect", side_effect=connect_with_injected_failure):
                with self.assertRaisesRegex(sqlite3.OperationalError, "injected view creation failure"):
                    RecordingDatabase(self.db_path)
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
    """Remove empty retired columns while refusing to discard actual readings."""

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

    def clear_recorded_rates(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("UPDATE samples SET " + ", ".join(f"{field} = NULL" for field in RETIRED_RATE_FIELDS))
            connection.commit()
            self.cumulative_rows = [
                dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")
            ]
        finally:
            connection.close()

    def schema_and_data_snapshot(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            return {
                "schema": [tuple(row) for row in connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )],
                "readings": [dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")],
                "sessions": [dict(row) for row in connection.execute("SELECT * FROM sessions ORDER BY id")],
            }
        finally:
            connection.close()

    def test_populated_legacy_rates_block_migration_without_changing_schema_or_data(self):
        before = self.schema_and_data_snapshot()
        with self.assertRaises(ValueError):
            RecordingDatabase(self.db_path)
        self.assertEqual(self.schema_and_data_snapshot(), before)

        # Zero is an actual recorded measurement, not an empty column.
        self.clear_recorded_rates()
        for field in RETIRED_RATE_FIELDS:
            with self.subTest(field=field):
                with closing(sqlite3.connect(self.db_path)) as connection, connection:
                    connection.execute(f"UPDATE samples SET {field} = 0 WHERE id = 42")
                before = self.schema_and_data_snapshot()
                with self.assertRaises(ValueError):
                    RecordingDatabase(self.db_path)
                self.assertEqual(self.schema_and_data_snapshot(), before)
                with closing(sqlite3.connect(self.db_path)) as connection, connection:
                    connection.execute(f"UPDATE samples SET {field} = NULL")

    def test_new_appends_reject_retired_rate_keys_even_when_value_is_none(self):
        database = RecordingDatabase(self.directory / "fresh.sqlite3")
        self.addCleanup(database.close)
        session = database.start_session("normal", "Current firmware", "test-port")
        for field in RETIRED_RATE_FIELDS:
            for value in (None, 0.0, 2.5):
                with self.subTest(field=field, value=value):
                    sample = {**expected_slope_sample(0, 100), field: value}
                    with self.assertRaises(ValueError):
                        database.append_sample(session, sample)
        self.assertEqual(database.connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 0)
        row = database.append_sample(session, expected_slope_sample(0, 100))
        self.assertTrue(set(row).isdisjoint(RETIRED_RATE_FIELDS))

    def test_empty_cumulative_columns_are_removed_and_csv_recovers_missing_tail(self):
        self.clear_recorded_rates()
        self.write_cumulative_csv(self.cumulative_rows[:1])
        database = self.open_database()
        session = database.start_session("normal", "Sliding regression", "test-port")
        slope_row = database.append_sample(session, expected_slope_sample(0, 100))

        recorder = CSVRecorder(self.csv_path, database)
        recorder.close()
        header, rows = self.read_csv()
        self.assertEqual(header, CLEAN_CSV_FIELDS)
        self.assertEqual([row["record_id"] for row in rows], ["41", "42", str(slope_row["record_id"])])
        for old, new in zip(self.cumulative_rows, rows):
            expected = expected_migrated_row(old)
            self.assertEqual(new, {key: "" if value is None else str(value) for key, value in expected.items()})
            self.assertTrue(all(new[key] == "" for key in SLOPE_FIELDS))
        for key in SLOPE_FIELDS:
            self.assertEqual(rows[-1][key], str(slope_row[key]))
        self.assertTrue(set(header).isdisjoint(RETIRED_RATE_FIELDS))

        before_reopen = self.csv_path.read_bytes()
        reopened = CSVRecorder(self.csv_path, database)
        reopened.close()
        self.assertEqual(self.csv_path.read_bytes(), before_reopen)

    def test_nonempty_retired_or_edited_retained_csv_values_prevent_upgrade(self):
        self.clear_recorded_rates()
        database = self.open_database()
        for field in RETIRED_RATE_FIELDS + ("accel_msd",):
            with self.subTest(field=field):
                edited = dict(self.cumulative_rows[1])
                edited[field] = 0.0 if field in RETIRED_RATE_FIELDS else 999.0
                self.write_cumulative_csv([self.cumulative_rows[0], edited])
                before = self.csv_path.read_bytes()
                with self.assertRaises(ValueError):
                    CSVRecorder(self.csv_path, database)
                self.assertEqual(self.csv_path.read_bytes(), before)


class PreviousSlopeSchemaMigrationTests(unittest.TestCase):
    """Exercise the real 26-column export with populated slopes and empty rates."""

    open_database = LegacyRecordingMigrationTests.open_database
    read_csv = LegacyRecordingMigrationTests.read_csv
    schema_and_data_snapshot = CumulativeRecordingMigrationTests.schema_and_data_snapshot

    def setUp(self):
        CumulativeRecordingMigrationTests.setUp(self)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            for field in LEGACY_SLOPE_FIELDS:
                kind = "INTEGER" if field == "slope_window_samples" else "REAL"
                connection.execute(f"ALTER TABLE samples ADD COLUMN {field} {kind}")
            connection.execute("""
                INSERT INTO sessions VALUES
                    (9, '2026-09-23T00:01:00+00:00', '2026-09-23T00:02:00+00:00',
                     'standing', 'Second recorded session', 'old-port')
            """)
            connection.execute("DELETE FROM samples")
            for i in range(300):
                sample = expected_legacy_slope_sample(i % 150, i * 100)
                sample["accel_avg_slope"] = (i - 150) / 100.0 if i >= 4 else None
                sample["gyro_avg_slope"] = (150 - i) / 10.0 if i >= 4 else None
                sample["accel_msd_slope"] = i / 8.0 if i >= 5 else None
                sample["gyro_msd_slope"] = -i / 7.0 if i >= 5 else None
                if i == 0:
                    sample["accel_msd"] = sample["gyro_msd"] = None
                fields = ("id", "session_id", "timestamp_utc", *sample, *RETIRED_RATE_FIELDS)
                values = (
                    41 + i * 2, 7 if i < 150 else 9,
                    f"2026-09-23T00:{i // 60:02d}:{i % 60:02d}+00:00",
                    *sample.values(), *(None for _ in RETIRED_RATE_FIELDS),
                )
                connection.execute(
                    f"INSERT INTO samples ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
                    values,
                )
            connection.execute("DROP VIEW readings")
            connection.execute("""
                CREATE VIEW readings AS
                    SELECT p.id AS record_id, p.session_id, p.timestamp_utc,
                           s.activity, s.notes, p.sample_number,
                           p.accel_x_mps2, p.accel_y_mps2, p.accel_z_mps2,
                           p.accel_avg_mps2, p.gyro_x_dps, p.gyro_y_dps,
                           p.gyro_z_dps, p.gyro_avg_dps,
                           p.board_time_ms, p.accel_msd, p.accel_avg_rate,
                           p.accel_msd_rate, p.gyro_msd, p.gyro_avg_rate, p.gyro_msd_rate,
                           p.slope_window_samples, p.accel_avg_slope, p.accel_msd_slope,
                           p.gyro_avg_slope, p.gyro_msd_slope
                    FROM samples p JOIN sessions s ON p.session_id = s.id
            """)
            connection.commit()
            self.previous_rows = [
                dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")
            ]
            self.previous_sessions = [dict(row) for row in connection.execute("SELECT * FROM sessions ORDER BY id")]
        finally:
            connection.close()

    def write_previous_csv(self, rows):
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=PREVIOUS_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_cleanup_keeps_300_rows_recomputes_magnitude_slopes_and_preserves_msd_then_appends(self):
        self.write_previous_csv(self.previous_rows)
        expected_rows = [
            expected_migrated_row(row, 0.0 if i % 150 >= 4 else None, 0.0 if i % 150 >= 4 else None)
            for i, row in enumerate(self.previous_rows)
        ]
        database = self.open_database()
        migrated = [dict(row) for row in database.connection.execute("SELECT * FROM readings ORDER BY record_id")]
        self.assertEqual(len(migrated), 300)
        self.assertEqual(migrated, expected_rows)
        self.assertEqual(
            [dict(row) for row in database.connection.execute("SELECT * FROM sessions ORDER BY id")],
            self.previous_sessions,
        )
        recorder = CSVRecorder(self.csv_path, database)
        recorder.close()
        database.close()

        reopened = self.open_database()
        columns = {row["name"] for row in reopened.connection.execute("PRAGMA table_info(samples)")}
        self.assertTrue(columns.isdisjoint(RETIRED_RATE_FIELDS + LEGACY_AVERAGE_FIELDS))
        self.assertEqual(
            [dict(row) for row in reopened.connection.execute("SELECT * FROM readings ORDER BY record_id")],
            expected_rows,
        )
        session = reopened.start_session("normal", "After cleanup", "new-port")
        appended = reopened.append_sample(session, expected_slope_sample(0, 0))
        self.assertGreater(session, 9)
        self.assertGreater(appended["record_id"], self.previous_rows[-1]["record_id"])
        self.assertTrue(set(appended).isdisjoint(RETIRED_RATE_FIELDS))
        recorder = CSVRecorder(self.csv_path, reopened)
        recorder.close()
        header, csv_rows = self.read_csv()
        self.assertEqual(header, CLEAN_CSV_FIELDS)
        self.assertEqual(len(header), 22)
        self.assertEqual(csv_rows, [
            {key: "" if row[key] is None else str(row[key]) for key in CLEAN_CSV_FIELDS}
            for row in expected_rows + [appended]
        ])

    def test_populated_retired_rate_in_last_row_prevents_schema_cleanup(self):
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("UPDATE samples SET gyro_msd_rate = 0 WHERE id = ?", (self.previous_rows[-1]["record_id"],))
        before = self.schema_and_data_snapshot()
        with self.assertRaises(ValueError):
            RecordingDatabase(self.db_path)
        self.assertEqual(self.schema_and_data_snapshot(), before)

    def test_previous_csv_with_nonempty_rate_or_edited_slope_is_never_rewritten(self):
        database = self.open_database()
        for field, value in (("gyro_avg_rate", 0.0), ("accel_avg_slope", 999.0)):
            with self.subTest(field=field):
                edited = dict(self.previous_rows[-1])
                edited[field] = value
                self.write_previous_csv([*self.previous_rows[:-1], edited])
                before = self.csv_path.read_bytes()
                with self.assertRaises(ValueError):
                    CSVRecorder(self.csv_path, database)
                self.assertEqual(self.csv_path.read_bytes(), before)

    def test_failed_cleanup_restores_dropped_rate_columns_original_view_and_all_rows(self):
        class FailViewCreationConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.lstrip().upper().startswith("CREATE VIEW READINGS AS"):
                    raise sqlite3.OperationalError("injected view creation failure")
                return super().execute(sql, parameters)

        before = self.schema_and_data_snapshot()
        connect = sqlite3.connect
        connection = connect(self.db_path, factory=FailViewCreationConnection)

        def connect_with_injected_failure(path, *args, **kwargs):
            if Path(path).resolve() == self.db_path.resolve():
                return connection
            return connect(path, *args, **kwargs)

        try:
            with mock.patch("record_activity.sqlite3.connect", side_effect=connect_with_injected_failure):
                with self.assertRaisesRegex(sqlite3.OperationalError, "injected view creation failure"):
                    RecordingDatabase(self.db_path)
        finally:
            connection.close()
        self.assertEqual(self.schema_and_data_snapshot(), before)


class MagnitudeMigrationTests(unittest.TestCase):
    """Migrate the real 22-column Avg schema, with recoverable original values."""

    open_database = LegacyRecordingMigrationTests.open_database
    read_csv = LegacyRecordingMigrationTests.read_csv
    schema_and_data_snapshot = CumulativeRecordingMigrationTests.schema_and_data_snapshot

    def setUp(self):
        PreviousSlopeSchemaMigrationTests.setUp(self)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("DROP VIEW readings")
            for field in RETIRED_RATE_FIELDS:
                connection.execute(f"ALTER TABLE samples DROP COLUMN {field}")
            fields = ", ".join(f"p.{field}" for field in AVERAGE_CSV_FIELDS[6:])
            connection.execute(f"""CREATE VIEW readings AS
                SELECT p.id AS record_id, p.session_id, p.timestamp_utc,
                       s.activity, s.notes, p.sample_number, {fields}
                FROM samples p JOIN sessions s ON p.session_id = s.id
            """)
            connection.commit()
            self.original_rows = [dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")]
        finally:
            connection.close()

    def write_average_csv(self, rows):
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=AVERAGE_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def metadata_and_backup(self, database):
        metadata = dict(database.connection.execute("SELECT key, value FROM recording_metadata"))
        relative = Path(metadata["legacy_average_backup"])
        self.assertFalse(relative.is_absolute())
        self.assertEqual(relative.parts[0], "backups")
        backup = self.directory / relative
        self.assertTrue(backup.is_file())
        return metadata, backup

    def replace_original_samples(self, specifications):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("DELETE FROM samples")
            for index, specification in enumerate(specifications):
                sample = expected_legacy_slope_sample(index, index * 100)
                sample.update(specification)
                session = sample.pop("session_id", 7)
                # The previous mean and its slope are intentionally unrelated to
                # the vector length. Copying either is observably incorrect.
                sample["accel_avg_mps2"] = sum(sample[f"accel_{axis}_mps2"] for axis in "xyz") / 3
                sample["gyro_avg_dps"] = sum(sample[f"gyro_{axis}_dps"] for axis in "xyz") / 3
                sample["accel_avg_slope"] = 1234.0
                sample["gyro_avg_slope"] = -5678.0
                sample["accel_msd"] = index + 0.125
                sample["gyro_msd"] = index + 25.0
                sample["accel_msd_slope"] = index - 5.75
                sample["gyro_msd_slope"] = 8.25 - index
                fields = ("id", "session_id", "timestamp_utc", *sample)
                values = (100 + index * 7, session, f"2026-09-23T00:00:{index:02d}+00:00", *sample.values())
                connection.execute(
                    f"INSERT INTO samples ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
                    values,
                )
            connection.commit()
            self.original_rows = [dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")]
        finally:
            connection.close()

    def test_backup_preserves_every_original_value_and_provenance_survives_reopen(self):
        original_snapshot = self.schema_and_data_snapshot()
        database = self.open_database()
        metadata, backup = self.metadata_and_backup(database)
        self.assertEqual(int(metadata["magnitude_recomputed_through_record_id"]), 639)
        connection = sqlite3.connect(backup)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(
                [dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")],
                self.original_rows,
            )
            self.assertEqual(
                [dict(row) for row in connection.execute("SELECT * FROM sessions ORDER BY id")],
                original_snapshot["sessions"],
            )
        finally:
            connection.close()
        for table in ("samples", "readings"):
            columns = {row["name"] for row in database.connection.execute(f"PRAGMA table_info({table})")}
            self.assertTrue(columns.isdisjoint(LEGACY_AVERAGE_FIELDS + RETIRED_RATE_FIELDS))
        backups_before = {path.name for path in backup.parent.iterdir()}
        database.close()
        reopened = self.open_database()
        self.assertEqual(dict(reopened.connection.execute("SELECT key, value FROM recording_metadata")), metadata)
        self.assertEqual({path.name for path in backup.parent.iterdir()}, backups_before)

    def test_slopes_use_actual_rounded_vectors_times_windows_and_reset_boundaries(self):
        # Each tuple starts a new continuous history: gap, rebooted counter,
        # another session, changed window, duplicate tick, or backward tick.
        groups = [
            (7, 50, [0xfffffff0 + t for t in (0, 80, 210, 310, 520, 630, 770)], 5, 10, 2, 40, -3),
            (7, 60, [800, 900, 1000, 1100, 1200], 5, 20, -4, 30, 7),
            (7, 0, [1300, 1400, 1500, 1600, 1700], 5, 5, 6, 25, -8),
            (9, 5, [1800, 1900, 2000, 2100, 2200], 5, 11, -2, 18, 3),
            (9, 10, [2300, 2400, 2500], 3, 12, 4, 15, -5),
            (9, 13, [2500, 2600, 2700], 3, 9, -6, 10, 7),
            (9, 16, [2000, 2100, 2200], 3, 8, 8, 20, -9),
        ]
        specifications = []
        expectations = []
        for session, counter, times, window, accel_start, accel_rate, gyro_start, gyro_rate in groups:
            for index, tick in enumerate(times):
                elapsed = (tick - times[0]) / 1000.0
                accel = accel_start + accel_rate * elapsed
                gyro = gyro_start + gyro_rate * elapsed
                specifications.append({
                    "session_id": session, "sample_number": counter + index,
                    "board_time_ms": tick & 0xffffffff, "slope_window_samples": window,
                    "accel_x_mps2": round(0.6 * accel, 3), "accel_y_mps2": round(0.8 * accel, 3), "accel_z_mps2": 0.0,
                    "gyro_x_dps": round(-0.6 * gyro, 3), "gyro_y_dps": round(-0.8 * gyro, 3), "gyro_z_dps": 0.0,
                })
                expectations.append((accel_rate, gyro_rate) if index >= window - 1 else (None, None))
        self.replace_original_samples(specifications)
        database = self.open_database()
        rows = [dict(row) for row in database.connection.execute("SELECT * FROM readings ORDER BY record_id")]
        for old, new, expected_slopes in zip(self.original_rows, rows, expectations):
            with self.subTest(record_id=new["record_id"]):
                expected = expected_migrated_row(old, *expected_slopes)
                for field in CLEAN_CSV_FIELDS:
                    if field in ("accel_magnitude_slope", "gyro_magnitude_slope") and expected[field] is not None:
                        self.assertAlmostEqual(new[field], expected[field], places=10)
                    else:
                        self.assertEqual(new[field], expected[field])

    def test_recomputed_slopes_evict_old_points_and_do_not_use_average_slopes(self):
        magnitudes = [10, 10, 10, 10, 20, 30, 40, 50, 60, 70]
        self.replace_original_samples([
            {"accel_x_mps2": value, "accel_y_mps2": 0.0, "accel_z_mps2": 0.0,
             "gyro_x_dps": 100 - value, "gyro_y_dps": 0.0, "gyro_z_dps": 0.0}
            for value in magnitudes
        ])
        database = self.open_database()
        rows = list(database.connection.execute("SELECT * FROM readings ORDER BY record_id"))
        for index, row in enumerate(rows):
            if index < 4:
                self.assertIsNone(row["accel_magnitude_slope"])
                self.assertIsNone(row["gyro_magnitude_slope"])
                continue
            # Pairwise least-squares identity is independent of the centered
            # covariance calculation used by the implementation.
            points = list(enumerate(magnitudes[index - 4:index + 1]))
            numerator = sum((x - u) * (y - v) for x, y in points for u, v in points if x > u)
            denominator = sum((x - u) ** 2 for x, _ in points for u, _ in points if x > u)
            expected = 10 * numerator / denominator  # index spacing is 0.1 s
            self.assertAlmostEqual(row["accel_magnitude_slope"], expected)
            self.assertAlmostEqual(row["gyro_magnitude_slope"], -expected)
        self.assertEqual(rows[-1]["accel_magnitude_slope"], 100.0)

    def test_restart_after_database_migration_validates_old_csv_then_keeps_new_live_values(self):
        self.write_average_csv(self.original_rows[:-1])
        original_csv = self.csv_path.read_bytes()
        database = self.open_database()
        metadata, _ = self.metadata_and_backup(database)
        database.close()  # Simulates stopping after DB commit but before CSV replacement.
        reopened = self.open_database()
        session = reopened.start_session("normal", "Magnitude firmware", "new-port")
        current = expected_slope_sample(0, 0)
        live_row = reopened.append_sample(session, current)
        recorder = CSVRecorder(self.csv_path, reopened)
        recorder.close()
        header, rows = self.read_csv()
        self.assertEqual(header, CLEAN_CSV_FIELDS)
        self.assertEqual(len(rows), 301)
        self.assertEqual([int(row["record_id"]) for row in rows], [row["record_id"] for row in self.original_rows] + [live_row["record_id"]])
        self.assertEqual(rows[-1], {key: "" if value is None else str(value) for key, value in live_row.items()})
        backups = list((self.directory / "backups").glob("*.csv"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original_csv)
        self.assertEqual(dict(reopened.connection.execute("SELECT key, value FROM recording_metadata")), metadata)
        reopened.close()
        final = self.open_database()
        persisted_live = dict(final.connection.execute("SELECT * FROM readings WHERE record_id = ?", (live_row["record_id"],)).fetchone())
        self.assertEqual(persisted_live, live_row)
        before = self.csv_path.read_bytes()
        CSVRecorder(self.csv_path, final).close()
        self.assertEqual(self.csv_path.read_bytes(), before)

    def test_edited_average_or_slope_is_rejected_before_csv_backup_or_rewrite(self):
        database = self.open_database()
        for field in LEGACY_AVERAGE_FIELDS:
            with self.subTest(field=field):
                last = {**self.original_rows[-1], field: 12345.0}
                self.write_average_csv([*self.original_rows[:-1], last])
                before = self.csv_path.read_bytes()
                with self.assertRaises(ValueError):
                    CSVRecorder(self.csv_path, database)
                self.assertEqual(self.csv_path.read_bytes(), before)
                self.assertEqual(list((self.directory / "backups").glob("*.csv")), [])

    def test_missing_original_database_backup_prevents_old_csv_rewrite(self):
        self.write_average_csv(self.original_rows)
        before = self.csv_path.read_bytes()
        database = self.open_database()
        _, backup = self.metadata_and_backup(database)
        backup.rename(backup.with_suffix(".unavailable"))
        with self.assertRaisesRegex(ValueError, "backup"):
            CSVRecorder(self.csv_path, database)
        self.assertEqual(self.csv_path.read_bytes(), before)

    def test_failed_database_backup_leaves_original_schema_and_values_intact(self):
        class FailBackupConnection(sqlite3.Connection):
            def backup(self, target, **kwargs):
                raise sqlite3.OperationalError("injected backup failure")

        before = self.schema_and_data_snapshot()
        connect = sqlite3.connect
        connection = connect(self.db_path, factory=FailBackupConnection)

        def connect_with_failed_backup(path, *args, **kwargs):
            if Path(path).resolve() == self.db_path.resolve():
                return connection
            return connect(path, *args, **kwargs)

        try:
            with mock.patch("record_activity.sqlite3.connect", side_effect=connect_with_failed_backup):
                with self.assertRaisesRegex(sqlite3.OperationalError, "injected backup failure"):
                    RecordingDatabase(self.db_path)
        finally:
            connection.close()
        self.assertEqual(self.schema_and_data_snapshot(), before)
        self.assertEqual(list((self.directory / "backups").glob("*")), [])


class RecordingDurationTests(unittest.TestCase):
    def test_cli_defaults_to_thirty_seconds_and_accepts_positive_override(self):
        for options, expected_duration in (([], 30), (["--duration", "7"], 7)):
            with self.subTest(options=options):
                with mock.patch.object(sys, "argv", ["record_activity.py", *options]):
                    with mock.patch.object(record_activity, "record", return_value=0) as record:
                        self.assertEqual(record_activity.main(), 0)
                record.assert_called_once()
                self.assertEqual(record.call_args.args[0].duration, expected_duration)

    def test_cli_rejects_nonpositive_duration(self):
        for value in ("0", "-1"):
            with self.subTest(value=value):
                with mock.patch.object(sys, "argv", ["record_activity.py", "--duration", value]):
                    with mock.patch.object(sys, "stderr", io.StringIO()):
                        with self.assertRaises(SystemExit) as error:
                            record_activity.main()
                self.assertEqual(error.exception.code, 2)

    def run_timed_recording(self, events, duration=1, samples=None, expire_on_second_accel=False):
        """Advance only fake serial-arrival/processing time; persist to real temp files."""
        clock = SimpleNamespace(now=100.0)
        parser_feed = SampleParser.feed
        accel_lines = 0

        class FakeReader:
            def __init__(self):
                self.events = list(events)
                self.timeouts = []
                self.closed = False

            def read(self, timeout=1.0):
                self.timeouts.append(timeout)
                if len(self.timeouts) > 10:
                    raise AssertionError("Recording did not stop at its deadline")
                if self.events:
                    elapsed, chunk = self.events.pop(0)
                    clock.now += elapsed
                    return chunk
                clock.now += timeout
                return None

            def close(self):
                self.closed = True

        def feed_with_processing_delay(parser, line):
            nonlocal accel_lines
            result = parser_feed(parser, line)
            if line.strip() == ACCEL_SLOPE:
                accel_lines += 1
                if expire_on_second_accel and accel_lines == 2:
                    clock.now = 100.0 + duration
            return result

        reader = FakeReader()
        console = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "duration.sqlite3"
            csv_path = Path(directory) / "duration.csv"
            args = SimpleNamespace(
                db=db_path, csv=csv_path, port="mock-serial", activity="normal",
                notes="Duration regression", duration=duration, samples=samples,
            )
            with mock.patch.object(record_activity, "prepare_console"), \
                 mock.patch.object(record_activity, "serial_port", return_value="mock-serial"), \
                 mock.patch.object(record_activity, "SerialReader", return_value=reader), \
                 mock.patch.object(record_activity.time, "monotonic", side_effect=lambda: clock.now), \
                 mock.patch.object(SampleParser, "feed", new=feed_with_processing_delay), \
                 mock.patch.object(sys, "stdout", console):
                self.assertEqual(record_activity.record(args), 0)

            self.assertTrue(reader.closed)
            connection = sqlite3.connect(db_path)
            try:
                session = connection.execute("SELECT ended_at_utc FROM sessions").fetchone()
                self.assertIsNotNone(session[0])
                numbers = [row[0] for row in connection.execute("SELECT sample_number FROM samples ORDER BY id")]
            finally:
                connection.close()
            with csv_path.open(newline="", encoding="utf-8") as handle:
                csv_numbers = [int(row["sample_number"]) for row in csv.DictReader(handle)]
            self.assertEqual(csv_numbers, numbers)
        self.assertIn(f"Saved {len(numbers)} sample(s) this run.", console.getvalue())
        return numbers, reader.timeouts, clock.now, console.getvalue()

    @staticmethod
    def frame(number):
        return "\n".join([
            f"Sample {number} TimeMs={number * 100} SlopeWindow=5",
            ACCEL_SLOPE, GYRO_SLOPE, "",
        ]).encode("ascii")

    def test_reads_use_remaining_time_and_keep_completed_samples_when_deadline_expires(self):
        partial = ("Sample 1 TimeMs=100 SlopeWindow=5\n" + ACCEL_SLOPE + "\n").encode("ascii")
        numbers, timeouts, stopped, output = self.run_timed_recording([
            (0.8, self.frame(0)), (0.15, partial),
        ])
        self.assertEqual(numbers, [0])
        self.assertEqual(len(timeouts), 3)
        for actual, expected in zip(timeouts, (1.0, 0.2, 0.05)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(stopped, 101.0)
        self.assertIn("1-second recording complete. Keeping all saved readings.", output)

    def test_frame_completing_after_deadline_is_not_saved(self):
        partial = ("Sample 1 TimeMs=100 SlopeWindow=5\n" + ACCEL_SLOPE + "\n").encode("ascii")
        numbers, timeouts, _, _ = self.run_timed_recording([
            (0.25, self.frame(0) + partial),
            (0.76, (GYRO_SLOPE + "\n").encode("ascii")),
        ])
        self.assertEqual(numbers, [0])
        self.assertEqual(len(timeouts), 2)
        self.assertAlmostEqual(timeouts[1], 0.75)

    def test_deadline_is_checked_while_processing_already_buffered_lines(self):
        numbers, timeouts, stopped, _ = self.run_timed_recording(
            [(0.1, self.frame(0) + self.frame(1))], expire_on_second_accel=True,
        )
        self.assertEqual(numbers, [0])
        self.assertEqual(len(timeouts), 1)
        self.assertAlmostEqual(stopped, 101.0)

    def test_sample_limit_still_stops_before_duration_with_no_extra_saved_sample(self):
        numbers, timeouts, stopped, _ = self.run_timed_recording(
            [(0.1, self.frame(0) + self.frame(1))], duration=30, samples=1,
        )
        self.assertEqual(numbers, [0])
        self.assertEqual(len(timeouts), 1)
        self.assertAlmostEqual(stopped, 100.1)

    def test_detector_messages_are_visible_without_becoming_saved_samples(self):
        messages = [
            b"DETECTOR TimeMs=10 State=SENSOR_FAULT Alarm=0 Sensors=FAULT Event=SENSOR_FAULT invalid data\n",
            b"DETECTOR TimeMs=8000 State=FALL_LATCHED Alarm=1 Sensors=OK Event=POSSIBLE_FALL reset board\n",
        ]
        numbers, _, _, output = self.run_timed_recording([
            (0.1, messages[0] + self.frame(10) + messages[1] + self.frame(11)),
        ])
        self.assertEqual(numbers, [10, 11])
        for message in messages:
            self.assertIn(message.decode("ascii").strip(), output)


@unittest.skipUnless(os.name == "posix", "Console repair requires POSIX terminals")
class ConsoleInterruptTests(unittest.TestCase):
    def test_prepare_console_skips_redirected_files_and_streams_without_fileno(self):
        import termios

        with tempfile.TemporaryFile(mode="w+") as redirected:
            for stream in (redirected, io.StringIO()):
                with self.subTest(stream_type=type(stream).__name__):
                    with mock.patch.multiple(record_activity.sys, stdin=stream, stdout=stream, stderr=stream):
                        with mock.patch.object(termios, "tcgetattr") as get_attributes:
                            with mock.patch.object(termios, "tcsetattr") as set_attributes:
                                record_activity.prepare_console()
                        get_attributes.assert_not_called()
                        set_attributes.assert_not_called()

    def test_prepare_console_repairs_only_interrupt_and_output_newline_settings(self):
        import pty
        import termios

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        settings = termios.tcgetattr(slave)
        settings[0] |= termios.IXOFF
        settings[1] &= ~(termios.OPOST | termios.ONLCR)
        settings[3] &= ~termios.ISIG
        settings[6][termios.VINTR] = bytes([os.fpathconf(slave, "PC_VDISABLE") & 0xFF])
        settings[6][termios.VERASE] = b"\x08"
        termios.tcsetattr(slave, termios.TCSANOW, settings)
        before = termios.tcgetattr(slave)

        with os.fdopen(os.dup(slave), "r") as stdin, \
             os.fdopen(os.dup(slave), "w") as stdout, \
             os.fdopen(os.dup(slave), "w") as stderr:
            with mock.patch.multiple(record_activity.sys, stdin=stdin, stdout=stdout, stderr=stderr):
                record_activity.prepare_console()

        after = termios.tcgetattr(slave)
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], before[1] | termios.OPOST | termios.ONLCR)
        self.assertEqual(after[2], before[2])
        self.assertEqual(after[3], before[3] | termios.ISIG)
        self.assertEqual(after[4:6], before[4:6])
        expected_characters = list(before[6])
        expected_characters[termios.VINTR] = b"\x03"
        self.assertEqual(after[6], expected_characters)

    def test_actual_cli_ctrl_c_byte_stops_cleanly_from_initially_broken_console(self):
        import fcntl
        import pty
        import termios

        console_master, console_slave = pty.openpty()
        serial_master, serial_slave = pty.openpty()
        descriptors = {console_master, console_slave, serial_master, serial_slave}
        process = None
        output = bytearray()

        def make_controlling_terminal():
            # Keep terminal-generated signals in this child's own session/group.
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        def read_until(marker, timeout=8):
            deadline = time.monotonic() + timeout
            while marker not in output:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail(f"Timed out waiting for {marker!r}; output: {output.decode(errors='replace')}")
                ready, _, _ = select.select([console_master], [], [], min(remaining, 0.2))
                if not ready:
                    continue
                try:
                    chunk = os.read(console_master, 4096)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    self.fail(f"Console closed before {marker!r}; output: {output.decode(errors='replace')}")
                output.extend(chunk)

        try:
            broken = termios.tcgetattr(console_slave)
            broken[1] &= ~(termios.OPOST | termios.ONLCR)
            broken[3] &= ~termios.ISIG
            broken[6][termios.VINTR] = bytes([os.fpathconf(console_slave, "PC_VDISABLE") & 0xFF])
            termios.tcsetattr(console_slave, termios.TCSANOW, broken)
            serial_path = os.ttyname(serial_slave)
            os.close(serial_slave)
            descriptors.remove(serial_slave)

            with tempfile.TemporaryDirectory() as directory:
                db_path = Path(directory) / "interrupt.sqlite3"
                csv_path = Path(directory) / "interrupt.csv"
                command = [
                    sys.executable, str(Path(record_activity.__file__).resolve()),
                    "--port", serial_path, "--db", str(db_path), "--csv", str(csv_path),
                    "--activity", "normal", "--notes", "Console interrupt regression",
                ]
                process = subprocess.Popen(
                    command, stdin=console_slave, stdout=console_slave, stderr=console_slave,
                    preexec_fn=make_controlling_terminal,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                os.close(console_slave)
                descriptors.remove(console_slave)
                read_until(b"Waiting for complete Sample")

                frame = "\r\n".join([
                    "Sample 6 TimeMs=600 SlopeWindow=5", ACCEL_SLOPE, GYRO_SLOPE, "",
                ]).encode("ascii")
                os.write(serial_master, frame)
                read_until(b"Saved 1: sample 6")

                # This is the keyboard byte, not a direct subprocess SIGINT.
                os.write(console_master, b"\x03")
                read_until(b"Session 1 ended.")
                self.assertEqual(process.wait(timeout=3), 0, output.decode(errors="replace"))
                self.assertIn(b"Stopping recording.", output)
                self.assertIn(b"\r\n", output)

                connection = sqlite3.connect(db_path)
                try:
                    ended, count = connection.execute(
                        "SELECT ended_at_utc, (SELECT COUNT(*) FROM samples) FROM sessions"
                    ).fetchone()
                    self.assertIsNotNone(ended)
                    self.assertEqual(count, 1)
                finally:
                    connection.close()
                with csv_path.open(newline="", encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["sample_number"], "6")
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for descriptor in descriptors:
                os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
