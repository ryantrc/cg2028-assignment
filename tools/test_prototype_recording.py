"""Structured modes over a simulated serial port, with real temporary files."""
from contextlib import closing
import csv
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import record_activity as recorder


def frame(n):
    return (f"Sample {n} TimeMs={n * 100} SlopeWindow=5\n"
            "Accel EWMA ASM [m/s^2]: X=0 Y=0 Z=9.8 Magnitude=9.8 MSD=0 MagnitudeSlope=0 MSDSlope=0\n"
            "Gyro EWMA ASM [dps]: X=0 Y=0 Z=1.5 Magnitude=1.5 MSD=0 MagnitudeSlope=0 MSDSlope=0\n").encode()


NORMAL = b"DETECTOR TimeMs=120 State=NORMAL Alarm=0 Sensors=OK Event=STATUS \n"
SPIKE = b"DETECTOR TimeMs=220 State=OBSERVING Alarm=0 Sensors=OK Event=SPIKE observing\n"
FALL = b"DETECTOR TimeMs=8220 State=FALL_LATCHED Alarm=1 Sensors=OK Event=POSSIBLE_FALL reset\n"
NEAR = b"DETECTOR TimeMs=8220 State=NORMAL Alarm=0 Sensors=OK Event=NEAR_FALL moving\n"
CONFIRMED = (b"DETECTOR TimeMs=2220 State=OBSERVING Alarm=0 Sensors=OK Event=DISTURBANCE_CONFIRMED "
             b"BaselineMSD=1.000000e-01 EventMSD=1.000000e+00 IncreaseRatio=1.000000e+01\n")
LOW = (b"DETECTOR TimeMs=2220 State=OBSERVING Alarm=0 Sensors=OK Event=DISTURBANCE_LOW "
       b"BaselineMSD=1.000000e+00 EventMSD=1.000000e+00 IncreaseRatio=1.000000e+00\n")
CONTINUED = b"DETECTOR TimeMs=8220 State=NORMAL Alarm=0 Sensors=OK Event=MOVEMENT_CONTINUED moving\n"
REJECTED = (b"DETECTOR TimeMs=2220 State=NORMAL Alarm=0 Sensors=OK Event=DISTURBANCE_REJECTED "
            b"BaselineMSD=1.000000e+00 EventMSD=1.000000e+00 IncreaseRatio=1.000000e+00\n")
UNKNOWN = b"DETECTOR TimeMs=2220 State=WARMUP Alarm=0 Sensors=OK Event=DISTURBANCE_UNKNOWN insufficient evidence\n"
READY = b"DETECTOR TimeMs=7220 State=NORMAL Alarm=0 Sensors=OK Event=READY baseline ready\n"


class ModeTests(unittest.TestCase):
    def test_standalone_help_imports_without_opening_a_port(self):
        result = subprocess.run(
            [sys.executable, "-B", "-Werror", str(Path(recorder.__file__)), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        for option in ("--test", "--free", "--calibration"):
            self.assertIn(option, result.stdout)

    def test_direct_record_rejects_hardlinked_outputs_before_port_access(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "existing.sqlite3"
            csv_path = Path(directory) / "alias.csv"
            original = b"Existing content must not be changed"
            db.write_bytes(original)
            csv_path.hardlink_to(db)
            args = SimpleNamespace(db=db, csv=csv_path)
            with mock.patch.object(recorder, "prepare_console") as console, \
                 mock.patch.object(recorder, "serial_port") as port, \
                 mock.patch.object(recorder, "SerialReader") as reader:
                with self.assertRaisesRegex(ValueError, "different database/CSV files"):
                    recorder.record(args)
            console.assert_not_called()
            port.assert_not_called()
            reader.assert_not_called()
            self.assertEqual(db.read_bytes(), original)

    def args(self, *options):
        with mock.patch.object(sys, "argv", ["record_activity.py", *options]), \
             mock.patch.object(recorder, "record", return_value=0) as record:
            self.assertEqual(recorder.main(), 0)
        return record.call_args.args[0]

    def test_modes_route_to_exactly_one_pair(self):
        for mode, stem in (("test", "prototype_verdicts"), ("free", "prototype_readings"),
                           ("calibration", "calibration_readings")):
            args = self.args("--" + mode, *(["--verdict", "near-fall"] if mode == "test" else []))
            self.assertEqual(args.db, recorder.DATA_DIR / (stem + ".sqlite3"))
            self.assertEqual(args.csv, recorder.DATA_DIR / (stem + ".csv"))

    def test_timing_and_expected_name_are_separate(self):
        args = self.args("--test", "--name", "fall-test-3", "--verdict", "near-fall")
        self.assertEqual((args.activity, args.verdict, args.duration), ("fall-test-3", "near-fall", 30))
        self.assertEqual(self.args("--test", "--verdict", "normal", "--duration", "60").duration, 60)
        self.assertEqual(self.args("--calibration").duration, 30)
        self.assertIsNone(self.args("--free").duration)

    def test_invalid_options_never_start_recording(self):
        cases = ([], ["--test"], ["--free", "--calibration"],
                 ["--free", "--verdict", "fall"], ["--calibration", "--verdict", "normal"],
                 ["--test", "--verdict", "typo"], ["--calibration", "--duration", "31"],
                 ["--calibration", "--samples", "3"], ["--free", "--duration", "30"],
                 ["--free", "--samples", "3"], ["--free", "--name", " "],
                 ["--free", "--db", "/tmp/same", "--csv", "/tmp/same"])
        for options in cases:
            with self.subTest(options=options), mock.patch.object(sys, "argv", ["record_activity.py", *options]), \
                 mock.patch.object(sys, "stderr", io.StringIO()), mock.patch.object(recorder, "record") as record:
                try:
                    self.assertNotEqual(recorder.main(), 0)
                except SystemExit as error:
                    self.assertEqual(error.code, 2)
                record.assert_not_called()

    def test_custom_files_and_summary(self):
        args = self.args("--test", "--verdict", "fall", "--db", "/tmp/mytest.sqlite3")
        self.assertEqual(args.csv, Path("/tmp/mytest.csv"))
        self.assertEqual(self.args("--free", "--csv", "/tmp/fresh.csv").db,
                         recorder.DATA_DIR / "prototype_readings.sqlite3")
        with mock.patch.object(sys, "argv", ["record_activity.py", "--test", "--summary"]), \
             mock.patch.object(recorder, "record") as record, mock.patch.object(recorder, "show_summary") as summary:
            self.assertEqual(recorder.main(), 0)
        record.assert_not_called()
        summary.assert_called_once_with(recorder.DATA_DIR / "prototype_verdicts.sqlite3", "test")

    def test_free_cannot_add_measurement_tables_to_test_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db, csv_path = Path(directory)/"tests.sqlite3", Path(directory)/"tests.csv"
            recorder.VerdictRecorder(db, csv_path).close()
            before = db.read_bytes()
            with self.assertRaisesRegex(ValueError, "test-verdict database"):
                recorder.RecordingDatabase(db)
            self.assertEqual(db.read_bytes(), before)
            recorder.VerdictRecorder(db, csv_path).close()


class RecordingTests(unittest.TestCase):
    def run_recording(self, lines, mode="test", expected="normal", failure=None,
                      post_commit_error=False, interactive=False):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        clock = SimpleNamespace(now=100.0)
        console = Terminal() if interactive else io.StringIO()
        serial = mock.Mock()
        chunks = 0

        def read(timeout=1):
            nonlocal chunks
            chunks += 1
            if chunks == 1:
                clock.now += 0.1
                return b"".join(lines)
            if failure:
                clock.now += 40
                raise failure
            clock.now += timeout
            self.assertLess(chunks, 40, "Unbounded fake recording")
            return None

        serial.read.side_effect = read
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            args = SimpleNamespace(db=folder/"results.sqlite3", csv=folder/"results.csv", mode=mode,
                                   verdict=expected if mode == "test" else None, activity="fall-test-3",
                                   notes="Expected label is independent", port="mock", samples=None,
                                   duration=None if mode == "free" else 1)
            append = recorder.VerdictRecorder.append_sample

            def append_and_fail(store, *arguments):
                append(store, *arguments)
                raise OSError("after sample commit")

            with mock.patch.object(recorder, "prepare_console"), \
                 mock.patch.object(recorder, "serial_port", return_value="mock"), \
                 mock.patch.object(recorder, "SerialReader", return_value=serial), \
                 mock.patch.object(recorder.time, "monotonic", side_effect=lambda: clock.now), \
                 mock.patch.object(recorder.VerdictRecorder, "append_sample", new=append_and_fail if post_commit_error else append), \
                 mock.patch.object(sys, "stdout", console), mock.patch.object(sys, "stderr", io.StringIO()):
                if post_commit_error:
                    with self.assertRaisesRegex(OSError, "after sample commit"):
                        recorder.record(args)
                    result = 1
                else:
                    result = recorder.record(args)
            serial.close.assert_called_once()
            self.assertEqual(len(list(folder.glob("*.sqlite3"))), 1)
            with closing(sqlite3.connect(args.db)) as db:
                db.row_factory = sqlite3.Row
                if mode == "test":
                    samples = [dict(row) for row in db.execute("SELECT * FROM test_samples")]
                    run = dict(db.execute("SELECT * FROM runs").fetchone())
                    events = [dict(row) for row in db.execute("SELECT * FROM events")]
                    self.assertEqual(run["activity"], "fall-test-3-" + expected)
                    self.assertEqual(run["expected_verdict"], expected)
                    self.assertEqual(run["sample_count"], len(samples))
                else:
                    samples = [dict(row) for row in db.execute("SELECT * FROM readings")]
                    run = dict(db.execute("SELECT * FROM sessions").fetchone())
                    has_events = db.execute("SELECT 1 FROM sqlite_master WHERE name='detector_events'").fetchone()
                    events = [dict(row) for row in db.execute("SELECT * FROM detector_events")] if has_events else []
                self.assertIsNotNone(run["ended_at_utc"])
            with args.csv.open(newline="") as handle:
                exported = list(csv.DictReader(handle))
            if mode == "test":
                self.assertEqual(len(exported), 1)
                self.assertEqual(exported[0]["observed_verdict"], run["observed_verdict"])
                self.assertEqual(exported[0]["expected_verdict"], expected)
            else:
                self.assertEqual(len(exported), len(samples))
                self.assertEqual(set(exported[0]), set(recorder.CSV_FIELDS))
            return result, samples, run, events, console.getvalue(), clock.now

    def test_expected_and_actual_can_differ(self):
        result, samples, run, events, _, _ = self.run_recording([frame(1), SPIKE, frame(2), FALL], expected="near-fall")
        self.assertEqual((result, len(samples), len(events)), (0, 2, 2))
        self.assertEqual(run["observed_verdict"], "POSSIBLE_FALL")
        self.assertEqual(run["stop_reason"], "DURATION")

    def test_network_diagnostics_are_console_only_in_every_mode(self):
        diagnostics = (
            b"WIFI Product=ISM43362 FW=test-firmware API=test-api\r\n",
            b"ALERT Network=AP_FAILED Code=1\r\n",
            b"ALERT Network=TLS_VERIFIED Code=0\r\n",
            b"ALERT Network=DELIVERED Code=0\r\n",
        )
        # A network line between sensor lines must not discard a complete frame.
        interleaved = frame(1).replace(b"\nAccel", b"\n" + diagnostics[1] + b"Accel", 1)
        for mode in ("test", "free", "calibration"):
            with self.subTest(mode=mode):
                _, samples, run, events, output, _ = self.run_recording(
                    [diagnostics[0], interleaved, *diagnostics[2:], NORMAL, frame(2)],
                    mode=mode, failure=KeyboardInterrupt() if mode == "free" else None,
                    interactive=True,
                )
                self.assertEqual([row["sample_number"] for row in samples], [1, 2])
                self.assertEqual([row["event"] for row in events],
                                 [] if mode == "calibration" else ["STATUS"])
                for diagnostic in diagnostics:
                    text = diagnostic.decode("ascii").strip()
                    self.assertEqual(output.count(text), 1)
                    self.assertIn("\r\x1b[2K" + text + "\n", output)
                self.assertIn("reading no. = 2 State = NORMAL Alarm = 0", output)
                self.assertNotIn("Fall detected.", output)
                if mode == "test":
                    self.assertEqual(run["observed_verdict"], "NO_EVENT_OBSERVED")

    def test_gate_rejection_is_saved_and_displayed_without_a_near_fall(self):
        _, samples, run, events, output, _ = self.run_recording(
            [frame(1), SPIKE, frame(2), REJECTED, NORMAL], expected="near-fall", interactive=True,
        )
        self.assertEqual([row["sample_number"] for row in samples], [1, 2])
        self.assertEqual(run["observed_verdict"], "NO_EVENT_OBSERVED")
        self.assertEqual([row["event"] for row in events], ["SPIKE", "DISTURBANCE_REJECTED", "STATUS"])
        self.assertEqual([row["parse_valid"] for row in events], [1, 1, 1])
        self.assertIn("IncreaseRatio=1.000000e+00", events[1]["message"])
        self.assertEqual(output.count("Event=DISTURBANCE_REJECTED"), 1)
        self.assertIn("reading no. = 2 State = NORMAL Alarm = 0", output)
        self.assertNotIn("Fall detected.", output)

    def test_free_gate_confirmation_keeps_recording_until_actual_fall(self):
        _, samples, _, events, output, _ = self.run_recording(
            [frame(1), SPIKE, CONFIRMED, frame(2), FALL, frame(99)], mode="free",
        )
        self.assertEqual([row["sample_number"] for row in samples], [1, 2])
        self.assertEqual([row["event"] for row in events], ["SPIKE", "DISTURBANCE_CONFIRMED", "POSSIBLE_FALL"])
        self.assertEqual([row["parse_valid"] for row in events], [1, 1, 1])
        self.assertEqual(output.count("Event=DISTURBANCE_CONFIRMED"), 1)
        self.assertEqual(output.count("Fall detected."), 1)

    def test_low_disturbance_remains_incomplete_when_test_ends(self):
        _, _, run, events, output, _ = self.run_recording([frame(1), SPIKE, LOW])
        self.assertEqual(run["observed_verdict"], "OBSERVATION_INCOMPLETE")
        self.assertEqual(events[-1]["event"], "DISTURBANCE_LOW")
        self.assertEqual(events[-1]["parse_valid"], 1)
        self.assertIn("reading no. = 1 State = OBSERVING Alarm = 0", output)

    def test_continued_movement_is_saved_as_neutral_not_expected_label(self):
        _, samples, run, events, output, _ = self.run_recording(
            [frame(1), SPIKE, LOW, frame(2), CONTINUED], expected="fall",
        )
        self.assertEqual([row["sample_number"] for row in samples], [1, 2])
        self.assertEqual((run["observed_verdict"], run["verdict_source"]),
                         ("MOVEMENT_CONTINUED", "FIRMWARE_EVENT"))
        self.assertEqual([row["event"] for row in events],
                         ["SPIKE", "DISTURBANCE_LOW", "MOVEMENT_CONTINUED"])
        self.assertEqual([row["parse_valid"] for row in events], [1, 1, 1])
        self.assertIn("reading no. = 2 State = NORMAL Alarm = 0", output)
        self.assertNotIn("Fall detected.", output)

    def test_free_continues_after_low_disturbance_and_neutral_movement(self):
        _, samples, _, events, output, clock = self.run_recording(
            [frame(1), SPIKE, LOW, CONTINUED, frame(2)], mode="free", failure=KeyboardInterrupt(),
        )
        self.assertEqual([row["sample_number"] for row in samples], [1, 2])
        self.assertEqual(events[-1]["event"], "MOVEMENT_CONTINUED")
        self.assertGreater(clock, 130)
        self.assertIn("Stopping recording.", output)
        self.assertNotIn("Fall detected.", output)

    def test_free_stops_on_fall_after_low_disturbance(self):
        _, samples, _, events, output, _ = self.run_recording(
            [frame(1), SPIKE, LOW, frame(2), FALL, frame(99)], mode="free",
        )
        self.assertEqual([row["sample_number"] for row in samples], [1, 2])
        self.assertEqual([row["event"] for row in events],
                         ["SPIKE", "DISTURBANCE_LOW", "POSSIBLE_FALL"])
        self.assertEqual([row["parse_valid"] for row in events], [1, 1, 1])
        self.assertEqual(output.count("Fall detected."), 1)

    def test_unknown_gate_evidence_is_not_erased_by_returning_to_ready(self):
        _, _, run, events, output, _ = self.run_recording([frame(1), SPIKE, UNKNOWN, READY])
        self.assertEqual(run["observed_verdict"], "OBSERVATION_INCOMPLETE")
        self.assertEqual([row["parse_valid"] for row in events], [1, 1, 1])
        self.assertIn("reading no. = 1 State = WARMUP Alarm = 0", output)
        self.assertIn("reading no. = 1 State = NORMAL Alarm = 0", output)

    def test_free_saves_final_sample_and_stops_on_fall(self):
        result, samples, _, events, output, _ = self.run_recording([frame(1), NORMAL, SPIKE, frame(2), FALL, frame(99)], mode="free")
        self.assertEqual(result, 0)
        self.assertEqual([row["sample_number"] for row in samples], [1, 2])
        self.assertEqual(events[-1]["event"], "POSSIBLE_FALL")
        self.assertIn("Fall detected.", output)
        self.assertIn("reading no. = 2 State = FALL_LATCHED Alarm = 1", output)
        self.assertEqual(output.count("Fall detected."), 1)

    def test_free_continues_past_thirty_seconds_after_near_fall(self):
        _, _, _, events, output, clock = self.run_recording([frame(1), NORMAL, SPIKE, NEAR], mode="free", failure=KeyboardInterrupt())
        self.assertGreater(clock, 130)
        self.assertNotIn("Fall detected", output)
        self.assertEqual(events[-1]["event"], "NEAR_FALL")

    def test_free_rejects_malformed_fall(self):
        bad = b"DETECTOR TimeMs=123 State=NORMAL Alarm=1 Sensors=OK Event=POSSIBLE_FALL bad\n"
        _, samples, _, events, _, _ = self.run_recording([frame(1), bad, frame(2), FALL], mode="free")
        self.assertEqual(len(samples), 2)
        self.assertEqual(events[0]["parse_valid"], 0)

    def test_free_existing_alarm_is_identified(self):
        status = b"DETECTOR TimeMs=9000 State=FALL_LATCHED Alarm=1 Sensors=OK Event=STATUS \n"
        _, _, _, events, output, _ = self.run_recording([frame(1), status], mode="free")
        self.assertIn("existing latched alarm", output)
        self.assertEqual(events[-1]["event"], "STATUS")

    def test_free_existing_long_lie_alarm_stops_with_last_sample_saved(self):
        status = b"DETECTOR TimeMs=39000 State=LONG_LIE Alarm=1 Sensors=OK Event=STATUS \n"
        _, samples, _, events, output, _ = self.run_recording(
            [frame(1), status, frame(99)], mode="free",
        )
        self.assertEqual([row["sample_number"] for row in samples], [1])
        self.assertEqual((events[-1]["state"], events[-1]["parse_valid"]), ("LONG_LIE", 1))
        self.assertIn("existing latched alarm", output)
        self.assertIn("reading no. = 1 State = LONG_LIE Alarm = 1", output)

    def test_test_mode_saves_long_lie_and_manual_reset_without_erasing_fall(self):
        long_lie = b"DETECTOR TimeMs=39000 State=LONG_LIE Alarm=1 Sensors=OK Event=LONG_LIE still quiet\n"
        reset = b"DETECTOR TimeMs=42000 State=WARMUP Alarm=0 Sensors=OK Event=MANUAL_RESET collecting baseline\n"
        _, samples, run, events, output, _ = self.run_recording(
            [frame(1), FALL, long_lie, reset, frame(2)], expected="fall",
        )
        self.assertEqual([row["sample_number"] for row in samples], [1, 2])
        self.assertEqual(run["observed_verdict"], "POSSIBLE_FALL")
        self.assertEqual((run["last_state"], run["last_alarm"]), ("WARMUP", 0))
        self.assertEqual([row["event"] for row in events], ["POSSIBLE_FALL", "LONG_LIE", "MANUAL_RESET"])
        self.assertEqual([row["parse_valid"] for row in events], [1, 1, 1])
        self.assertIn("reading no. = 2 State = WARMUP Alarm = 0", output)

    def test_disconnection_keeps_test_evidence(self):
        result, _, run, _, _, _ = self.run_recording([frame(1), SPIKE], failure=OSError("unplugged"))
        self.assertEqual(result, 1)
        self.assertEqual((run["observed_verdict"], run["stop_reason"]), ("OBSERVATION_INCOMPLETE", "DISCONNECTED"))

    def test_no_diagnostics_is_not_the_expected_verdict(self):
        _, _, run, events, _, _ = self.run_recording([frame(1)], expected="fall")
        self.assertEqual(run["observed_verdict"], "NO_DETECTOR_DATA")
        self.assertEqual(events, [])

    def test_calibration_keeps_only_measurements(self):
        result, samples, _, events, _, _ = self.run_recording([frame(1), FALL], mode="calibration")
        self.assertEqual((result, len(samples), len(events)), (0, 1, 0))

    def test_durable_count_survives_error_after_sample_commit(self):
        _, samples, run, _, output, _ = self.run_recording([frame(1)], post_commit_error=True,
                                                       interactive=True)
        self.assertEqual(len(samples), 1)
        self.assertEqual((run["sample_count"], run["stop_reason"]), (1, "ERROR"))
        self.assertIn("reading no. = 1 State = UNKNOWN Alarm = UNKNOWN\nSession 1 ended.", output)

    def test_progress_uses_saved_count_not_board_counter_and_still_saves_status(self):
        _, samples, run, events, output, _ = self.run_recording(
            [NORMAL, NORMAL, frame(400), NORMAL, frame(401)], interactive=True
        )
        self.assertEqual([row["sample_number"] for row in samples], [400, 401])
        self.assertEqual(run["sample_count"], 2)
        self.assertEqual([row["event"] for row in events], ["STATUS"] * 3)
        self.assertIn("reading no. = 0 State = UNKNOWN Alarm = UNKNOWN", output)
        self.assertIn("reading no. = 2 State = NORMAL Alarm = 0", output)
        self.assertNotIn("reading no. = 400", output)
        self.assertNotIn("Event=STATUS", output)
        self.assertNotIn("Accel X=", output)
        self.assertNotIn("Saved 1:", output)
        self.assertIn("\r\x1b[2K", output)

    def test_diagnostic_only_updates_count_zero_and_ignores_invalid_state(self):
        invalid = b"DETECTOR TimeMs=123 State=NORMAL Alarm=1 Sensors=OK Event=POSSIBLE_FALL bad\n"
        fault = b"DETECTOR TimeMs=124 State=SENSOR_FAULT Alarm=0 Sensors=FAULT Event=SENSOR_FAULT invalid data\n"
        _, samples, _, events, output, _ = self.run_recording([NORMAL, invalid, fault])
        self.assertEqual(samples, [])
        self.assertEqual([row["parse_valid"] for row in events], [1, 0, 1])
        self.assertIn("reading no. = 0 State = NORMAL Alarm = 0", output)
        self.assertIn("reading no. = 0 State = SENSOR_FAULT Alarm = 0", output)
        self.assertNotIn("State = NORMAL Alarm = 1", output)
        self.assertEqual(output.count("Event=SENSOR_FAULT"), 1)
        self.assertNotIn("\r", output)
        self.assertNotIn("\x1b", output)

    def test_free_terminal_final_alarm_is_visible_without_an_extra_sample(self):
        _, samples, _, events, output, _ = self.run_recording(
            [frame(100), NORMAL, FALL, frame(101)], mode="free", interactive=True,
        )
        self.assertEqual(len(samples), 1)
        self.assertEqual(events[-1]["event"], "POSSIBLE_FALL")
        self.assertIn("reading no. = 1 State = FALL_LATCHED Alarm = 1\nSession 1 ended.", output)
        self.assertIn("\r\x1b[2KFall detected. Saving data and stopping the free run.\n", output)

    def test_interrupt_finishes_terminal_line_before_session_summary(self):
        _, samples, _, events, output, _ = self.run_recording(
            [frame(20), NORMAL, NEAR], mode="free", failure=KeyboardInterrupt(), interactive=True,
        )
        self.assertEqual(len(samples), 1)
        self.assertEqual(events[-1]["event"], "NEAR_FALL")
        self.assertEqual(output.count("Event=NEAR_FALL"), 1)
        self.assertIn("\r\x1b[2KStopping recording.\n", output)
        self.assertIn("reading no. = 1 State = NORMAL Alarm = 0\nSession 1 ended.", output)


if __name__ == "__main__":
    unittest.main()
