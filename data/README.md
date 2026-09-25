# Sensor recordings

Choose **exactly one recording mode**: `--test`, `--free`, or `--calibration`.
There is no default mode, and the old `--dataset` option is no longer used.

| Mode | What is saved | Default files in this folder |
|---|---|---|
| `--test` with `--verdict` | Expected label, actual detector results, every sensor sample and diagnostic event | `prototype_verdicts.sqlite3`; `prototype_verdicts.csv` contains one summary row per test |
| `--free` | Sensor measurements and diagnostic history until a fall is reported, without an expected verdict | `prototype_readings.sqlite3` and the 22-column `prototype_readings.csv` |
| `--calibration` | Sensor measurements for a 30-second calibration recording | `calibration_readings.sqlite3` and the 22-column `calibration_readings.csv` |

**Test runs do not write to `prototype_readings` files.** All their data is in
`prototype_verdicts.sqlite3`: summaries in `runs`, telemetry in `test_samples`
(and its `test_readings` view), and detector messages in `events`. Their CSV is
a summary, not a copy of every sensor reading. SQLite is the authoritative copy.

**Earlier recordings are retained.** Calibration sessions **1–35 contain 10,465
readings**. The **601 readings from old test sessions 36–37** were moved into the
combined test database, preserving session IDs and labels. Original reading IDs
are retained in `legacy_record_id`. Their expected verdict is empty and their
observed verdict is `NOT_RECORDED`: the old recorder did not
capture that information, so neither is inferred from a name or replay. Existing
files were backed up before reorganizing them in the
[test-storage backup](backups/test-storage-20260925T075004Z-myaywirn/).
The previous mixed dataset is also preserved in the
[recording-split backup](backups/recording-split-20260925T071407Z-5iiwc38v/).

## Start and name a recording

1. Run/resume the existing Prototype 1 firmware on the board in CubeIDE. It
   prints `Magnitude`, `MagnitudeSlope` and `DETECTOR` messages. This recorder
   update changes Python only; no additional firmware upload is needed if
   Prototype 1 is already installed. Reset the board between tests to clear a
   previous latched alarm.
2. Close `screen` or any other program using the board's serial port.
3. From the repository root (`cg2028-Assignment`), choose a command below.

For a labelled test:

```bash
python3 tools/record_activity.py --test --name fall-test-3 --verdict near-fall \
  --notes "Trial 3: board moved by hand; sudden movement then recovery"
```

This stores `activity=fall-test-3-near-fall` and `expected_verdict=near-fall`.
`--verdict` means **your expected outcome**, not the program's answer. The board's
answer is saved separately as `observed_verdict`. Neither the name nor the
expected verdict is sent to the firmware or changes its thresholds.

A new test requires `--verdict fall`, `--verdict near-fall`, or `--verdict normal`.
The name is optional: without it, the recorder uses
`test-<session_id>-<expected_verdict>`. `--activity` remains an alias for `--name`.
Test recordings stop after **30 seconds** by default; `--duration 60` requests a
one-minute test. **Detecting a fall does not stop a test early.** If a fall is
reported 12 seconds into a default test, its result is saved and recording
continues for the remaining 18 seconds. The summary is finalized when the run
ends. An optional `--samples` limit stops a test at that sample count or its time
limit, whichever comes first.

For free recording without an expected outcome:

```bash
python3 tools/record_activity.py --free --name normal-walking \
  --notes "Exploring ordinary hand movements"
```

The default name is `free`. This mode **runs until the firmware reports a fall**;
there is no 30-second timer. An explicit `POSSIBLE_FALL` event or a valid
`FALL_LATCHED` status with `Alarm=1` prints **Fall detected** and stops Python.
Normal, near-fall and uncertain results continue recording. Detector events
remain visible in Terminal; routine status messages update the live counter.
All received detector messages are saved in the measurement database's
`detector_events` table; the CSV still contains only its 22 measurement columns.

If the board already has a latched alarm when recording begins, free mode stops
and identifies that existing alarm. Reset the board before a fresh run. Python
stopping does not reset or stop the STM32. `--free` rejects `--duration` and
`--samples`; Ctrl+C or disconnection can also end a run.

For calibration:

```bash
python3 tools/record_activity.py --calibration --name walking-slowly \
  --notes "Calibration: consistent hand movement"
```

This saves `walking-slowly` in the **`activity` column** and
`Calibration: consistent hand movement` in the **`notes` column** of the
calibration CSV and database. `--activity walking-slowly` is equivalent to
`--name walking-slowly`; use either flag. Notes are optional and can describe
the trial number, board placement and movement you simulated.

The name labels the recording, not its filename. Each run appends a new session
to `calibration_readings.csv` and `calibration_readings.sqlite3`, with an
automatically assigned session ID. Reusing an activity name does not overwrite
earlier trials.

The default name is `calibration`. Calibration has a **30-second limit**;
`--duration 30` is accepted, but another duration or `--samples` is rejected.
It continues to that limit even if the firmware detects a fall. Only `--free`
automatically stops when a fall is reported.
Calibration and free recording do not accept `--verdict`. Mixed modes, a missing
mode, and a missing required test verdict are rejected before opening the serial
port.

A timed test/calibration run includes time waiting for readings, so resume the
board first.
Each complete sample is saved as it arrives; a partial sample at the deadline
is discarded. **Ctrl+C stops early** and retains the data already saved. The
STM32 continues running after recording stops. Restarting Python does not reset
its filter or detector. No extra Python packages or separate Conda environment
are needed on macOS/Linux.

During recording, the Terminal updates a single line:

```text
reading no. = 42 State = NORMAL Alarm = 0
```

The number counts complete saved readings in this run, starting from zero.
State and alarm reflect the latest valid firmware message and initially show
`UNKNOWN`. Spikes, fall/near-fall decisions, faults and warnings still appear
separately. Sensor values are saved in full even though they no longer scroll
past in the console. Test/free databases also retain their detector diagnostics.
When console output is redirected, updates are plain lines instead of in-place
redraws.

Each recording appends to the files for its mode. To use custom filenames,
`--db` and `--csv` select that mode's pair directly:

```bash
python3 tools/record_activity.py --test --verdict normal --name control \
  --db data/control_tests.sqlite3 --csv data/control_tests.csv
```

This creates a combined test database and its summary CSV. There is no extra
readings pair for that test. With `--free` or `--calibration`, the same options
choose a measurement database and measurement CSV instead. The old
`--verdict-db` and `--verdict-csv` options are no longer used.

## Reading the prototype verdicts

Open `prototype_verdicts.csv` to compare **`expected_verdict` with
`observed_verdict`**. Each test has one summary row. A session can contain
multiple detector events, so the summary is not necessarily a single final LED
state. Detailed messages are retained in the `events` table of
`prototype_verdicts.sqlite3`; the `runs` table contains session summaries.

For quick review, the verdict CSV places `expected_verdict` beside your activity
name and `observed_verdict`. The full `source_database` path is its final column.
Start with these fields:

| Column | Meaning |
|---|---|
| `session_id`, `activity`, `notes` | The reading session and your intended-activity description. |
| `expected_verdict` | Your `--verdict` choice: `fall`, `near-fall` or `normal`; empty for historical tests whose expected result was not recorded. |
| `observed_verdict` | The result summarized from the diagnostics actually received; see the table below. |
| `verdict_source` | `FIRMWARE_EVENT` for explicit decisions, `FIRMWARE_STATUS` for observed alarm state, `FIRMWARE_EVENT_AND_STATUS` when both are needed, `FIRMWARE_DIAGNOSTICS` for other usable status, or `NONE`/`NOT_RECORDED`. |
| `recording_status` | `OPEN` while active or left unfinished; `FINISHED` after the recorder saves its end metadata. |
| `sample_count`, `started_at_utc`, `ended_at_utc`, `stop_reason` | Saved reading count and recording boundaries; count/end fields are finalized when the run closes. |
| `spike_count`, `possible_fall_count`, `near_fall_count`, `uncertain_count` | Counts of explicit firmware events, not counts of repeated status messages. |
| `sensor_health`, `fault_diagnostic_count` | Observed faults remain visible even when the run has a fall or near-fall verdict. |
| `diagnostic_coverage`, `invalid_diagnostic_count` | Whether diagnostics were received and whether any failed parsing; `OBSERVED` does not guarantee that every message was captured. |
| `last_state`, `last_alarm`, `last_sensors` | Last usable board status; this can differ from an earlier decision summarized for the run. |
| `historical`, `run_id`, `source_database` | Whether this is an imported earlier session, its verdict-record ID, and the source readings database. |

The CSV also retains diagnostic timestamps, first board state/tick, last board
tick and other event counts. `events` and `test_samples` link to `runs` by
`run_id`. The events retain original diagnostic lines, parsed values and parsing
errors. The `test_readings` view includes the familiar 22 measurement fields
plus `run_id`, `legacy_record_id`, `expected_verdict` and `port`.

| Saved verdict | Meaning |
|---|---|
| `POSSIBLE_FALL` | The recorder received an explicit possible-fall event during this recording. |
| `NEAR_FALL` | It received an explicit near-fall event during this recording. |
| `NEAR_FALL_WITH_LATCHED_ALARM` | It received a near-fall event and also observed a latched alarm, without capturing an explicit possible-fall event. Inspect the timeline; this is not a plain near-fall result. |
| `MIXED_DECISIONS` | It received both kinds of event in the same recording; inspect the detailed events. |
| `PREEXISTING_LATCHED_ALARM` | The first usable detector message already reported a latched alarm, without an explicit new fall event. Reset the board before a fresh trial. |
| `LATCHED_ALARM_OBSERVED` | A later status reported a latched alarm, but the explicit event was not captured. The alarm's onset is not established by the recording. |
| `SENSOR_FAULT` | The latest detector status reports a sensor fault, with no explicit fall/near-fall decision recorded. |
| `OBSERVATION_INCOMPLETE` | A triggered observation did not produce a recorded decision before the run ended, or its outcome was interrupted. |
| `UNCERTAIN` | The detector was still uncertain when recording ended. |
| `WARMUP_INCOMPLETE` | Recording ended while the detector was warming up. |
| `NO_EVENT_OBSERVED` | Healthy normal-monitoring status was observed without a detected event; this does not prove that the activity was normal. |
| `NO_DETECTOR_DATA` | No usable detector diagnostics were received. This is not a normal result. |
| `NOT_RECORDED` | Historical measurements exist, but their live detector output was not saved. |

An explicit `POSSIBLE_FALL` or `NEAR_FALL` takes priority in the summary, and
both together produce `MIXED_DECISIONS`. A near-fall event combined with a
latched-alarm status but no explicit possible-fall event produces
`NEAR_FALL_WITH_LATCHED_ALARM`, with source `FIRMWARE_EVENT_AND_STATUS`.
Faults remain recorded separately even if a decision was captured. The detailed
timeline is available when a single summary cannot explain everything that happened.

Observed verdicts come from received firmware messages, **not from your expected
verdict, activity label or an offline replay of the readings**. A periodic latched-alarm status is
distinguished from an explicit new fall event. The detector runs independently
of the Python session, so reset the board between trials and start recording
before the intended event.

The verdict SQLite database is authoritative. Its CSV is regenerated from the
saved summaries, rather than appending repeated copies of a session. A run is
marked `OPEN` while recording; an interrupted process that cannot perform its
normal shutdown can leave that marker behind. Treat such a row as an unfinished
recording, not an automatically completed test. Before the first usable detector
message, a new run has `NO_DETECTOR_DATA`; its verdict updates as messages arrive.

## Which columns contain the current rates of change?

The free/calibration readings CSVs each have **22 columns**. The same
measurement fields are available for tests through `test_readings` in the test
database. **The four `*_slope` fields contain the current rates of change.**

| CSV column | Meaning | Unit |
|---|---|---|
| `accel_magnitude_slope` | Recent rate of change of acceleration magnitude | (m/s²)/s |
| `accel_msd_slope` | Recent rate of change of accelerometer MSD | (m/s²)²/s |
| `gyro_magnitude_slope` | Recent rate of change of angular velocity magnitude | (degrees/s)/s |
| `gyro_msd_slope` | Recent rate of change of gyroscope MSD | (degrees/s)²/s |

The four empty legacy columns (`accel_avg_rate`, `accel_msd_rate`,
`gyro_avg_rate`, and `gyro_msd_rate`) have been **removed from the saved CSV and
database format**. The logger no longer creates these columns. Removing these
empty columns from an older dataset preserves its other measurements and labels.

The board calculates the slopes and prints `MagnitudeSlope` and `MSDSlope` for
each sensor. The Python logger copies those values into the corresponding CSV
and SQLite fields automatically; it does not calculate them later in Excel.

When opening an older recording, the logger automatically removes the retired
columns if they contain no values. If an older database contains actual values
in those columns, it stops to preserve them. Use a different pair of filenames
for new recordings and retain that older dataset separately.

## How the current measurements work

- **X, Y, Z:** EWMA-filtered sensor readings. Acceleration is in m/s²; gyroscope
  readings are in degrees/second.
- **Magnitude:** `sqrt(X² + Y² + Z²)` for one sample, saved as
  `accel_magnitude_mps2` and `gyro_magnitude_dps`. It replaces the arithmetic
  mean across axes. Magnitude is nonnegative and opposite directions do not
  cancel. Its units remain m/s² for acceleration and degrees/second for rotation.
- **MSD:** `((X - previous_X)² + (Y - previous_Y)² + (Z - previous_Z)²) / 3`.
  This compares the current sample with the previous one. It is nonnegative.
- **MagnitudeSlope and MSDSlope:** slopes of straight lines fitted through the
  latest five magnitude values and the latest five valid MSD values respectively, using
  actual board timestamps. Positive means increasing; negative means decreasing.
- **`slope_window_samples`:** `5` for the current firmware. Five readings at
  100 ms spacing span **400 ms** from the first to the last. The window advances
  with each new reading; the slopes do not accumulate from startup.

The board still targets **100 ms per sample**, approximately 10 samples/second,
and uses **115200 baud**. This update does not enable 50 Hz sampling.
A fully running 30-second recording therefore produces roughly **300 rows**;
the actual number depends on timing and complete readings received.

At board startup, MSD is unavailable on the first reading. MagnitudeSlope becomes
available after five readings; MSDSlope becomes available after six because
the first MSD requires two readings. Unavailable values appear as `NA` on the
terminal, NULL in SQLite, and empty cells in the CSV. Those initial empty rows
remain empty; subsequent readings contain the computed values. A window with
identical timestamps also produces an unavailable slope.

Opening a new Python session does not reset the board's EWMA state or rolling
windows. If the board has already been running, the first saved row may already
have all four slopes. Restarting the board clears that history.

All these metrics use EWMA-filtered readings (25% new input and 75% previous
filtered output, with integer truncation). Magnitude is calculated from those
filtered components; it does not apply an additional filter. Once the filter
has settled, stationary acceleration magnitude should be near **9.81 m/s²**
(gravity), and gyroscope magnitude near **0 degrees/second**, with sensor noise
and bias. Smoothing affects slopes and peaks, and can reduce magnitude during
rapid direction changes.
A symmetric rise and fall can have a near-zero fitted slope despite substantial
movement, so inspect magnitude, MSD and XYZ alongside the slopes. Magnitude
alone is not a fall detector; ordinary handling can also produce large readings.

## If you open an older Avg-format dataset later

The following applies if you open an older measurement dataset in free or
calibration mode. Current recordings already use Magnitude.

The logger automatically upgrades that database and matching CSV. Before
conversion it creates timestamped `before-magnitude` backups in a `backups`
folder beside the files, preserving their original Avg measurements.

The upgraded files replace `accel_avg_mps2` and `gyro_avg_dps` with
`accel_magnitude_mps2` and `gyro_magnitude_dps`, calculated from each old row's
saved XYZ. The two `*_avg_slope` fields are replaced by `*_magnitude_slope`
fields, recalculated from those magnitudes using the saved board timestamps and
window sizes. **An old average slope is never reused as a magnitude slope.**

Historical calculations cannot recover readings from before a saved session.
For a five-reading window, the **first four historical magnitude slopes are
empty** in each session and after a sample-counter gap or board reset. Missing
timing/history also leaves slopes empty. These blanks mean insufficient saved
history, not zero movement. Subsequent complete windows contain slopes. New
live recordings may have slopes from their first saved row if the board has
already filled its windows.

The upgrade preserves XYZ, MSD, MSD slopes, IDs, activities and notes. The CSV
still has **22 columns**. Historical magnitudes and their slopes use XYZ rounded
to three decimal places, so they may differ slightly from calculations the new
firmware would have made before display rounding.

## Identifying sessions and timestamps

| Field | Meaning |
|---|---|
| `record_id` | Unique saved row ID |
| `session_id` | Recording run; a new one is assigned each time the logger starts |
| `activity`, `notes` | Labels supplied in the command |
| `timestamp_utc` | Time the Mac received the complete sample, in UTC |
| `sample_number` | Board sample counter; a new Python run does not reset it |
| `board_time_ms` | Board tick at acquisition start, in milliseconds; resets on board restart and wraps after about 49.7 days |

IDs can have gaps after deleted trials or moving datasets; they are not a count
of remaining rows. In the combined test database, `run_id` joins a summary to
its sensor samples and diagnostic events. Imported records retain their original
source/session/start-time metadata for provenance, and original reading IDs in
`legacy_record_id`.
The board's timestamps are used for the slopes, rather than the Mac's receipt
time. XYZ/Magnitude are printed to three decimal places; the board computes
metrics before that display rounding, so recalculating from CSV XYZ can differ
slightly.

## Inspect and retain recordings

Show test summaries without opening the serial port; a summary command does not
require an expected verdict:

```bash
python3 tools/record_activity.py --test --summary
```

Show calibration sessions instead:

```bash
python3 tools/record_activity.py --calibration --summary
```

Show free-recording sessions:

```bash
python3 tools/record_activity.py --free --summary
```

Show prototype verdict summaries:

```bash
sqlite3 -header -column data/prototype_verdicts.sqlite3 'SELECT session_id, activity, expected_verdict, observed_verdict, recording_status FROM runs ORDER BY run_id;'
```

Inspect the last five sensor readings saved during tests:

```bash
sqlite3 -header -column data/prototype_verdicts.sqlite3 'SELECT * FROM test_readings ORDER BY record_id DESC LIMIT 5;'
```

Open the CSV in Excel after recording. Keep the logger's CSV consistent with
its database: do not overwrite it with spreadsheet edits. Save edited analysis
as a separate file. The logger
can recreate a missing CSV from SQLite and recover missing tail rows.

Measurement files are ignored by Git by default; this README can be shared
with your teammate. See the [full logger guide](../tools/README.md) for more
commands, serial connection details, and verification instructions.
