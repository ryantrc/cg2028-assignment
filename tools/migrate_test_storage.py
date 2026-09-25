#!/usr/bin/env python3
"""Move legacy test measurements into their existing verdict runs.

Stop all recorders first. Only sessions with exact existing verdict provenance
are moved; activity names never determine membership or expected verdicts.
SQLite backups and both original CSVs precede any schema upgrade or import.
The default command previews the move; --apply performs it. Interrupted copies
are safe to retry because imports are idempotent and precede source deletion.
"""

import argparse
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from detector_verdicts import VerdictRecorder
from record_activity import CSV_FIELDS
from split_recordings import (
    _backup_database, _check_database, _connect, _csv_bytes, _manifest,
    _readings, _snapshot, _validate_csv, _write_bytes,
)


DATA = Path(__file__).resolve().parent.parent / "data"


def _paths(source_db, source_csv, verdict_db, verdict_csv):
    paths = tuple(Path(path).expanduser().resolve() for path in
                  (source_db, source_csv, verdict_db, verdict_csv))
    if len(set(paths)) != 4:
        raise ValueError("All four input paths must be different")
    for index, path in enumerate(paths):
        if not path.is_file():
            raise FileNotFoundError(path)
        if any(os.path.samefile(path, other) for other in paths[:index]):
            raise ValueError("Input paths must not alias the same file")
    return paths


def _rows(connection, table, order):
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")]


def _validate_verdict_csv(content, runs):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8"), newline=""))
    fields = reader.fieldnames
    if not fields or len(fields) != len(set(fields)) or "run_id" not in fields:
        raise ValueError("Malformed verdict CSV header")
    if runs and set(fields) != set(runs[0]):
        raise ValueError("Verdict CSV columns differ from its database schema")
    expected = [{key: "" if row[key] is None else str(row[key]) for key in fields} for row in runs]
    if list(reader) != expected:
        raise ValueError("Verdict CSV differs from SQLite; reconcile it before migration")


def _matches(source, verdict, source_path):
    sessions = _rows(source, "sessions", "id")
    runs = _rows(verdict, "runs", "run_id")
    if any(row["ended_at_utc"] is None for row in sessions):
        raise ValueError("A measurement session is unfinished; stop the recorder first")
    if any(row["recording_status"] == "OPEN" for row in runs):
        raise ValueError("A verdict run is open; stop the recorder first")
    matches = []
    for session in sessions:
        same = [run for run in runs if
                Path(run["source_database"]).expanduser().resolve() == source_path
                and run["session_id"] == session["id"]
                and run["started_at_utc"] == session["started_at_utc"]]
        if len(same) > 1:
            raise ValueError("Multiple verdict runs match the same source session")
        if not same:
            continue
        run = same[0]
        count = source.execute("SELECT COUNT(*) FROM samples WHERE session_id=?", (session["id"],)).fetchone()[0]
        for field in ("activity", "notes", "ended_at_utc"):
            if session[field] != run[field]:
                raise ValueError(f"Session {session['id']} has mismatched verdict {field}")
        if run["sample_count"] != count:
            raise ValueError(f"Session {session['id']} sample count disagrees with its verdict run")
        if run.get("port") not in (None, session["port"]):
            raise ValueError(f"Session {session['id']} has a conflicting port")
        matches.append({"session": session, "run_id": run["run_id"], "historical": bool(run["historical"]),
                        "expected_verdict": run.get("expected_verdict"), "samples": count})
    return matches, runs


def _expected_source(original, session_ids):
    result = {"schema": original["schema"], "tables": dict(original["tables"])}
    for table, key in (("samples", "session_id"), ("sessions", "id")):
        columns, rows = original["tables"][table]
        result["tables"][table] = (columns, [row for row in rows if row[columns.index(key)] not in session_ids])
    return result


def _verify_imports(connection, original_rows, matches):
    for match in matches:
        expected = [row for row in original_rows if row["session_id"] == match["session"]["id"]]
        actual = _rows_for_run(connection, match["run_id"])
        if len(actual) != len(expected):
            raise ValueError("Imported test sample count differs from the source")
        actual_by_id = {row["legacy_record_id"]: row for row in actual}
        for row in expected:
            saved = actual_by_id.get(row["record_id"])
            if saved is None or any(saved[field] != row[field] for field in CSV_FIELDS if field != "record_id"):
                raise ValueError("Imported test sample values or legacy record IDs differ from the source")


def _rows_for_run(connection, run_id):
    return [dict(row) for row in connection.execute("SELECT * FROM test_readings WHERE run_id=? ORDER BY legacy_record_id", (run_id,))]


def _replace_csv(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".test-storage-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _recover_csv(source, verdict, paths):
    """Repair only a documented exact CSV lag after a committed deletion."""
    rows = _readings(source)
    try:
        _validate_csv(paths[1].read_bytes(), rows)
        return False
    except ValueError as mismatch:
        for manifest_path in sorted((paths[0].parent / "backups").glob("test-storage-*/manifest.json"), reverse=True):
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("paths") != [str(path) for path in paths] or not manifest.get("imports_verified"):
                continue
            backup = manifest_path.parent
            if paths[1].read_bytes() != (backup / "measurements.csv").read_bytes():
                continue
            old = _connect(backup / "measurements.sqlite3", readonly=True)
            try:
                expected = _expected_source(_snapshot(old), {item["session"]["id"] for item in manifest["matches"]})
                if _snapshot(source) != expected:
                    continue
                _verify_imports(verdict, _readings(old), manifest["matches"])
            finally:
                old.close()
            _replace_csv(paths[1], _csv_bytes(rows))
            manifest["status"] = "complete"
            manifest["recovered_csv"] = True
            _manifest(backup, manifest)
            return True
        raise ValueError("Measurement CSV differs from SQLite; no verified interrupted migration can explain it") from mismatch


def migrate_test_storage(source_db, source_csv, verdict_db, verdict_csv, *, apply=True):
    paths = _paths(source_db, source_csv, verdict_db, verdict_csv)
    source = _connect(paths[0], readonly=not apply)
    verdict = _connect(paths[2], readonly=not apply)
    recorder = None
    directory = None
    result = {"paths": [str(path) for path in paths], "status": "preparing", "imports_verified": False}
    try:
        source.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        verdict.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        _check_database(source)
        _check_database(verdict)
        recovered = _recover_csv(source, verdict, paths) if apply else False
        source_content = paths[1].read_bytes()
        original_rows = _readings(source)
        _validate_csv(source_content, original_rows)
        matches, runs = _matches(source, verdict, paths[0])
        verdict_content = paths[3].read_bytes()
        _validate_verdict_csv(verdict_content, runs)
        result.update(matches=matches, sessions=len(matches), samples=sum(item["samples"] for item in matches), recovered_csv=recovered)
        if not matches or not apply:
            result["status"] = "no_changes" if apply else "dry_run"
            return result

        original = _snapshot(source)
        original_verdict = _snapshot(verdict)
        parent = paths[0].parent / "backups"
        parent.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = Path(tempfile.mkdtemp(prefix=f"test-storage-{stamp}-", dir=parent))
        result["backup_path"] = str(directory)
        _manifest(directory, result)
        for database, name, snapshot in ((paths[0], "measurements", original), (paths[2], "verdicts", original_verdict)):
            _backup_database(database, directory / f"{name}.sqlite3")
            check = _connect(directory / f"{name}.sqlite3", readonly=True)
            try:
                if _snapshot(check) != snapshot:
                    raise ValueError("Backup does not match the guarded original")
            finally:
                check.close()
        _write_bytes(directory / "measurements.csv", source_content)
        _write_bytes(directory / "verdicts.csv", verdict_content)
        # The recorder owns its own connection and schema upgrade. All writers
        # must remain stopped; the final verification reacquires a write lock.
        verdict.close()
        verdict = None
        recorder = VerdictRecorder(paths[2], paths[3])
        for match in matches:
            session = match["session"]
            run_id = recorder.start_run(paths[0], session["id"], session["activity"], session["notes"],
                                        session["started_at_utc"], historical=match["historical"],
                                        expected_verdict=match["expected_verdict"], port=session["port"])
            if run_id != match["run_id"]:
                raise ValueError("Verdict provenance changed during migration")
            for row in original_rows:
                if row["session_id"] == session["id"]:
                    recorder.import_sample(run_id, row)

        recorder.connection.execute("BEGIN IMMEDIATE")
        _verify_imports(recorder.connection, original_rows, matches)
        # Preserve every pre-existing verdict, diagnostic, and summary field.
        if len(_rows(recorder.connection, "runs", "run_id")) != len(runs):
            raise ValueError("Verdict runs changed concurrently during migration")
        for old_run in runs:
            saved = recorder.get_run(old_run["run_id"])
            filled_port = old_run.get("port") is None and any(item["run_id"] == old_run["run_id"] for item in matches)
            if any(saved[key] != value for key, value in old_run.items() if not (key == "port" and filled_port)):
                raise ValueError("Migration changed an existing verdict or run metadata")
        columns, events = original_verdict["tables"]["events"]
        if [tuple(row[column] for column in columns) for row in _rows(recorder.connection, "events", "event_id")] != events:
            raise ValueError("Migration changed diagnostic events")
        old_metadata = dict(original_verdict["tables"]["verdict_metadata"][1])
        new_metadata = dict(recorder.connection.execute("SELECT key,value FROM verdict_metadata"))
        if any(new_metadata.get(key) != value for key, value in old_metadata.items() if key != "schema_id"):
            raise ValueError("Migration changed original verdict metadata")
        recorder.export_csv()
        _validate_verdict_csv(paths[3].read_bytes(), _rows(recorder.connection, "runs", "run_id"))
        _check_database(recorder.connection)
        if paths[1].read_bytes() != source_content or _snapshot(source) != original:
            raise ValueError("Measurement source changed during migration")
        result.update(status="imports_verified", imports_verified=True)
        _manifest(directory, result)
        ids = {match["session"]["id"] for match in matches}
        for session_id in ids:
            source.execute("DELETE FROM samples WHERE session_id=?", (session_id,))
            source.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        if _snapshot(source) != _expected_source(original, ids):
            raise ValueError("Unexpected source schema, metadata, sequence, or row changes")
        _check_database(source)
        remaining_csv = _csv_bytes(_readings(source))
        source.execute("COMMIT")
        result["status"] = "source_committed"
        _manifest(directory, result)
        source.execute("BEGIN IMMEDIATE")
        if _snapshot(source) != _expected_source(original, ids):
            raise ValueError("Measurement database changed before CSV publication")
        _replace_csv(paths[1], remaining_csv)
        _validate_csv(paths[1].read_bytes(), _readings(source))
        result["status"] = "complete"
        _manifest(directory, result)
        return result
    except BaseException as error:
        if directory is not None:
            result.update(status="failed", error=str(error))
            _manifest(directory, result)
        raise
    finally:
        if recorder is not None:
            if recorder.connection.in_transaction:
                recorder.connection.rollback()
            recorder.close()
        if verdict is not None:
            verdict.close()
        source.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (("source-db", "prototype_readings.sqlite3"), ("source-csv", "prototype_readings.csv"),
                          ("verdict-db", "prototype_verdicts.sqlite3"), ("verdict-csv", "prototype_verdicts.csv")):
        parser.add_argument("--" + name, type=Path, default=DATA / default)
    parser.add_argument("--apply", action="store_true")
    args = vars(parser.parse_args(argv))
    try:
        result = migrate_test_storage(**args)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Migration failed: {error}\nOriginal backups remain under data/backups/test-storage-*.\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
