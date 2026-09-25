#!/usr/bin/env python3
"""Replay saved readings through the actual C detector, without changing data.

Uses Python's standard library and a native C compiler. To check the original
35 calibration recordings within a larger database, add --check-calibration.
These recordings informed the prototype; matching their labels is not an
independent accuracy measurement.

Example:
    python3 tools/replay_fall_detector.py --check-calibration
"""

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = ROOT / "data" / "calibration_readings.sqlite3"
HEADER_DIRECTORY = ROOT / "CG2028_Assignment" / "Core" / "Inc"
UINT32_MASK = 0xFFFFFFFF
MEASUREMENT_FIELDS = (
    "accel_x_mps2", "accel_y_mps2", "accel_z_mps2", "accel_magnitude_mps2",
    "gyro_x_dps", "gyro_y_dps", "gyro_z_dps", "gyro_magnitude_dps",
)
AXIS_FIELDS = tuple(field for field in MEASUREMENT_FIELDS if "magnitude" not in field)
DECISION_EVENTS = {"FALL", "NEAR_FALL", "UNCERTAIN"}

# This adapter only transports inputs and events. All detection decisions come
# from the firmware header, not a separately implemented Python classifier.
ADAPTER_SOURCE = r'''
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "fall_detector.h"

static const char *event_name(FallDetectorEvent event)
{
    switch (event)
    {
    case FALL_EVENT_NONE: return "NONE";
    case FALL_EVENT_READY: return "READY";
    case FALL_EVENT_SPIKE: return "SPIKE";
    case FALL_EVENT_NEAR_FALL: return "NEAR_FALL";
    case FALL_EVENT_FALL: return "FALL";
    case FALL_EVENT_UNCERTAIN: return "UNCERTAIN";
    case FALL_EVENT_SENSOR_FAULT: return "SENSOR_FAULT";
    case FALL_EVENT_RESTARTED: return "RESTARTED";
    case FALL_EVENT_DISTURBANCE_CONFIRMED: return "DISTURBANCE_CONFIRMED";
    case FALL_EVENT_DISTURBANCE_REJECTED: return "DISTURBANCE_REJECTED";
    case FALL_EVENT_DISTURBANCE_UNKNOWN: return "DISTURBANCE_UNKNOWN";
    default: return "UNKNOWN";
    }
}

static void output(uint64_t session, uint64_t record, uint32_t tick,
                   const char *event, const FallDetector *detector)
{
    printf("%" PRIu64 ",%" PRIu64 ",%" PRIu32 ",%s,%s,%d,%.17g,%.17g,%.17g\n",
           session, record, tick, event, FallDetector_StateName(detector->state),
           detector->fall_latched ? 1 : 0, detector->candidate_baseline_mean,
           detector->event_accel_msd_mean, detector->disturbance_ratio);
}

int main(void)
{
    FallDetector detector;
    FallDetector_Init(&detector);
    uint64_t session = 0;
    uint64_t last_record = 0;
    uint32_t last_tick = 0;
    bool have_sample = false;
    char command;
    puts("session_id,record_id,board_time_ms,event,state,fall_latched,baseline_mean,event_mean,disturbance_ratio");
    while (scanf(" %c", &command) == 1)
    {
        if (command == 'R')
        {
            if (have_sample) output(session, last_record, last_tick, "END", &detector);
            if (scanf(" %" SCNu64, &session) != 1) return 2;
            FallDetector_Init(&detector);
            have_sample = false;
            continue;
        }
        if (command != 'S') return 2;
        uint64_t record;
        uint32_t tick;
        int valid, msd_valid;
        double accel_msd, gyro_msd, gyro_magnitude;
        if (scanf(" %" SCNu64 " %" SCNu32 " %d %d %lf %lf %lf",
                  &record, &tick, &valid, &msd_valid,
                  &accel_msd, &gyro_msd, &gyro_magnitude) != 7) return 2;
        const FallDetectorInput input = {
            .time_ms = tick, .valid = valid != 0, .msd_valid = msd_valid != 0,
            .accel_msd = accel_msd, .gyro_msd = gyro_msd,
            .gyro_magnitude = gyro_magnitude
        };
        FallDetectorEvent event = FallDetector_Update(&detector, &input);
        last_record = record;
        last_tick = tick;
        have_sample = true;
        if (event != FALL_EVENT_NONE)
            output(session, record, tick, event_name(event), &detector);
    }
    if (have_sample) output(session, last_record, last_tick, "END", &detector);
    return ferror(stdin) ? 2 : 0;
}
'''


def load_recordings(path):
    """Open an existing SQLite database in read-only mode, including its WAL."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Recording database does not exist: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        # Both queries must see the same snapshot if a logger is appending.
        connection.execute("BEGIN")
        objects = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        if {"sessions", "readings"} <= objects:
            sessions = [dict(row) for row in connection.execute("SELECT * FROM sessions ORDER BY id")]
            readings = [dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY session_id, record_id")]
            for session in sessions:
                session["source_kind"] = "calibration_session"
        elif {"runs", "test_readings"} <= objects:
            sessions = []
            for row in connection.execute("SELECT * FROM runs ORDER BY run_id"):
                run = dict(row)
                run.update(id=run["run_id"], source_kind="test_run", source_session_id=run["session_id"])
                sessions.append(run)
            readings = []
            for row in connection.execute("SELECT * FROM test_readings ORDER BY run_id,record_id"):
                sample = dict(row)
                sample["source_session_id"] = sample["session_id"]
                sample["session_id"] = sample["run_id"]
                readings.append(sample)
        else:
            raise ValueError("Database needs sessions/readings or runs/test_readings")
    finally:
        connection.close()
    return sessions, readings


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def adapter_input(sessions, readings):
    by_session = {session["id"]: [] for session in sessions}
    for row in readings:
        by_session[row["session_id"]].append(row)
    lines = []
    diagnostics = {}
    for session in sessions:
        sid = session["id"]
        lines.append(f"R {sid}\n")
        rows = by_session[sid]
        previous = None
        gaps = invalid = 0
        for row in rows:
            tick = row["board_time_ms"]
            number = row["sample_number"]
            if not isinstance(tick, int) or not 0 <= tick <= UINT32_MASK:
                raise ValueError(f"Record {row['record_id']} has no valid uint32 board timestamp.")
            if not isinstance(number, int) or not 0 <= number <= UINT32_MASK:
                raise ValueError(f"Record {row['record_id']} has no valid uint32 sample counter.")
            valid = all(finite(row[field]) for field in MEASUREMENT_FIELDS)
            # The stored format has no per-read hardware status. All six axes
            # persistently zero was a known failed recording, not table rest.
            valid = valid and any(row[field] != 0 for field in AXIS_FIELDS)
            if previous is not None:
                elapsed = (tick - previous["board_time_ms"]) & UINT32_MASK
                counter_gap = number != ((previous["sample_number"] + 1) & UINT32_MASK)
                time_gap = elapsed == 0 or elapsed > 250
                if counter_gap or time_gap:
                    gaps += 1
                # Long timestamp gaps are handled by the actual header. A
                # counter-only discontinuity (or duplicate tick) must also
                # invalidate history, even though the C input has no counter.
                if (counter_gap and elapsed <= 250) or elapsed == 0:
                    valid = False
            if not valid:
                invalid += 1
            has_msd = row["accel_msd"] is not None and row["gyro_msd"] is not None
            accel = float(row["accel_msd"]) if has_msd else 0.0
            gyro = float(row["gyro_msd"]) if has_msd else 0.0
            magnitude = float(row["gyro_magnitude_dps"]) if finite(row["gyro_magnitude_dps"]) else 0.0
            lines.append(
                f"S {row['record_id']} {tick} {int(valid)} {int(has_msd)} "
                f"{accel:.17g} {gyro:.17g} {magnitude:.17g}\n"
            )
            previous = row
        diagnostics[sid] = {"rows": len(rows), "gaps": gaps, "invalid": invalid}
    return "".join(lines), diagnostics


def replay(sessions, readings, compiler="cc"):
    stream, diagnostics = adapter_input(sessions, readings)
    with tempfile.TemporaryDirectory(prefix="cg2028-fall-replay-") as directory:
        source = Path(directory) / "adapter.c"
        executable = Path(directory) / "adapter"
        source.write_text(ADAPTER_SOURCE, encoding="utf-8")
        compiled = subprocess.run(
            [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-O2", "-I", str(HEADER_DIRECTORY), str(source), "-lm", "-o", str(executable)],
            capture_output=True, text=True, check=False,
        )
        if compiled.returncode:
            raise ValueError(f"Could not compile the detector adapter:\n{compiled.stderr.strip()}")
        result = subprocess.run(
            [str(executable)], input=stream, capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise ValueError(f"Detector replay failed (exit {result.returncode}): {result.stderr.strip()}")
    events = list(csv.DictReader(io.StringIO(result.stdout)))
    for event in events:
        for field in ("session_id", "record_id", "board_time_ms", "fall_latched"):
            event[field] = int(event[field])
        for field in ("baseline_mean", "event_mean", "disturbance_ratio"):
            event[field] = float(event[field])
        if event["event"] == "END":
            diagnostics[event["session_id"]].update(final_state=event["state"],
                                                   final_fall_latched=event["fall_latched"],
                                                   last_board_time_ms=event["board_time_ms"])
    events = [event for event in events if event["event"] != "END"]
    return events, diagnostics


def check_calibration(sessions, events, diagnostics):
    """Check known examples only; never advertise these counts as accuracy."""
    identifiers = {session["id"] for session in sessions}
    if not set(range(1, 36)) <= identifiers or any(session.get("source_kind") == "test_run" for session in sessions):
        raise ValueError("--check-calibration requires original calibration sessions 1 through 35 (additional sessions allowed).")
    failures = []
    for sid in range(1, 36):
        found = [event for event in events if event["session_id"] == sid]
        spikes = [event for event in found if event["event"] == "SPIKE"]
        decisions = [event for event in found if event["event"] in DECISION_EVENTS]
        expected = [] if sid <= 20 else ["FALL"] if sid <= 30 else ["NEAR_FALL"]
        confirmed = [event for event in found if event["event"] == "DISTURBANCE_CONFIRMED"]
        if len(confirmed) != len(expected):
            failures.append(f"session {sid}: disturbance confirmations={len(confirmed)}")
        if [event["event"] for event in decisions] != expected or len(spikes) != len(expected):
            failures.append(f"session {sid}: spikes={len(spikes)}, decisions={[event['event'] for event in decisions]}")
        if diagnostics[sid]["invalid"] or any(event["event"] == "SENSOR_FAULT" for event in found):
            failures.append(f"session {sid}: invalid sensor readings or sensor fault")
        if expected and len(spikes) == 1 and len(decisions) == 1:
            delay = (decisions[0]["board_time_ms"] - spikes[0]["board_time_ms"]) & UINT32_MASK
            if not 8000 <= delay <= 8250:
                failures.append(f"session {sid}: decision delay {delay} ms, expected the first sample at/after 8 s")
    if failures:
        raise ValueError("Calibration replay did not match:\n" + "\n".join(failures))


def print_summary(sessions, events, diagnostics):
    print("Actual C detector replay (saved EWMA metrics; SQLite opened read-only)")
    print("Session/run  Rows Gaps Invalid Spikes Confirm Reject Unknown  Outcome; final state")
    for session in sessions:
        sid = session["id"]
        found = [event for event in events if event["session_id"] == sid]
        spikes = [event for event in found if event["event"] == "SPIKE"]
        decisions = [event for event in found if event["event"] in DECISION_EVENTS]
        confirmed = sum(event["event"] == "DISTURBANCE_CONFIRMED" for event in found)
        rejected = sum(event["event"] == "DISTURBANCE_REJECTED" for event in found)
        unknown = sum(event["event"] == "DISTURBANCE_UNKNOWN" for event in found)
        outcome = "NO EVENT"
        if decisions:
            outcome = " -> ".join(event["event"] for event in decisions)
            if spikes:
                delay = (decisions[-1]["board_time_ms"] - spikes[0]["board_time_ms"]) & UINT32_MASK
                outcome += f" ({delay / 1000:.3f} s)"
        elif diagnostics[sid].get("final_state") == "OBSERVING":
            outcome = "OBSERVATION INCOMPLETE"
        elif unknown or diagnostics[sid].get("final_state") == "WARMUP":
            outcome = "INSUFFICIENT HISTORY/EVIDENCE"
        elif rejected:
            outcome = "DISTURBANCES REJECTED"
        elif spikes:
            outcome = "CANDIDATE CANCELLED"
        if any(event["event"] == "SENSOR_FAULT" for event in found):
            outcome += "; SENSOR_FAULT"
        info = diagnostics[sid]
        print(f"{sid:11d} {info['rows']:5d} {info['gaps']:4d} {info['invalid']:7d} {len(spikes):6d} "
              f"{confirmed:7d} {rejected:6d} {unknown:7d}  {outcome}; {info.get('final_state', 'NO SAMPLES')}")
    print("Gaps restart detector warmup; stale prefixes are not joined to later samples.")
    print("Replay uses saved rounded metrics and cannot reproduce sensor reads or EWMA resets.")
    print("Every capture starts with fresh state; pre-capture detector history and inherited alarms are unavailable.")
    print("A candidate still OBSERVING at the last sample has no final verdict in this replay.")
    print("These recordings informed the thresholds: this is a calibration check, not accuracy validation.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--cc", default="cc", help="Native C compiler executable (default: cc)")
    parser.add_argument("--check-calibration", action="store_true", help="Assert the expected results for current sessions 1–35")
    parser.add_argument("--events", action="store_true", help="Also print each event with its original database record ID and board tick")
    parser.add_argument("--session", type=int, action="append", help="Include only these calibration session IDs; repeatable")
    parser.add_argument("--run-id", type=int, action="append", help="Include only these verdict test run IDs; repeatable")
    parser.add_argument("--json", type=Path, help="Write replay events, final states and input metadata as a JSON report")
    args = parser.parse_args(argv)
    try:
        sessions, readings = load_recordings(args.db)
        if args.session and args.run_id:
            raise ValueError("Choose --session or --run-id")
        selected = args.run_id or args.session
        if selected:
            is_test = bool(sessions and sessions[0].get("source_kind") == "test_run")
            if bool(args.run_id) != is_test:
                raise ValueError("Use --run-id with verdict databases and --session with measurement databases")
            unknown = set(selected) - {session["id"] for session in sessions}
            if unknown:
                raise ValueError(f"Unknown recording IDs: {sorted(unknown)}")
            sessions = [session for session in sessions if session["id"] in selected]
            readings = [row for row in readings if row["session_id"] in selected]
        events, diagnostics = replay(sessions, readings, args.cc)
        print_summary(sessions, events, diagnostics)
        if args.events:
            print("\nEvents:")
            for event in events:
                print(event)
        if args.check_calibration:
            check_calibration(sessions, events, diagnostics)
            print("Calibration check passed: 20 no-event sessions, 10 FALL patterns, 5 NEAR_FALL patterns.")
        if args.json:
            args.json = args.json.expanduser()
            if args.json.resolve() == args.db.expanduser().resolve():
                raise ValueError("JSON output must not replace the input database")
            args.json.parent.mkdir(parents=True, exist_ok=True)
            # Never overwrite an existing analysis or any recording file.
            with args.json.open("x", encoding="utf-8") as output:
                json.dump({"database": str(args.db.resolve()),
                           "header_sha256": hashlib.sha256((HEADER_DIRECTORY / "fall_detector.h").read_bytes()).hexdigest(),
                           "sessions": sessions, "events": events, "diagnostics": diagnostics,
                           "limitations": "Fresh detector per capture; stored rounded metrics; calibration only, not independent validation."},
                          output, indent=2, allow_nan=False)
                output.write("\n")
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Replay error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
