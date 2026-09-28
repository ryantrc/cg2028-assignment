"""Temporary-data tests for the one-time SQLite/CSV partition migration.

Run: python3 -m unittest discover -s tools -p 'test_split_recordings.py'
"""

import csv
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import split_recordings as split
from record_activity import CSV_FIELDS


class SplitRecordingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.paths = tuple(self.root / name for name in (
            "activity.sqlite3", "activity.csv", "calibration.sqlite3",
            "calibration.csv", "prototype.sqlite3", "prototype.csv",
        ))
        source, csv_path = self.paths[:2]
        connection = sqlite3.connect(source)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at_utc TEXT NOT NULL, ended_at_utc TEXT,
                    activity TEXT NOT NULL, notes TEXT NOT NULL, port TEXT NOT NULL
                );
                CREATE TABLE recording_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE experiment_config (name TEXT PRIMARY KEY, value BLOB);
            """)
            metric_columns = []
            for field in CSV_FIELDS[6:]:
                kind = "INTEGER" if field in ("board_time_ms", "slope_window_samples") else "REAL"
                metric_columns.append(f"{field} {kind}")
            connection.execute("""CREATE TABLE samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                timestamp_utc TEXT NOT NULL, sample_number INTEGER NOT NULL,
            """ + ", ".join(metric_columns) + ")")
            connection.execute("CREATE INDEX samples_session ON samples(session_id)")
            connection.execute("CREATE INDEX samples_time ON samples(board_time_ms)")
            connection.execute("""CREATE VIEW readings AS
                SELECT p.id AS record_id,p.session_id,p.timestamp_utc,
                       s.activity,s.notes,p.sample_number,""" +
                ",".join(f"p.{field}" for field in CSV_FIELDS[6:]) +
                " FROM samples p JOIN sessions s ON s.id=p.session_id")
            for sid, activity in ((1, "normal"), (3, "walking-quickly"),
                                  (36, "prototype1-fall-test"), (37, "Prototype1-fall-test-2")):
                connection.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (
                    sid, f"2026-09-24T00:{sid:02d}:00+00:00", f"2026-09-24T00:{sid:02d}:30+00:00",
                    activity, f'Trial {sid}: "quoted", note\nSecond line', "original-port",
                ))
            for row_id, sid, number in ((2, 1, 0), (8, 1, 1), (11, 3, 0), (100, 36, 0), (200, 37, 0)):
                sample = {field: float(row_id) / 17 for field in CSV_FIELDS[6:]}
                sample.update(accel_x_mps2=-1.25, accel_y_mps2=2.5, accel_z_mps2=9.8,
                              gyro_x_dps=-30.0, gyro_y_dps=15.0, gyro_z_dps=0.0,
                              board_time_ms=number * 101, slope_window_samples=5,
                              accel_magnitude_slope=None if row_id == 2 else -2.5)
                columns = ("id", "session_id", "timestamp_utc", "sample_number", *sample)
                connection.execute(f"INSERT INTO samples ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                                   (row_id, sid, "2026-09-24T00:00:00.123+00:00", number, *sample.values()))
            connection.executemany("INSERT INTO recording_metadata VALUES (?,?)", (
                ("legacy_average_backup", "backups/old-preserved.sqlite3"),
                ("magnitude_recomputed_through_record_id", "11"),
                ("recording_dataset", "previous-role"),
            ))
            connection.execute("INSERT INTO experiment_config VALUES (?,?)", ("binary", b"\x00\xff\x12"))
            # These sequence values deliberately exceed the surviving maximum IDs.
            connection.execute("UPDATE sqlite_sequence SET seq=123 WHERE name='sessions'")
            connection.execute("UPDATE sqlite_sequence SET seq=9001 WHERE name='samples'")
            connection.commit()
            self.original = split._snapshot(connection)
            self.original_rows = split._readings(connection)
        finally:
            connection.close()
        csv_path.write_bytes(split._csv_bytes(self.original_rows))
        self.original_csv = csv_path.read_bytes()

    def snapshot(self, path):
        connection = split._connect(path, readonly=True)
        try:
            split._check_database(connection)
            return split._snapshot(connection), split._readings(connection)
        finally:
            connection.close()

    def assert_sources_unchanged(self):
        snapshot, rows = self.snapshot(self.paths[0])
        self.assertEqual(snapshot, self.original)
        self.assertEqual(rows, self.original_rows)
        self.assertEqual(self.paths[1].read_bytes(), self.original_csv)

    def assert_no_destinations(self):
        self.assertFalse(any(path.exists() for path in self.paths[2:]))
        self.assertFalse(list(self.root.glob(".recording-split-*")))

    def test_split_preserves_ids_sequences_full_schema_values_labels_and_metadata(self):
        result = split.split_recordings(*self.paths)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["calibration"]["session_ids"], [1, 3])
        self.assertEqual(result["prototype"]["session_ids"], [36, 37])
        self.assertEqual(result["calibration"]["samples"], 3)
        self.assertEqual(result["prototype"]["samples"], 2)
        self.assertFalse(any(path.exists() for path in self.paths[:2]))
        for name, db_path, csv_path, ids in (
            ("calibration", self.paths[2], self.paths[3], {1, 3}),
            ("prototype", self.paths[4], self.paths[5], {36, 37}),
        ):
            snapshot, readings = self.snapshot(db_path)
            self.assertEqual(snapshot["schema"], self.original["schema"])
            expected = [row for row in self.original_rows if row["session_id"] in ids]
            self.assertEqual(readings, expected)
            split._validate_csv(csv_path.read_bytes(), expected)
            for table in ("sqlite_sequence", "experiment_config"):
                self.assertEqual(snapshot["tables"][table], self.original["tables"][table])
            metadata = dict(snapshot["tables"]["recording_metadata"][1])
            original_metadata = dict(self.original["tables"]["recording_metadata"][1])
            self.assertEqual(metadata, {**original_metadata, "recording_dataset": name})
            self.assertFalse(Path(str(db_path) + "-wal").exists())
        # New insertions must not reuse either existing or previously removed IDs.
        with closing(sqlite3.connect(self.paths[4])) as connection, connection:
            new_id = connection.execute("INSERT INTO sessions(started_at_utc,activity,notes,port) VALUES ('now','prototype','n','p')").lastrowid
        self.assertEqual(new_id, 124)

    def test_both_original_backups_and_archives_are_complete_and_reopenable(self):
        result = split.split_recordings(*self.paths)
        backup = Path(result["backup_path"])
        self.assertEqual(backup.parent, self.root / "backups")
        snapshot, readings = self.snapshot(Path(result["original_db_backup"]))
        self.assertEqual(snapshot, self.original)
        self.assertEqual(readings, self.original_rows)
        self.assertEqual(Path(result["original_csv_backup"]).read_bytes(), self.original_csv)
        self.assertEqual(self.snapshot(backup / "retired_working_files" / self.paths[0].name), (self.original, self.original_rows))
        self.assertEqual((backup / "retired_working_files" / self.paths[1].name).read_bytes(), self.original_csv)
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["status"], "complete")

    def test_dry_run_has_no_file_changes_or_backup_side_effects(self):
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()}
        result = split.plan_split(*self.paths)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["prototype"]["session_ids"], [36, 37])
        self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()})

    def test_explicit_session_ids_override_activity_prefix(self):
        result = split.split_recordings(*self.paths, prototype_session_ids=[3])
        self.assertEqual(result["prototype"]["session_ids"], [3])
        self.assertEqual(result["calibration"]["session_ids"], [1, 36, 37])

    def test_mismatching_csv_aborts_before_backup_or_destinations(self):
        edited = [dict(row) for row in self.original_rows]
        edited[-1]["gyro_x_dps"] = 999.0
        self.paths[1].write_bytes(split._csv_bytes(edited))
        before = self.paths[1].read_bytes()
        with self.assertRaisesRegex(ValueError, "differs"):
            split.split_recordings(*self.paths)
        self.assertEqual(self.snapshot(self.paths[0]), (self.original, self.original_rows))
        self.assertEqual(self.paths[1].read_bytes(), before)
        self.assert_no_destinations()
        self.assertFalse((self.root / "backups").exists())

    def test_existing_destination_aborts_without_overwrite(self):
        self.paths[4].write_bytes(b"existing dataset must not change")
        with self.assertRaises(FileExistsError):
            split.split_recordings(*self.paths)
        self.assertEqual(self.paths[4].read_bytes(), b"existing dataset must not change")
        self.assert_sources_unchanged()
        self.assertFalse((self.root / "backups").exists())

    def test_repeated_apply_refuses_to_repeat_or_overwrite_finished_split(self):
        result = split.split_recordings(*self.paths)
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths[2:]}
        with self.assertRaises(FileExistsError):
            split.split_recordings(*self.paths)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths[2:]})
        self.assertEqual(len(list((self.root / "backups").glob("recording-split-*"))), 1)
        self.assertEqual(json.loads((Path(result["backup_path"]) / "manifest.json").read_text())["status"], "complete")

    def test_empty_or_unknown_selection_aborts_without_mutation(self):
        for selected in ([], [999], [1, 3, 36, 37]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                split.split_recordings(*self.paths, prototype_session_ids=selected)
            self.assert_sources_unchanged()
            self.assert_no_destinations()
        with closing(sqlite3.connect(self.paths[0])) as connection, connection:
            connection.execute("UPDATE sessions SET activity='normal'")
        connection = split._connect(self.paths[0], readonly=True)
        try:
            self.paths[1].write_bytes(split._csv_bytes(split._readings(connection)))
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, "No prototype"):
            split.split_recordings(*self.paths)
        self.assert_no_destinations()

    def test_write_lock_prevents_migration_while_another_writer_holds_source(self):
        connection = sqlite3.connect(self.paths[0])
        try:
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                split.split_recordings(*self.paths)
        finally:
            connection.close()
        self.assert_sources_unchanged()
        self.assert_no_destinations()

    def test_unfinished_session_is_not_archived_as_if_recording_had_stopped(self):
        with closing(sqlite3.connect(self.paths[0])) as connection, connection:
            connection.execute("UPDATE sessions SET ended_at_utc=NULL WHERE id=37")
        before = self.snapshot(self.paths[0])
        with self.assertRaisesRegex(ValueError, "unfinished"):
            split.split_recordings(*self.paths)
        self.assertEqual(self.snapshot(self.paths[0]), before)
        self.assertEqual(self.paths[1].read_bytes(), self.original_csv)
        self.assert_no_destinations()

    def test_csv_edit_during_preparation_cancels_publication_without_discarding_edit(self):
        make_partition = split._make_partition
        edited = self.original_csv + b"external edit\n"

        def edit_after_staging(*args, **kwargs):
            make_partition(*args, **kwargs)
            self.paths[1].write_bytes(edited)

        with mock.patch.object(split, "_make_partition", side_effect=edit_after_staging):
            with self.assertRaisesRegex(ValueError, "changed during preparation"):
                split.split_recordings(*self.paths)
        self.assertEqual(self.snapshot(self.paths[0]), (self.original, self.original_rows))
        self.assertEqual(self.paths[1].read_bytes(), edited)
        self.assert_no_destinations()
        backup = next((self.root / "backups").iterdir())
        self.assertEqual((backup / "original.csv").read_bytes(), self.original_csv)

    def test_publishing_failure_rolls_back_partial_outputs_retaining_verified_backup(self):
        link = split.os.link
        calls = []

        def fail_third(source, destination):
            calls.append(destination)
            if len(calls) == 3:
                raise OSError("injected publication failure")
            return link(source, destination)

        with mock.patch.object(split.os, "link", side_effect=fail_third):
            with self.assertRaisesRegex(OSError, "injected publication failure"):
                split.split_recordings(*self.paths)
        self.assert_sources_unchanged()
        self.assert_no_destinations()
        backup = next((self.root / "backups").iterdir())
        self.assertEqual(self.snapshot(backup / "original.sqlite3"), (self.original, self.original_rows))
        self.assertEqual((backup / "original.csv").read_bytes(), self.original_csv)
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["remaining_destinations"], [])

    def test_archive_failure_restores_already_moved_source_and_removes_own_outputs(self):
        rename = split.os.rename
        calls = []

        def fail_csv(source, target):
            calls.append((source, target))
            if Path(source) == self.paths[1]:
                raise OSError("injected archive failure")
            return rename(source, target)

        with mock.patch.object(split.os, "rename", side_effect=fail_csv):
            with self.assertRaisesRegex(OSError, "injected archive failure"):
                split.split_recordings(*self.paths)
        self.assertGreaterEqual(len(calls), 3)  # DB moved, CSV failed, DB restored.
        self.assert_sources_unchanged()
        self.assert_no_destinations()

    def test_destination_creation_race_is_not_overwritten_or_removed(self):
        link = split.os.link

        def race(source, destination):
            if Path(destination) == self.paths[4]:
                self.paths[4].write_bytes(b"another process owns this")
            return link(source, destination)

        with mock.patch.object(split.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                split.split_recordings(*self.paths)
        self.assert_sources_unchanged()
        self.assertEqual(self.paths[4].read_bytes(), b"another process owns this")
        self.assertFalse(self.paths[2].exists())
        self.assertFalse(self.paths[3].exists())
        self.assertFalse(self.paths[5].exists())

    def test_backup_includes_committed_wal_rows_and_is_standalone(self):
        writer = sqlite3.connect(self.paths[0])
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO recording_metadata VALUES ('wal_only','preserve me')")
            writer.commit()
            self.assertTrue(Path(str(self.paths[0]) + "-wal").exists())
            destination = self.root / "standalone.sqlite3"
            split._backup_database(self.paths[0], destination)
            with closing(sqlite3.connect(destination)) as copy:
                self.assertEqual(copy.execute("SELECT value FROM recording_metadata WHERE key='wal_only'").fetchone()[0], "preserve me")
                self.assertEqual(copy.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertFalse(Path(str(destination) + "-wal").exists())
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()
