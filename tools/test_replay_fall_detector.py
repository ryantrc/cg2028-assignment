"""Read-only storage adapters and actual-C replay integration checks."""

from contextlib import closing, redirect_stdout
import hashlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

import replay_fall_detector as replay


def sample(index, *, session=1, accel=0.0):
    return {
        "record_id": index + 1000, "session_id": session,
        "sample_number": index, "board_time_ms": index * 100,
        "accel_x_mps2": 0.0, "accel_y_mps2": 0.0, "accel_z_mps2": 9.8,
        "accel_magnitude_mps2": 9.8, "gyro_x_dps": 0.0,
        "gyro_y_dps": 0.0, "gyro_z_dps": 1.5, "gyro_magnitude_dps": 1.5,
        "accel_msd": accel, "gyro_msd": 0.0,
    }


class ActualCReplayTests(unittest.TestCase):
    def test_confirmed_disturbance_retains_original_eight_second_deadline(self):
        readings = [sample(index, accel=2 if index <= 50 else 8 if index < 71 else 0)
                    for index in range(132)]
        events, diagnostics = replay.replay([{"id": 1}], readings)
        self.assertEqual([event["event"] for event in events],
                         ["READY", "SPIKE", "DISTURBANCE_CONFIRMED", "FALL"])
        confirmed = events[2]
        self.assertEqual((confirmed["baseline_mean"], confirmed["event_mean"], confirmed["disturbance_ratio"]),
                         (2.0, 8.0, 4.0))
        self.assertEqual(events[-1]["board_time_ms"] - events[1]["board_time_ms"], 8000)
        self.assertEqual(diagnostics[1]["final_state"], "FALL_LATCHED")

    def test_sustained_running_rejected_without_near_fall_or_repeated_trigger(self):
        events, diagnostics = replay.replay([{"id": 1}], [sample(index, accel=8) for index in range(160)])
        self.assertEqual([event["event"] for event in events], ["READY", "SPIKE", "DISTURBANCE_REJECTED"])
        self.assertEqual(events[-1]["disturbance_ratio"], 1.0)
        self.assertEqual(diagnostics[1]["final_state"], "NORMAL")
        self.assertEqual(diagnostics[1]["final_fall_latched"], 0)

    def test_truncated_candidate_stays_explicitly_incomplete(self):
        readings = [sample(index, accel=2 if index <= 50 else 8) for index in range(60)]
        events, diagnostics = replay.replay([{"id": 1}], readings)
        self.assertEqual([event["event"] for event in events], ["READY", "SPIKE"])
        self.assertEqual(diagnostics[1]["final_state"], "OBSERVING")
        self.assertEqual(diagnostics[1]["last_board_time_ms"], 5900)

    def test_counter_only_gap_cancels_candidate_instead_of_joining_history(self):
        readings = [sample(index, accel=0 if index <= 50 else 8) for index in range(90)]
        for row in readings[60:]:
            row["sample_number"] += 2
        events, diagnostics = replay.replay([{"id": 1}], readings)
        self.assertEqual(diagnostics[1]["gaps"], 1)
        self.assertEqual(diagnostics[1]["invalid"], 1)
        self.assertIn("SENSOR_FAULT", [event["event"] for event in events])
        self.assertNotIn("DISTURBANCE_CONFIRMED", [event["event"] for event in events])
        self.assertEqual(diagnostics[1]["final_state"], "WARMUP")

    def test_counter_and_board_tick_wrap_are_not_a_discontinuity(self):
        rows = [sample(index) for index in range(4)]
        for index, row in enumerate(rows):
            row["board_time_ms"] = (0xFFFFFFFF - 150 + index * 100) & 0xFFFFFFFF
            row["sample_number"] = (0xFFFFFFFF - 1 + index) & 0xFFFFFFFF
        _, diagnostics = replay.adapter_input([{"id": 1}], rows)
        self.assertEqual(diagnostics[1]["gaps"], 0)
        self.assertEqual(diagnostics[1]["invalid"], 0)


class StorageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "recordings.sqlite3"

    def test_verdict_readings_are_grouped_by_run_id_with_original_ids_preserved(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE runs(run_id INTEGER,session_id INTEGER,activity TEXT)")
            connection.executemany("INSERT INTO runs VALUES (?,?,?)", [(3, 7, "first"), (8, 7, "second")])
            connection.execute("CREATE TABLE test_readings(record_id INTEGER,session_id INTEGER,run_id INTEGER,legacy_record_id INTEGER)")
            connection.executemany("INSERT INTO test_readings VALUES (?,?,?,?)", [(111, 7, 3, 900), (222, 7, 8, None)])
        before = hashlib.sha256(self.path.read_bytes()).digest()
        sessions, readings = replay.load_recordings(self.path)
        self.assertEqual([session["id"] for session in sessions], [3, 8])
        self.assertEqual([row["session_id"] for row in readings], [3, 8])
        self.assertEqual([row["source_session_id"] for row in readings], [7, 7])
        self.assertEqual([row["record_id"] for row in readings], [111, 222])
        self.assertEqual(readings[0]["legacy_record_id"], 900)
        self.assertTrue(all(row["source_kind"] == "test_run" for row in sessions))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)

    def test_original_calibration_checker_accepts_newer_sessions_without_counting_them(self):
        sessions = [{"id": sid} for sid in [*range(1, 36), 55]]
        events = []
        for sid in range(21, 36):
            for name, time in (("SPIKE", 10000), ("DISTURBANCE_CONFIRMED", 12000),
                               ("FALL" if sid <= 30 else "NEAR_FALL", 18000)):
                events.append({"session_id": sid, "event": name, "board_time_ms": time})
        # An extra capture is intentionally unfinished and should not be
        # asserted to match any inferred label by the original calibration check.
        events.append({"session_id": 55, "event": "SPIKE", "board_time_ms": 100})
        diagnostics = {session["id"]: {"invalid": 0} for session in sessions}
        replay.check_calibration(sessions, events, diagnostics)

    def test_missing_database_is_never_created(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            replay.load_recordings(self.path)
        self.assertFalse(self.path.exists())

    def test_short_capture_summary_identifies_missing_history(self):
        diagnostics = {1: {"rows": 10, "gaps": 0, "invalid": 0, "final_state": "WARMUP"}}
        output = io.StringIO()
        with redirect_stdout(output):
            replay.print_summary([{"id": 1}], [], diagnostics)
        self.assertIn("INSUFFICIENT HISTORY/EVIDENCE; WARMUP", output.getvalue())
        self.assertNotIn("NO EVENT;", output.getvalue())

    def test_unknown_history_is_not_hidden_by_a_later_rejection(self):
        diagnostics = {1: {"rows": 100, "gaps": 0, "invalid": 0, "final_state": "NORMAL"}}
        events = [{"session_id": 1, "event": name, "board_time_ms": tick} for name, tick in
                  (("DISTURBANCE_UNKNOWN", 1000), ("SPIKE", 6200), ("DISTURBANCE_REJECTED", 8200))]
        output = io.StringIO()
        with redirect_stdout(output):
            replay.print_summary([{"id": 1}], events, diagnostics)
        self.assertIn("INSUFFICIENT HISTORY/EVIDENCE; NORMAL", output.getvalue())
        self.assertNotIn("DISTURBANCES REJECTED;", output.getvalue())


if __name__ == "__main__":
    unittest.main()
