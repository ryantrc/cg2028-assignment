"""Temporary-data tests for moving measurements into existing verdict runs."""

import csv
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import detector_verdicts
from detector_verdicts import VerdictRecorder
import migrate_test_storage as migration
from record_activity import CSV_FIELDS, RecordingDatabase
from split_recordings import _connect, _csv_bytes, _readings, _snapshot


START = "2026-09-25T01:00:00.000+00:00"
END = "2026-09-25T01:00:30.000+00:00"


class MigrateTestStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.paths = tuple(self.root / name for name in (
            "prototype_readings.sqlite3", "prototype_readings.csv",
            "prototype_verdicts.sqlite3", "prototype_verdicts.csv",
        ))
        db = RecordingDatabase(self.paths[0])
        db.close()
        with closing(sqlite3.connect(self.paths[0])) as connection, connection:
            for sid, label in ((36, "prototype1-fall-test"), (37, "prototype1-fall-test-2"), (38, "free-named-fall")):
                connection.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (
                    sid, START, END, label, f'Original {sid}, "notes"\nretained', "/dev/original-port",
                ))
            for record_id, sid, number in ((100, 36, 0), (101, 36, 1), (105, 37, 0), (106, 37, 1), (110, 38, 0)):
                values = {field: record_id / 13 for field in CSV_FIELDS[6:]}
                values.update(accel_x_mps2=-9.8, gyro_y_dps=-12.0, board_time_ms=100 + 100 * number,
                              slope_window_samples=5, accel_magnitude_slope=None if not number else -3.5)
                columns = ("id", "session_id", "timestamp_utc", "sample_number", *values)
                connection.execute(f"INSERT INTO samples ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                                   (record_id, sid, START, number, *values.values()))
            connection.executemany("INSERT INTO recording_metadata VALUES (?,?)", (
                ("recording_dataset", "prototype"), ("custom", "original metadata"),
            ))
            connection.execute("CREATE TABLE extra_metadata (name TEXT PRIMARY KEY, data BLOB)")
            connection.execute("INSERT INTO extra_metadata VALUES ('bytes', ?)", (b"\x00\xff",))
            connection.execute("UPDATE sqlite_sequence SET seq=999 WHERE name='samples'")
        self.original, self.original_rows = self.source_snapshot()
        self.paths[1].write_bytes(_csv_bytes(self.original_rows))
        self.original_csv = self.paths[1].read_bytes()
        self.make_v1_verdict_database()
        self.old_verdict_schema = self.verdict_snapshot()
        self.old_verdict_csv = self.paths[3].read_bytes()

    def make_v1_verdict_database(self):
        # This fixture is the actual v1 structure, not a new-schema API roundtrip.
        counts = ",".join(f"{field} INTEGER NOT NULL DEFAULT 0" for field in detector_verdicts.COUNT_FIELDS)
        details = ",".join(f"{field} {'INTEGER' if field.endswith('_ms') or field == 'last_alarm' else 'TEXT'}"
                           for field in detector_verdicts.DETAIL_FIELDS)
        with closing(sqlite3.connect(self.paths[2])) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.executescript(f"""
                CREATE TABLE verdict_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT, source_database TEXT NOT NULL,
                    session_id INTEGER NOT NULL, activity TEXT NOT NULL, notes TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL, ended_at_utc TEXT,
                    recording_status TEXT NOT NULL DEFAULT 'OPEN', sample_count INTEGER,
                    stop_reason TEXT, historical INTEGER NOT NULL CHECK(historical IN (0,1)),
                    observed_verdict TEXT NOT NULL, verdict_source TEXT NOT NULL,
                    {counts},{details},diagnostic_coverage TEXT NOT NULL,sensor_health TEXT NOT NULL,
                    UNIQUE(source_database,session_id,started_at_utc)
                );
                CREATE TABLE events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL REFERENCES runs(run_id),
                    timestamp_utc TEXT NOT NULL,raw_line TEXT NOT NULL,parse_valid INTEGER NOT NULL CHECK(parse_valid IN (0,1)),
                    parse_error TEXT,board_time_ms INTEGER,state TEXT,alarm INTEGER,sensors TEXT,event TEXT,message TEXT
                );
                CREATE INDEX events_run_id ON events(run_id,event_id);
                INSERT INTO verdict_metadata VALUES ('schema_id','cg2028_firmware_verdicts_v1');
                INSERT INTO verdict_metadata VALUES ('original-extra','retain me');
            """)
            for sid in (36, 37):
                session = next(dict(zip(self.original["tables"]["sessions"][0], row))
                               for row in self.original["tables"]["sessions"][1] if row[0] == sid)
                connection.execute("""INSERT INTO runs(source_database,session_id,activity,notes,started_at_utc,ended_at_utc,
                    recording_status,sample_count,stop_reason,historical,observed_verdict,verdict_source,diagnostic_coverage,sensor_health)
                    VALUES (?,?,?,?,?,?,'FINISHED',2,'HISTORICAL_IMPORT',1,'NOT_RECORDED','NOT_RECORDED','NOT_RECORDED','NOT_RECORDED')""",
                    (str(self.paths[0]), sid, session["activity"], session["notes"], START, END))
            rows = [dict(row) for row in connection.execute("SELECT * FROM runs ORDER BY run_id")]
            # Old CSV order is immaterial; all actual v1 columns are present.
            with self.paths[3].open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=detector_verdicts.V1_CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)

    def source_snapshot(self):
        connection = _connect(self.paths[0], readonly=True)
        try:
            return _snapshot(connection), _readings(connection)
        finally:
            connection.close()

    def verdict_snapshot(self):
        connection = _connect(self.paths[2], readonly=True)
        try:
            return _snapshot(connection)
        finally:
            connection.close()

    def assert_source_original(self):
        self.assertEqual(self.source_snapshot(), (self.original, self.original_rows))
        self.assertEqual(self.paths[1].read_bytes(), self.original_csv)

    def test_move_preserves_values_metadata_and_unmatched_free_session(self):
        result = migration.migrate_test_storage(*self.paths)
        self.assertEqual((result["status"], result["sessions"], result["samples"]), ("complete", 2, 4))
        snapshot, rows = self.source_snapshot()
        self.assertEqual([row["session_id"] for row in rows], [38])
        expected = migration._expected_source(self.original, {36, 37})
        self.assertEqual(snapshot, expected)
        with closing(_connect(self.paths[2], readonly=True)) as connection:
            saved = [dict(row) for row in connection.execute("SELECT * FROM test_readings ORDER BY legacy_record_id")]
            self.assertEqual([row["legacy_record_id"] for row in saved], [100, 101, 105, 106])
            for actual, original in zip(saved, self.original_rows[:4]):
                self.assertEqual({field: actual[field] for field in CSV_FIELDS if field != "record_id"},
                                 {field: original[field] for field in CSV_FIELDS if field != "record_id"})
                self.assertIsNone(actual["expected_verdict"])
                self.assertEqual(actual["port"], "/dev/original-port")
            self.assertEqual([tuple(row) for row in connection.execute("SELECT observed_verdict,historical FROM runs")],
                             [("NOT_RECORDED", 1), ("NOT_RECORDED", 1)])
            self.assertEqual(connection.execute("SELECT value FROM verdict_metadata WHERE key='original-extra'").fetchone()[0], "retain me")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        with closing(sqlite3.connect(self.paths[0])) as connection, connection:
            next_id = connection.execute("INSERT INTO sessions(started_at_utc,activity,notes,port) VALUES (?,'free','n','p')", (START,)).lastrowid
            self.assertEqual(next_id, 39)

    def test_backups_precede_upgrade_and_preserve_both_original_pairs(self):
        result = migration.migrate_test_storage(*self.paths)
        backup = Path(result["backup_path"])
        for name, expected in (("measurements", self.original), ("verdicts", self.old_verdict_schema)):
            with closing(_connect(backup / (name + ".sqlite3"), readonly=True)) as connection:
                self.assertEqual(_snapshot(connection), expected)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual((backup / "measurements.csv").read_bytes(), self.original_csv)
        self.assertEqual((backup / "verdicts.csv").read_bytes(), self.old_verdict_csv)
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["matches"][0]["session"]["port"], "/dev/original-port")

    def test_preview_and_completed_rerun_make_no_changes(self):
        preview = migration.migrate_test_storage(*self.paths, apply=False)
        self.assertEqual((preview["status"], preview["samples"]), ("dry_run", 4))
        self.assert_source_original()
        self.assertEqual(self.verdict_snapshot(), self.old_verdict_schema)
        self.assertFalse((self.root / "backups").exists())
        migration.migrate_test_storage(*self.paths)
        before = [path.read_bytes() for path in self.paths]
        result = migration.migrate_test_storage(*self.paths)
        self.assertEqual(result["status"], "no_changes")
        self.assertEqual([path.read_bytes() for path in self.paths], before)

    def test_mismatched_measurement_csv_refuses_before_any_upgrade(self):
        self.paths[1].write_text(self.original_csv.decode().replace("-9.8", "-8.8", 1))
        with self.assertRaisesRegex(ValueError, "CSV differs"):
            migration.migrate_test_storage(*self.paths)
        self.assertEqual(self.verdict_snapshot(), self.old_verdict_schema)
        self.assertFalse((self.root / "backups").exists())

    def test_mismatched_verdict_csv_refuses_before_any_upgrade(self):
        self.paths[3].write_text(self.old_verdict_csv.decode().replace("NOT_RECORDED", "POSSIBLE_FALL", 1))
        with self.assertRaisesRegex(ValueError, "Verdict CSV differs"):
            migration.migrate_test_storage(*self.paths)
        self.assert_source_original()
        self.assertEqual(self.verdict_snapshot(), self.old_verdict_schema)

    def test_interrupted_copy_never_deletes_source_and_retry_is_idempotent(self):
        original_import = VerdictRecorder.import_sample
        calls = 0
        def interrupted(recorder, run_id, row):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("injected copy interruption")
            return original_import(recorder, run_id, row)
        with mock.patch.object(VerdictRecorder, "import_sample", interrupted):
            with self.assertRaisesRegex(OSError, "injected"):
                migration.migrate_test_storage(*self.paths)
        self.assert_source_original()
        with closing(_connect(self.paths[2], readonly=True)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM test_samples").fetchone()[0], 2)
        migration.migrate_test_storage(*self.paths)
        with closing(_connect(self.paths[2], readonly=True)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM test_samples").fetchone()[0], 4)

    def test_post_commit_csv_failure_recovers_only_verified_exact_old_csv(self):
        with mock.patch.object(migration, "_replace_csv", side_effect=OSError("injected CSV replace")):
            with self.assertRaisesRegex(OSError, "injected"):
                migration.migrate_test_storage(*self.paths)
        self.assertEqual([row["session_id"] for row in self.source_snapshot()[1]], [38])
        self.assertEqual(self.paths[1].read_bytes(), self.original_csv)
        result = migration.migrate_test_storage(*self.paths)
        self.assertTrue(result["recovered_csv"])
        self.assertEqual(result["status"], "no_changes")
        self.assertEqual(self.paths[1].read_bytes(), _csv_bytes(self.source_snapshot()[1]))

    def test_wrong_provenance_does_not_select_a_test_named_session(self):
        with closing(sqlite3.connect(self.paths[2])) as connection, connection:
            connection.execute("UPDATE runs SET source_database=?", (str(self.root / "another.sqlite3"),))
        # Restore an exact matching CSV for this intentionally different source.
        with closing(_connect(self.paths[2], readonly=True)) as connection:
            rows = migration._rows(connection, "runs", "run_id")
        with self.paths[3].open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=detector_verdicts.V1_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        result = migration.migrate_test_storage(*self.paths)
        self.assertEqual(result["status"], "no_changes")
        self.assert_source_original()

    def test_existing_writer_blocks_before_changes(self):
        with closing(sqlite3.connect(self.paths[0])) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                migration.migrate_test_storage(*self.paths)
        self.assert_source_original()
        self.assertEqual(self.verdict_snapshot(), self.old_verdict_schema)

    def test_all_legacy_tests_move_and_future_free_starts_at_38(self):
        with closing(sqlite3.connect(self.paths[0])) as connection, connection:
            connection.execute("DELETE FROM samples WHERE session_id=38")
            connection.execute("DELETE FROM sessions WHERE id=38")
            connection.execute("UPDATE sqlite_sequence SET seq=37 WHERE name='sessions'")
        self.paths[1].write_bytes(_csv_bytes(self.source_snapshot()[1]))
        migration.migrate_test_storage(*self.paths)
        self.assertEqual(self.source_snapshot()[1], [])
        self.assertEqual(self.paths[1].read_bytes(), _csv_bytes([]))
        with closing(sqlite3.connect(self.paths[0])) as connection, connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            sid = connection.execute("INSERT INTO sessions(started_at_utc,activity,notes,port) VALUES (?,'free','n','p')", (START,)).lastrowid
            self.assertEqual(sid, 38)
            self.assertEqual(connection.execute("SELECT seq FROM sqlite_sequence WHERE name='samples'").fetchone()[0], 999)

    def test_inconsistent_matched_metadata_refuses_before_backup(self):
        with closing(sqlite3.connect(self.paths[0])) as connection, connection:
            connection.execute("UPDATE sessions SET ended_at_utc=? WHERE id=36", (START,))
        with self.assertRaisesRegex(ValueError, "mismatched verdict ended_at_utc"):
            migration.migrate_test_storage(*self.paths)
        self.assertEqual(self.verdict_snapshot(), self.old_verdict_schema)
        self.assertFalse((self.root / "backups").exists())

    def test_imported_value_corruption_is_detected_before_deletion(self):
        original_import = VerdictRecorder.import_sample
        def corrupt_import(recorder, run_id, row):
            sample_id = original_import(recorder, run_id, row)
            with recorder.connection:
                recorder.connection.execute("UPDATE test_samples SET gyro_y_dps=123 WHERE id=?", (sample_id,))
            return sample_id
        with mock.patch.object(VerdictRecorder, "import_sample", corrupt_import):
            with self.assertRaisesRegex(ValueError, "values or legacy record IDs differ"):
                migration.migrate_test_storage(*self.paths)
        self.assert_source_original()

    def test_recovery_refuses_edited_stale_csv(self):
        with mock.patch.object(migration, "_replace_csv", side_effect=OSError("injected CSV replace")):
            with self.assertRaises(OSError):
                migration.migrate_test_storage(*self.paths)
        self.paths[1].write_text(self.original_csv.decode().replace("-9.8", "-1.8", 1))
        edited = self.paths[1].read_bytes()
        with self.assertRaisesRegex(ValueError, "no verified interrupted migration"):
            migration.migrate_test_storage(*self.paths)
        self.assertEqual(self.paths[1].read_bytes(), edited)
        with closing(_connect(self.paths[2], readonly=True)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM test_samples").fetchone()[0], 4)

    def test_verdict_writer_blocks_before_upgrade_or_backup(self):
        with closing(sqlite3.connect(self.paths[2])) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                migration.migrate_test_storage(*self.paths)
        self.assert_source_original()
        self.assertEqual(self.verdict_snapshot(), self.old_verdict_schema)
        self.assertFalse((self.root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
