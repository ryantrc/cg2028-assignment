"""Tests for actual firmware verdict capture, attribution and crash recovery.

Run: python3 -m unittest discover -s tools -p 'test_detector_verdicts.py'
"""

import csv
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import detector_verdicts
from detector_verdicts import CSV_FIELDS, VerdictRecorder, parse_diagnostic


START = "2026-09-25T01:00:00.000+00:00"
END = "2026-09-25T01:00:30.000+00:00"


def diagnostic(event="STATUS", state=None, *, tick=1000, alarm=None, sensors="OK", message=""):
    if state is None:
        state = detector_verdicts.EVENT_STATES.get(event, "NORMAL")
        if event == "SENSOR_FAULT":
            state = "SENSOR_FAULT"
    if alarm is None:
        alarm = int(state == "FALL_LATCHED")
    return f"DETECTOR TimeMs={tick} State={state} Alarm={alarm} Sensors={sensors} Event={event} {message}"


def sample(number=123):
    return {
        "sample_number": number,
        "accel_x_mps2": -1.234, "accel_y_mps2": 2.345, "accel_z_mps2": 9.567,
        "accel_magnitude_mps2": 9.927,
        "gyro_x_dps": -1.222, "gyro_y_dps": 2.333, "gyro_z_dps": 3.444,
        "gyro_magnitude_dps": 4.337,
        "board_time_ms": 12345, "accel_msd": 0.001234567, "gyro_msd": 3.141592,
        "slope_window_samples": 5, "accel_magnitude_slope": -0.9876543,
        "accel_msd_slope": 0.01234567, "gyro_magnitude_slope": -1.234567,
        "gyro_msd_slope": -0.1234567,
    }


class VerdictRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = self.root / "prototype_verdicts.sqlite3"
        self.csv = self.root / "prototype_verdicts.csv"
        self.source = self.root / "prototype_readings.sqlite3"
        self.recorder = VerdictRecorder(self.db, self.csv)
        self.addCleanup(lambda: self.recorder.close())

    def start(self, *, session=1, activity="normal", historical=False, started=START):
        return self.recorder.start_run(self.source, session, activity, "test, with notes", started,
                                       historical=historical)

    def append(self, run, event="STATUS", state=None, **kwargs):
        self.assertTrue(self.recorder.append_line(run, diagnostic(event, state, **kwargs), START))

    def row(self, run):
        return self.recorder.get_run(run)

    def csv_rows(self):
        with self.csv.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    def test_live_empty_run_is_not_inferred_from_activity(self):
        run = self.start(activity="fall")
        self.assertEqual(self.row(run)["observed_verdict"], "NO_DETECTOR_DATA")
        self.assertEqual(self.row(run)["recording_status"], "OPEN")
        self.assertIsNone(self.row(run)["sample_count"])
        self.recorder.finish_run(run, END, "DURATION", 300)
        row = self.row(run)
        self.assertEqual(row["observed_verdict"], "NO_DETECTOR_DATA")
        self.assertEqual((row["recording_status"], row["sample_count"]), ("FINISHED", 300))

    def test_labels_never_change_diagnostic_verdict(self):
        values = []
        for index, label in enumerate(("fall", "near-fall", "normal", "anything"), 1):
            run = self.start(session=index, activity=label)
            self.append(run)
            values.append(self.row(run)["observed_verdict"])
        self.assertEqual(values, ["NO_EVENT_OBSERVED"] * 4)

    def test_explicit_fall_is_durable_before_finish(self):
        run = self.start()
        message = "Sustained stillness; reset board to clear alarm."
        self.append(run, "POSSIBLE_FALL", message=message)
        with closing(sqlite3.connect(self.db)) as other, other:
            self.assertEqual(other.execute("SELECT observed_verdict FROM runs").fetchone()[0], "POSSIBLE_FALL")
            self.assertEqual(other.execute("SELECT message FROM events").fetchone()[0], message)
        row = self.row(run)
        self.assertEqual((row["possible_fall_count"], row["verdict_source"]), (1, "FIRMWARE_EVENT"))
        self.assertEqual(self.csv_rows()[0]["recording_status"], "OPEN")

    def test_both_explicit_decisions_are_mixed(self):
        run = self.start()
        self.append(run, "NEAR_FALL")
        self.append(run, "POSSIBLE_FALL")
        self.assertEqual(self.row(run)["observed_verdict"], "MIXED_DECISIONS")
        self.assertEqual(self.row(run)["event_count"], 2)

    def test_first_seen_latched_status_is_not_new_fall(self):
        run = self.start()
        self.append(run, state="FALL_LATCHED")
        self.append(run, state="FALL_LATCHED")
        row = self.row(run)
        self.assertEqual(row["observed_verdict"], "PREEXISTING_LATCHED_ALARM")
        self.assertEqual(row["verdict_source"], "FIRMWARE_STATUS")
        self.assertEqual((row["possible_fall_count"], row["status_count"]), (0, 2))

    def test_status_after_missed_event_retains_evidence_without_inventing_event(self):
        run = self.start()
        self.append(run, "READY")
        self.append(run, "SPIKE")
        self.append(run, state="FALL_LATCHED")
        row = self.row(run)
        self.assertEqual(row["observed_verdict"], "LATCHED_ALARM_OBSERVED")
        self.assertEqual(row["possible_fall_count"], 0)

    def test_near_fall_plus_status_only_alarm_is_not_plain_near_fall(self):
        run = self.start()
        self.append(run, "NEAR_FALL")
        self.append(run, state="FALL_LATCHED")
        row = self.row(run)
        self.assertEqual(row["observed_verdict"], "NEAR_FALL_WITH_LATCHED_ALARM")
        self.assertEqual(row["verdict_source"], "FIRMWARE_EVENT_AND_STATUS")
        self.assertEqual(row["possible_fall_count"], 0)

    def test_observation_and_uncertain_states_are_not_fall_decisions(self):
        run = self.start()
        self.append(run, "SPIKE")
        self.assertEqual(self.row(run)["observed_verdict"], "OBSERVATION_INCOMPLETE")
        self.append(run, "UNCERTAIN")
        self.assertEqual(self.row(run)["observed_verdict"], "UNCERTAIN")
        self.recorder.finish_run(run, END, "INTERRUPTED", 80)
        self.assertEqual(self.row(run)["observed_verdict"], "UNCERTAIN")

    def test_status_after_missing_resolution_does_not_infer_near_fall(self):
        run = self.start()
        self.append(run, "SPIKE")
        self.append(run)
        self.assertEqual(self.row(run)["observed_verdict"], "OBSERVATION_INCOMPLETE")
        self.assertEqual(self.row(run)["near_fall_count"], 0)

    def test_warmup_only_is_incomplete(self):
        run = self.start()
        self.append(run, state="WARMUP")
        self.assertEqual(self.row(run)["observed_verdict"], "WARMUP_INCOMPLETE")

    def test_fault_recovery_retains_fault_history(self):
        run = self.start()
        self.append(run, "SENSOR_FAULT", sensors="FAULT")
        self.append(run, state="SENSOR_FAULT", sensors="FAULT")
        self.assertEqual(self.row(run)["observed_verdict"], "SENSOR_FAULT")
        self.append(run, "RESTARTED")
        self.append(run, "READY")
        row = self.row(run)
        self.assertEqual(row["observed_verdict"], "NO_EVENT_OBSERVED")
        self.assertEqual(row["sensor_health"], "FAULT_RECOVERED")
        self.assertEqual((row["sensor_fault_count"], row["fault_diagnostic_count"]), (1, 2))

    def test_fault_after_fall_never_erases_decision(self):
        run = self.start()
        self.append(run, "POSSIBLE_FALL")
        self.append(run, "SENSOR_FAULT", "FALL_LATCHED", sensors="FAULT")
        self.append(run, state="FALL_LATCHED", sensors="FAULT")
        row = self.row(run)
        self.assertEqual(row["observed_verdict"], "POSSIBLE_FALL")
        self.assertEqual((row["last_alarm"], row["sensor_health"]), (1, "FAULT_PRESENT"))

    def test_historical_metadata_is_never_a_replayed_verdict(self):
        run = self.start(activity="fall", historical=True)
        self.recorder.finish_run(run, END, "HISTORICAL_IMPORT", 299)
        row = self.row(run)
        self.assertEqual(row["observed_verdict"], "NOT_RECORDED")
        self.assertEqual(row["verdict_source"], "NOT_RECORDED")
        with self.assertRaises(ValueError):
            self.append(run, "POSSIBLE_FALL")
        self.assertEqual(self.row(run)["diagnostic_count"], 0)

    def test_invalid_diagnostics_preserved_but_do_not_create_verdict(self):
        run = self.start()
        lines = (
            "DETECTOR broken",
            diagnostic("POSSIBLE_FALL", alarm=0),
            diagnostic(tick=0x100000000),
            diagnostic("UNRECOGNIZED"),
            diagnostic("POSSIBLE_FALL", sensors="FAULT"),
            diagnostic("SENSOR_FAULT", "NORMAL", sensors="FAULT"),
        )
        for line in lines:
            self.assertFalse(self.recorder.append_line(run, line, START))
        row = self.row(run)
        self.assertEqual(row["observed_verdict"], "NO_DETECTOR_DATA")
        self.assertEqual(row["diagnostic_coverage"], "INVALID_ONLY")
        self.assertEqual(row["invalid_diagnostic_count"], len(lines))
        saved = self.recorder.connection.execute("SELECT raw_line,parse_error FROM events ORDER BY event_id").fetchall()
        self.assertEqual([event["raw_line"] for event in saved], list(lines))
        self.assertTrue(all(event["parse_error"] for event in saved))
        self.append(run)
        self.assertEqual(self.row(run)["diagnostic_coverage"], "OBSERVED_WITH_INVALID_LINES")

    def test_unrelated_sensor_lines_do_not_make_events(self):
        run = self.start()
        self.assertFalse(self.recorder.append_line(run, "Sample 123 TimeMs=12345 SlopeWindow=5", START))
        self.assertEqual(self.row(run)["diagnostic_count"], 0)

    def test_finish_rejects_additional_events_and_is_idempotent(self):
        run = self.start()
        self.recorder.finish_run(run, END, "DURATION", 300)
        self.recorder.finish_run(run, END, "DURATION", 300)
        with self.assertRaises(ValueError):
            self.append(run)
        with self.assertRaises(ValueError):
            self.recorder.finish_run(run, END, "DISCONNECTED", 299)

    def test_import_idempotency_and_reused_session_numbers(self):
        run = self.start(historical=True)
        self.assertEqual(run, self.start(historical=True))
        with self.assertRaises(ValueError):
            self.start(activity="different", historical=True)
        other = self.start(historical=True, started=END)
        self.assertNotEqual(run, other)
        self.assertEqual(len(self.csv_rows()), 2)

    def test_csv_has_one_summary_per_run_not_per_event(self):
        run = self.start()
        for tick in range(30):
            self.append(run, tick=tick)
        self.assertEqual(len(self.csv_rows()), 1)
        self.assertEqual(self.row(run)["diagnostic_count"], 30)
        self.assertEqual(list(self.csv_rows()[0])[:6], list(CSV_FIELDS)[:6])
        self.assertEqual(self.csv_rows()[0]["notes"], "test, with notes")

    def test_csv_recovered_after_post_commit_export_failure(self):
        run = self.start()
        with mock.patch.object(self.recorder, "export_csv", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.append(run, "POSSIBLE_FALL")
        self.assertEqual(self.csv_rows()[0]["observed_verdict"], "NO_DETECTOR_DATA")
        # Simulate process termination: close SQLite without exporting.
        self.recorder.connection.close()
        self.recorder.connection = None
        self.recorder = VerdictRecorder(self.db, self.csv)
        self.assertEqual(self.csv_rows()[0]["observed_verdict"], "POSSIBLE_FALL")
        self.assertEqual(self.row(run)["recording_status"], "OPEN")

    def test_atomic_csv_failure_leaves_previous_export_intact(self):
        run = self.start()
        before = self.csv.read_bytes()
        with mock.patch.object(detector_verdicts.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.append(run)
        self.assertEqual(self.csv.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".prototype_verdicts.csv.*.tmp")))
        self.assertEqual(self.row(run)["diagnostic_count"], 1)

    def test_reopen_missing_csv_restores_summary_and_preserves_events(self):
        run = self.start()
        self.append(run, "NEAR_FALL")
        self.recorder.close()
        self.csv.unlink()
        self.recorder = VerdictRecorder(self.db, self.csv)
        first = self.csv.read_bytes()
        self.recorder.close()
        self.recorder = VerdictRecorder(self.db, self.csv)
        self.assertEqual(self.csv.read_bytes(), first)
        self.assertEqual(self.row(run)["near_fall_count"], 1)

    def test_refuses_measurement_database_and_foreign_csv(self):
        source = self.root / "source.sqlite3"
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.execute("CREATE TABLE sessions(id INTEGER)")
        before = source.read_bytes()
        with self.assertRaises(ValueError):
            VerdictRecorder(source, self.root / "new.csv")
        self.assertEqual(source.read_bytes(), before)
        foreign = self.root / "readings.csv"
        foreign.write_text("session_id,accel_x_mps2\n1,9.8\n")
        with self.assertRaises(ValueError):
            VerdictRecorder(self.root / "new.sqlite3", foreign)
        self.assertEqual(foreign.read_text(), "session_id,accel_x_mps2\n1,9.8\n")

    def test_database_csv_and_source_collisions_rejected(self):
        with self.assertRaises(ValueError):
            VerdictRecorder(self.db, self.db)
        alias = self.root / "alias.sqlite3"
        alias.hardlink_to(self.db)
        with self.assertRaises(ValueError):
            VerdictRecorder(self.db, alias)
        for source in (self.db, self.csv, alias):
            with self.assertRaises(ValueError):
                self.recorder.start_run(source, 1, "normal", "", START)

    def test_foreign_verdict_csv_cannot_be_replaced(self):
        run = self.start()
        self.append(run)
        self.recorder.close()
        with self.assertRaises(ValueError):
            VerdictRecorder(self.root / "unrelated.sqlite3", self.csv)
        # The rejected constructor did not remove any existing summary.
        self.assertEqual(self.csv_rows()[0]["diagnostic_count"], "1")

    def test_tick_wrap_does_not_sort_away_event_order(self):
        run = self.start()
        self.append(run, "SPIKE", tick=0xFFFFFFF0)
        self.append(run, "UNCERTAIN", tick=8000)
        row = self.row(run)
        self.assertEqual((row["first_board_time_ms"], row["last_board_time_ms"]), (0xFFFFFFF0, 8000))
        self.assertEqual(row["observed_verdict"], "UNCERTAIN")

    def test_invalid_metadata_cannot_change_run(self):
        with self.assertRaises(ValueError):
            self.start(session=0)
        with self.assertRaises(ValueError):
            self.start(started="2026-09-25T12:00:00")
        run = self.start()
        with self.assertRaises(ValueError):
            self.recorder.finish_run(run, END, "DURATION", -1)
        self.assertEqual(self.row(run)["recording_status"], "OPEN")

    def make_v1_fixture(self):
        """Build the preceding released schema with real run/event rows."""
        rows = self.csv_rows()
        self.recorder.close()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("DROP VIEW test_readings")
            connection.execute("DROP TABLE test_samples")
            connection.execute("ALTER TABLE runs DROP COLUMN expected_verdict")
            connection.execute("ALTER TABLE runs DROP COLUMN port")
            connection.execute("UPDATE verdict_metadata SET value=? WHERE key='schema_id'",
                               (detector_verdicts.V1_SCHEMA_ID,))
        with self.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=detector_verdicts.V1_CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return self.csv.read_bytes()

    def test_structured_test_keeps_expectation_separate_from_observed_decision(self):
        run = self.recorder.start_test("fall-test-3", "near-fall", "careful", START, "/dev/tty.example")
        self.append(run, "POSSIBLE_FALL")
        row = self.row(run)
        self.assertEqual(row["activity"], "fall-test-3-near-fall")
        self.assertEqual(row["expected_verdict"], "near-fall")
        self.assertEqual(row["observed_verdict"], "POSSIBLE_FALL")
        self.assertEqual(row["source_database"], str(self.db.resolve()))
        self.assertEqual(row["port"], "/dev/tty.example")

    def test_test_ids_continue_legacy_sessions_and_default_name(self):
        self.start(session=36, historical=True)
        self.start(session=37, historical=True)
        run = self.recorder.start_test(None, "normal", "", START)
        self.assertEqual(self.row(run)["session_id"], 38)
        self.assertEqual(self.row(run)["activity"], "test-38-normal")
        self.assertEqual(self.row(run)["sample_count"], 0)
        next_run = self.recorder.start_test("again", "fall", "", START)
        self.assertEqual(self.row(next_run)["session_id"], 39)

    def test_test_requires_explicit_supported_expectation(self):
        for expected in (None, "", "FALL", "uncertain"):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.recorder.start_test("test", expected, "", START)
        with self.assertRaises(ValueError):
            self.recorder.start_test("  ", "fall", "", START)
        with self.assertRaises(ValueError):
            self.recorder.start_run(self.source, 1, "fall", "", START,
                                    historical=True, expected_verdict="fall")

    def test_test_telemetry_and_metadata_share_database(self):
        run = self.recorder.start_test("walk", "normal", "notes", START, "port")
        values = sample()
        sample_id = self.recorder.append_sample(run, values, START)
        self.assertEqual(self.recorder.count_samples(run), 1)
        self.assertEqual(self.row(run)["sample_count"], 1)
        self.assertEqual(self.csv_rows()[0]["sample_count"], "1")
        saved = dict(self.recorder.connection.execute("SELECT * FROM test_readings").fetchone())
        self.assertEqual(saved["record_id"], sample_id)
        self.assertIsNone(saved["legacy_record_id"])
        self.assertEqual(saved["expected_verdict"], "normal")
        self.assertEqual(saved["activity"], "walk-normal")
        self.assertEqual(saved["timestamp_utc"], START)
        for field, value in values.items():
            self.assertEqual(saved[field], value)
        self.recorder.finish_run(run, END, "DURATION", 1)
        with self.assertRaises(ValueError):
            self.recorder.append_sample(run, values, END)

    def test_test_finish_count_must_match_durable_samples(self):
        run = self.recorder.start_test(None, "normal", "", START)
        self.recorder.append_sample(run, sample(), START)
        with self.assertRaises(ValueError):
            self.recorder.finish_run(run, END, "DURATION", 0)
        self.assertEqual(self.row(run)["recording_status"], "OPEN")

    def test_missing_startup_metrics_remain_null(self):
        run = self.recorder.start_test(None, "normal", "", START)
        values = sample()
        for key in ("accel_msd", "gyro_msd", "accel_magnitude_slope", "accel_msd_slope",
                    "gyro_magnitude_slope", "gyro_msd_slope"):
            values[key] = None
        self.recorder.append_sample(run, values, START)
        saved = dict(self.recorder.connection.execute("SELECT * FROM test_samples").fetchone())
        for key, value in values.items():
            self.assertEqual(saved[key], value)

    def test_bad_telemetry_never_changes_count(self):
        run = self.recorder.start_test(None, "normal", "", START)
        for change in ({"accel_x_mps2": float("nan")}, {"gyro_msd": -1},
                       {"sample_number": 0x100000000}, {"slope_window_samples": 1},
                       {"accel_magnitude_mps2": -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.recorder.append_sample(run, dict(sample(), **change), START)
        self.assertEqual(self.recorder.count_samples(run), 0)
        self.assertEqual(self.row(run)["sample_count"], 0)

    def test_retired_rates_are_rejected_instead_of_silently_dropped(self):
        run = self.recorder.start_test(None, "normal", "", START)
        for field in detector_verdicts.RETIRED_RATE_FIELDS:
            for value in (None, 1.25):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "Retired AvgRate/MSDRate"):
                    self.recorder.append_sample(run, dict(sample(), **{field: value}), START)
        self.assertEqual(self.recorder.count_samples(run), 0)
        self.assertEqual(self.row(run)["sample_count"], 0)

    def test_legacy_samples_import_exactly_idempotently_without_changing_verdict(self):
        run = self.start(session=36, activity="fall", historical=True)
        self.recorder.finish_run(run, END, "HISTORICAL_IMPORT", 299)
        before = self.row(run)
        values = dict(sample(), record_id=12000, session_id=36, activity="fall",
                      notes="test, with notes", timestamp_utc=START)
        inserted = self.recorder.import_sample(run, values)
        self.assertEqual(self.recorder.import_sample(run, values), inserted)
        self.assertEqual(self.recorder.count_samples(run), 1)
        self.assertEqual(self.row(run), before)
        saved = dict(self.recorder.connection.execute("SELECT * FROM test_readings").fetchone())
        self.assertEqual(saved["legacy_record_id"], 12000)
        for field in values.keys() - {"record_id"}:
            self.assertEqual(saved[field], values[field])
        self.assertIsNone(saved["expected_verdict"])
        for changed in ({"gyro_x_dps": 99}, {"timestamp_utc": END},
                        {"session_id": 37}, {"activity": "normal"}, {"notes": "other"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.recorder.import_sample(run, dict(values, **changed))
        self.assertEqual(self.recorder.count_samples(run), 1)

    def test_legacy_port_is_enriched_once_without_fabricated_expectation(self):
        run = self.start(session=36, historical=True)
        same = self.recorder.start_run(self.source, 36, "normal", "test, with notes", START,
                                      historical=True, port="/dev/old")
        self.assertEqual(same, run)
        self.assertEqual(self.row(run)["port"], "/dev/old")
        self.assertIsNone(self.row(run)["expected_verdict"])
        with self.assertRaises(ValueError):
            self.recorder.start_run(self.source, 36, "normal", "test, with notes", START,
                                    historical=True, port="/dev/different")

    def test_v1_upgrade_preserves_all_old_values_and_creates_backups(self):
        run = self.start(session=36, activity="fall", historical=True)
        self.recorder.finish_run(run, END, "HISTORICAL_IMPORT", 299)
        other = self.start(session=37)
        self.append(other, "SPIKE", message="Original event retained")
        before = {row["run_id"]: dict(row) for row in self.recorder.connection.execute("SELECT * FROM runs")}
        events = [dict(row) for row in self.recorder.connection.execute("SELECT * FROM events")]
        csv_bytes = self.make_v1_fixture()
        self.recorder = VerdictRecorder(self.db, self.csv)
        self.assertEqual(len(self.recorder.migration_backups), 2)
        for row in self.recorder.connection.execute("SELECT * FROM runs"):
            self.assertEqual(dict(row), before[row["run_id"]])
        self.assertEqual([dict(row) for row in self.recorder.connection.execute("SELECT * FROM events")], events)
        db_backup, csv_backup = self.recorder.migration_backups
        self.assertEqual(db_backup.parent, (self.db.parent / "backups").resolve())
        self.assertEqual(csv_backup.parent, (self.csv.parent / "backups").resolve())
        self.assertEqual(csv_backup.read_bytes(), csv_bytes)
        with closing(sqlite3.connect(db_backup)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT value FROM verdict_metadata WHERE key='schema_id'").fetchone()[0],
                             detector_verdicts.V1_SCHEMA_ID)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertTrue(all(row["expected_verdict"] == "" for row in self.csv_rows()))
        self.assertEqual(self.recorder.count_samples(run), 0)

    def test_v1_migration_rejects_foreign_csv_before_changing_database(self):
        self.start()
        self.make_v1_fixture()
        text = self.csv.read_text().replace("test, with notes", "different notes")
        self.csv.write_text(text)
        before = self.db.read_bytes()
        with self.assertRaises(ValueError):
            VerdictRecorder(self.db, self.csv)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse(list(self.root.rglob("*.before-v2.*.bak")))

    def test_v1_schema_failure_rolls_back_all_schema_changes(self):
        self.start()
        self.make_v1_fixture()
        with mock.patch.object(detector_verdicts, "TEST_SCHEMA_STATEMENTS", ("THIS IS NOT SQL",)):
            with self.assertRaises(sqlite3.DatabaseError):
                VerdictRecorder(self.db, self.csv)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            names = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            self.assertNotIn("expected_verdict", names)
            self.assertNotIn("port", names)
            self.assertEqual(connection.execute("SELECT value FROM verdict_metadata WHERE key='schema_id'").fetchone()[0],
                             detector_verdicts.V1_SCHEMA_ID)
        self.recorder = VerdictRecorder(self.db, self.csv)
        self.assertEqual(self.csv_rows()[0]["expected_verdict"], "")

    def test_v2_database_and_old_csv_recover_after_export_interruption(self):
        self.start()
        old_csv = self.make_v1_fixture()
        with mock.patch.object(detector_verdicts.os, "replace", side_effect=OSError("interrupted export")):
            with self.assertRaises(OSError):
                VerdictRecorder(self.db, self.csv)
        self.assertEqual(self.csv.read_bytes(), old_csv)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(connection.execute("SELECT value FROM verdict_metadata WHERE key='schema_id'").fetchone()[0],
                             detector_verdicts.SCHEMA_ID)
        self.recorder = VerdictRecorder(self.db, self.csv)
        self.assertEqual(list(self.csv_rows()[0]), list(CSV_FIELDS))
        self.assertEqual(self.csv_rows()[0]["expected_verdict"], "")

    def test_live_sample_survives_csv_export_failure(self):
        run = self.recorder.start_test(None, "normal", "", START)
        with mock.patch.object(self.recorder, "export_csv", side_effect=OSError("disk busy")):
            with self.assertRaises(OSError):
                self.recorder.append_sample(run, sample(), START)
        self.recorder.connection.close()
        self.recorder.connection = None
        self.recorder = VerdictRecorder(self.db, self.csv)
        self.assertEqual(self.recorder.count_samples(run), 1)
        self.assertEqual(self.row(run)["sample_count"], 1)
        self.assertEqual(self.csv_rows()[0]["sample_count"], "1")


class DiagnosticParserTests(unittest.TestCase):
    def test_message_is_preserved_and_ticks_are_unsigned(self):
        line = diagnostic("SPIKE", tick=0xFFFFFFFF, message="Sharp movement; observing for 8 seconds.")
        parsed = parse_diagnostic(line + "\r\n")
        self.assertEqual(parsed["board_time_ms"], 0xFFFFFFFF)
        self.assertEqual(parsed["message"], "Sharp movement; observing for 8 seconds.")

    def test_injected_second_line_is_not_valid_diagnostic(self):
        with self.assertRaises(ValueError):
            parse_diagnostic(diagnostic() + "\n" + diagnostic("POSSIBLE_FALL"))


if __name__ == "__main__":
    unittest.main()
