#!/usr/bin/env python3
"""Split calibration and prototype recordings while preserving original IDs.

Stop all recorders before applying this one-time migration. By default this
command validates the source and prints a plan; --apply performs the split.
Original data is backed up before changes. Existing destinations are never
overwritten. After successful verification the old working files are archived.

If interrupted, inspect backups/recording-split-*/manifest.json. Verified backups
remain available even if publication or archiving fails. A repeat invocation
refuses existing destinations; it never silently repeats or merges a migration.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from record_activity import CSV_FIELDS


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _connect(path, readonly=False):
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro" if readonly else str(path),
        uri=readonly, timeout=0, isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _snapshot(connection):
    schema = [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    )]
    tables = {}
    for kind, name, _, _ in schema:
        if kind == "table":
            columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({_quote(name)})"))
            tables[name] = (columns, [tuple(row) for row in connection.execute(f"SELECT * FROM {_quote(name)}")])
    return {"schema": schema, "tables": tables}


def _readings(connection):
    return [dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY record_id")]


def _csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _validate_csv(content, rows):
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8"), newline=""))
        if tuple(reader.fieldnames or ()) != tuple(CSV_FIELDS):
            raise ValueError("Source CSV header does not match the current recording schema.")
        exported = list(reader)
    except (UnicodeError, csv.Error) as error:
        raise ValueError(f"Cannot read source CSV: {error}") from error
    expected = [{key: "" if row[key] is None else str(row[key]) for key in CSV_FIELDS} for row in rows]
    if exported != expected:
        raise ValueError("Source CSV differs from SQLite. No split was performed; reconcile the source files first.")


def _check_database(connection):
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise ValueError(f"SQLite integrity check failed: {integrity}")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("SQLite contains a foreign-key violation.")


def _paths(source_db, source_csv, calibration_db, calibration_csv, prototype_db, prototype_csv):
    paths = tuple(Path(path).expanduser().resolve() for path in
                  (source_db, source_csv, calibration_db, calibration_csv, prototype_db, prototype_csv))
    if len(set(paths)) != 6:
        raise ValueError("Source and destination paths must all be different.")
    for path in paths[2:]:
        if path.exists():
            raise FileExistsError(f"Destination already exists; refusing to overwrite: {path}")
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(str(path) + suffix).exists():
                raise FileExistsError(f"Destination has an existing SQLite sidecar: {path}{suffix}")
    for path in paths[:2]:
        if not path.is_file():
            raise FileNotFoundError(f"Source file is missing: {path}")
    return paths


def _selection(connection, source_csv, prototype_session_ids):
    _check_database(connection)
    original = _snapshot(connection)
    if "recording_metadata" not in original["tables"]:
        raise ValueError("The source must use the current schema with recording_metadata.")
    sessions = [dict(row) for row in connection.execute("SELECT * FROM sessions ORDER BY id")]
    if any(row["ended_at_utc"] is None for row in sessions):
        raise ValueError("A source session is unfinished. Stop the recorder and finish that session before splitting.")
    identifiers = {row["id"] for row in sessions}
    if prototype_session_ids is None:
        prototypes = {row["id"] for row in sessions if row["activity"].lower().startswith("prototype")}
    else:
        prototypes = set(prototype_session_ids)
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in prototypes):
            raise ValueError("Explicit session IDs must be positive integers.")
        unknown = prototypes - identifiers
        if unknown:
            raise ValueError(f"Prototype session IDs do not exist: {sorted(unknown)}")
    if not prototypes:
        raise ValueError("No prototype sessions were selected; source files were left unchanged.")
    calibration = identifiers - prototypes
    if not calibration:
        raise ValueError("No calibration sessions would remain; check the selected prototype IDs.")
    rows = _readings(connection)
    content = source_csv.read_bytes()
    _validate_csv(content, rows)
    partitions = {
        "calibration": {"session_ids": sorted(calibration), "rows": [row for row in rows if row["session_id"] in calibration]},
        "prototype": {"session_ids": sorted(prototypes), "rows": [row for row in rows if row["session_id"] in prototypes]},
    }
    return original, content, partitions


def _summary(paths, partitions):
    result = {"source_db": str(paths[0]), "source_csv": str(paths[1])}
    for name, db, csv_path in (("calibration", paths[2], paths[3]), ("prototype", paths[4], paths[5])):
        part = partitions[name]
        result[name] = {"session_ids": part["session_ids"], "sessions": len(part["session_ids"]),
                        "samples": len(part["rows"]), "db": str(db), "csv": str(csv_path)}
    return result


def plan_split(source_db, source_csv, calibration_db, calibration_csv, prototype_db, prototype_csv,
               prototype_session_ids=None):
    paths = _paths(source_db, source_csv, calibration_db, calibration_csv, prototype_db, prototype_csv)
    connection = _connect(paths[0], readonly=True)
    try:
        connection.execute("BEGIN")
        _, _, partitions = _selection(connection, paths[1], prototype_session_ids)
    finally:
        connection.close()
    return {"status": "dry_run", **_summary(paths, partitions)}


def _backup_database(source, destination):
    reader = _connect(source, readonly=True)
    writer = _connect(destination)
    try:
        reader.backup(writer)
        # Ensure the backup stands alone; never depend on the source's WAL.
        writer.execute("PRAGMA journal_mode=DELETE")
        _check_database(writer)
    finally:
        writer.close()
        reader.close()


def _expected_partition(original, ids, dataset):
    result = {"schema": original["schema"], "tables": dict(original["tables"])}
    for table, column in (("sessions", "id"), ("samples", "session_id")):
        columns, rows = original["tables"][table]
        index = columns.index(column)
        result["tables"][table] = (columns, [row for row in rows if row[index] in ids])
    columns, rows = original["tables"]["recording_metadata"]
    if columns != ("key", "value"):
        raise ValueError("Unexpected recording_metadata columns; refusing an unsafe migration.")
    metadata = dict(rows)
    metadata["recording_dataset"] = dataset
    result["tables"]["recording_metadata"] = (columns, sorted(metadata.items()))
    return result


def _normalized(snapshot):
    # Metadata key order is irrelevant; all other rows, including sequence
    # values and auxiliary tables, must be preserved exactly.
    result = {"schema": snapshot["schema"], "tables": dict(snapshot["tables"])}
    columns, rows = result["tables"]["recording_metadata"]
    result["tables"]["recording_metadata"] = (columns, sorted(rows))
    return result


def _make_partition(backup_db, staged_db, original, partition, dataset):
    _backup_database(backup_db, staged_db)
    connection = _connect(staged_db)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TEMP TABLE keep_sessions (id INTEGER PRIMARY KEY)")
        connection.executemany("INSERT INTO keep_sessions VALUES (?)", [(sid,) for sid in partition["session_ids"]])
        connection.execute("DELETE FROM samples WHERE session_id NOT IN (SELECT id FROM keep_sessions)")
        connection.execute("DELETE FROM sessions WHERE id NOT IN (SELECT id FROM keep_sessions)")
        connection.execute("""INSERT INTO recording_metadata(key,value) VALUES ('recording_dataset',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (dataset,))
        connection.execute("COMMIT")
        _check_database(connection)
        expected = _expected_partition(original, set(partition["session_ids"]), dataset)
        if _normalized(_snapshot(connection)) != expected:
            raise ValueError(f"{dataset} partition changed unexpected schema, IDs, values, metadata, or sequence counters.")
        if _readings(connection) != partition["rows"]:
            raise ValueError(f"{dataset} partition readings differ from the original rows.")
    finally:
        connection.close()


def _write_bytes(path, content):
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _manifest(directory, result):
    temporary = directory / "manifest.json.tmp"
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, directory / "manifest.json")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_sources(paths, directory):
    """Only called after every SQLite connection has closed."""
    archive = directory / "retired_working_files"
    archive.mkdir()
    sources = [paths[0], paths[1]]
    sources.extend(Path(str(paths[0]) + suffix) for suffix in ("-wal", "-shm", "-journal")
                   if Path(str(paths[0]) + suffix).exists())
    moved = []
    try:
        for source in sources:
            target = archive / source.name
            if target.exists():
                raise FileExistsError(f"Archive name collision: {target}")
            os.rename(source, target)
            moved.append((source, target))
    except BaseException:
        for source, target in reversed(moved):
            if source.exists():
                raise RuntimeError(f"Cannot restore source path created concurrently: {source}. Backups: {directory}")
            os.rename(target, source)
        raise
    return [str(target) for _, target in moved]


def split_recordings(source_db, source_csv, calibration_db, calibration_csv, prototype_db, prototype_csv,
                     prototype_session_ids=None):
    paths = _paths(source_db, source_csv, calibration_db, calibration_csv, prototype_db, prototype_csv)
    source_db, source_csv = paths[:2]
    guard = _connect(source_db)
    directory = None
    stages = []
    published = []
    result = None
    try:
        # A second read connection takes the backup while this write reservation
        # blocks recorder commits. Backing up this same transaction can hang.
        guard.execute("BEGIN IMMEDIATE")
        original, source_content, partitions = _selection(guard, source_csv, prototype_session_ids)
        source_identity = (source_db.stat().st_dev, source_db.stat().st_ino)
        result = _summary(paths, partitions)
        backup_parent = source_db.parent / "backups"
        backup_parent.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = Path(tempfile.mkdtemp(prefix=f"recording-split-{stamp}-", dir=backup_parent))
        backup_db = directory / "original.sqlite3"
        backup_csv = directory / "original.csv"
        result.update(status="preparing", backup_path=str(directory),
                      original_db_backup=str(backup_db), original_csv_backup=str(backup_csv))
        _manifest(directory, result)
        _backup_database(source_db, backup_db)
        _write_bytes(backup_csv, source_content)
        backup_connection = _connect(backup_db, readonly=True)
        try:
            if _snapshot(backup_connection) != original:
                raise ValueError("Original database backup differs from the guarded source snapshot.")
            _validate_csv(backup_csv.read_bytes(), _readings(backup_connection))
        finally:
            backup_connection.close()

        for name, db, csv_path in (("calibration", paths[2], paths[3]), ("prototype", paths[4], paths[5])):
            db.parent.mkdir(parents=True, exist_ok=True)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            # A same-filesystem hard link publishes each completed file without
            # any overwrite race. All staging files are verified beforehand.
            db_stage = Path(tempfile.mkdtemp(prefix=".recording-split-", dir=db.parent)) / db.name
            stages.append((db_stage, db))
            csv_stage = Path(tempfile.mkdtemp(prefix=".recording-split-", dir=csv_path.parent)) / csv_path.name
            stages.append((csv_stage, csv_path))
            _make_partition(backup_db, db_stage, original, partitions[name], name)
            _write_bytes(csv_stage, _csv_bytes(partitions[name]["rows"]))
            _validate_csv(csv_stage.read_bytes(), partitions[name]["rows"])

        if source_csv.read_bytes() != source_content or _snapshot(guard) != original:
            raise ValueError("Source files changed during preparation; split cancelled.")
        for staged, destination in stages:
            digest = _digest(staged)
            os.link(staged, destination)
            published.append((destination, digest))
        if any(_digest(destination) != digest for destination, digest in published):
            raise ValueError("A published destination changed concurrently; source files retained.")
        result["status"] = "published"
        _manifest(directory, result)

        # Do not rename a live SQLite handle or detach its WAL. The recorder
        # must remain stopped during this final file-level retirement step.
        guard.execute("ROLLBACK")
        guard.close()
        guard = None
        verification = _connect(source_db, readonly=True)
        try:
            if _snapshot(verification) != original:
                raise ValueError("Source database changed after publication; original files retained.")
        finally:
            verification.close()
        if source_identity != (source_db.stat().st_dev, source_db.stat().st_ino) or source_csv.read_bytes() != source_content:
            raise ValueError("Source files were replaced or edited; original files retained.")
        result["archived_working_files"] = _archive_sources(paths, directory)
        result["status"] = "complete"
        _manifest(directory, result)
        return result
    except BaseException as error:
        # Never remove a destination that another process has changed, and
        # never discard verified partitions after source retirement succeeded.
        originals_present = source_db.exists() and source_csv.exists()
        for destination, digest in reversed(published):
            sidecar = any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))
            if originals_present and destination.exists() and not sidecar and _digest(destination) == digest:
                destination.unlink()
        if directory is not None and result is not None:
            result.update(status="failed", error=str(error), original_working_files_present=originals_present,
                          remaining_destinations=[str(path) for path in paths[2:] if path.exists()])
            _manifest(directory, result)
        raise
    finally:
        if guard is not None:
            guard.close()
        for staged, _ in stages:
            for path in staged.parent.iterdir():
                path.unlink()
            staged.parent.rmdir()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for option, filename in (
        ("source-db", "activity_readings.sqlite3"), ("source-csv", "activity_readings.csv"),
        ("calibration-db", "calibration_readings.sqlite3"), ("calibration-csv", "calibration_readings.csv"),
        ("prototype-db", "prototype_readings.sqlite3"), ("prototype-csv", "prototype_readings.csv"),
    ):
        parser.add_argument(f"--{option}", type=Path, default=DATA / filename)
    parser.add_argument("--session", type=int, action="append", dest="prototype_session_ids",
                        help="Prototype session ID; repeat to override activity-prefix selection")
    parser.add_argument("--apply", action="store_true", help="Perform the verified split; otherwise validate and print a plan")
    args = vars(parser.parse_args(argv))
    apply = args.pop("apply")
    try:
        result = (split_recordings if apply else plan_split)(**args)
    except (OSError, ValueError, sqlite3.Error, RuntimeError) as error:
        parser.exit(1, f"Split failed: {error}\nInspect data/backups/recording-split-*/manifest.json before retrying if publication started.\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
