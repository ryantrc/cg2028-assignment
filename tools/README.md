# Record tests, free activity and calibration

`record_activity.py` runs on your Mac and saves UART readings without additional
Python packages. macOS or Linux is required for serial recording. **Choose
exactly one mode:** `--test`, `--free`, or `--calibration`. There is no default
recording mode; the old `--dataset` option has been removed.

**Prototype 2 requires rebuilding and flashing `CG2028_Assignment` in CubeIDE.**
It adds a comparison with recent baseline activity before a spike enters the
fall/near-fall observation. Sampling remains **100 ms**, baud remains **115200**,
and the existing **25% EWMA**, Magnitude/MSD calculations, recording modes and
file destinations are unchanged. Old `Avg` serial output is still rejected
explicitly.

The [Prototype 2 guide](../docs/prototype-2.md) explains the detector, LED behavior
and board-reset procedure. The recording mode affects where data is stored and
what labels you must provide; it does not reconfigure the detector.

## Start a recording

1. Build and flash `CG2028_Assignment` with Prototype 2, and click Resume in
   CubeIDE if execution is paused at `main()`. Reset the board between tests so
   a previous latched fall alarm does not carry into the next recording.
2. Close `screen` or another serial viewer. For a named screen session, use
   `screen -S stm32 -X quit`; otherwise use `screen -ls` and
   `screen -S SESSION_ID -X quit` from another Terminal tab.
3. From the repository root, choose a command:

   ```bash
   python3 tools/record_activity.py --test --name fall-test-3 --verdict near-fall
   python3 tools/record_activity.py --free --name normal-walking
   python3 tools/record_activity.py --calibration --name walking-slowly \
     --notes "Calibration: consistent hand movement"
   ```

Before the intended event, wait for `READY` (`State=NORMAL`, `Alarm=0`). Startup
requires roughly five seconds of valid data: two seconds for filter settling,
then three seconds of baseline activity. The recorder can save readings during
warmup, and that time counts toward a timed run. Use `--duration 60` for a test
that needs more preparation or observation time.

The first command records a test named `fall-test-3-near-fall`, with
`expected_verdict=near-fall`. The actual firmware verdict is stored separately.
Your expected label never forces a result, and neither label nor name is sent
to the board. `--test` requires one of `--verdict fall`, `--verdict near-fall`,
or `--verdict normal`. Without `--name`, a test is named
`test-<session_id>-<expected_verdict>`. `--activity` is an alias for `--name`.
The default free name is `free`; the default calibration name is `calibration`.

For calibration, `--name walking-slowly` saves `walking-slowly` in the
`activity` column, and `--notes "Calibration: consistent hand movement"`
saves that description in the `notes` column. You can use
`--activity walking-slowly` instead of `--name walking-slowly`; they mean the
same thing. The name labels the recording; it does not create a new filename.
Each run appends a new session with an automatically assigned session ID, so
you can repeat the same activity name and distinguish trials by session and notes.

The logger detects the USB serial device and uses 115200 baud, 8 data bits,
no parity, 1 stop bit and no flow control. If several devices are connected,
choose `--port /dev/cu.usbmodemXXXX` using the current device name from
`ls /dev/cu.usbmodem*`.

| Mode | Automatic stopping condition | If a fall is detected early |
|---|---|---|
| `--test` | 30 seconds by default; `--duration` changes the limit | Keeps recording until the limit |
| `--calibration` | 30 seconds | Keeps recording until the limit |
| `--free` | When a fall is reported; otherwise no time limit | Prints **Fall detected** and stops Python |

Tests default to **30 seconds** and accept `--duration 60` for a longer test.
**A fall does not end a test early.** For example, a fall reported 12 seconds
into a default test is saved, and recording continues for the remaining
18 seconds. The test summary is finalized when recording stops.
Calibration is fixed at **30 seconds**: `--duration 30` is accepted, other
values and `--samples` are rejected. Tests may use `--samples` to stop at a
sample count or their time limit, whichever happens first.

**Free mode runs until a fall is reported**, without a time limit. An explicit
`POSSIBLE_FALL` event or a valid `FALL_LATCHED` status with `Alarm=1` prints
**Fall detected** and stops Python; normal, near-fall and uncertain messages do
not stop it. An alarm already latched when the logger connects is identified
separately, and the run stops so you can reset the board. The STM32 continues
running. Free mode rejects `--duration` and `--samples`.

`--verdict` is not accepted outside test mode. Missing or mixed modes and missing
required test verdicts are rejected before serial access.

For a timed test/calibration run, the timer starts when recording opens,
including time waiting for the board. Resume the board first. Complete samples
are saved as they arrive; partial
samples at a deadline are discarded. **Ctrl+C stops early** and keeps saved
readings. The board continues running afterward. The logger repairs Ctrl+C and
newline handling on its interactive Terminal; redirected streams are left alone.

The board and logger must both be running to save measurements on the Mac.
When a connection is lost, saved data is retained; reconnect and start a new run.

### Live counter

The Terminal updates one line in place while recording:

```text
reading no. = 42 State = NORMAL Alarm = 0
```

The counter starts at zero for each run and counts complete saved sensor samples,
not the board's lifetime sample number. State and alarm show the latest valid
firmware diagnostic; both show `UNKNOWN` until the first one arrives. `Alarm = 1`
means the board's fall alarm is latched. Reset the board before a new trial and
wait for `READY` / `State = NORMAL Alarm = 0` before the intended event.

Routine `STATUS` messages update this line instead of scrolling the Terminal.
Detector events such as spikes, fall/near-fall decisions and sensor faults, plus
warnings, still print separately. Full sensor values continue to be saved, and
test/free databases retain their received diagnostics. Redirected console output
uses plain lines without terminal control codes. This display change needs no
firmware upload and does not change recording duration or detector behavior.

## Files created and retained

| Mode | Database | CSV |
|---|---|---|
| Test | `data/prototype_verdicts.sqlite3`: expected label, results, samples and detector events | `data/prototype_verdicts.csv`: one summary row per test |
| Free | `data/prototype_readings.sqlite3`: measurements and diagnostic history | `data/prototype_readings.csv`: one row per sample, 22 columns |
| Calibration | `data/calibration_readings.sqlite3`: measurements | `data/calibration_readings.csv`: one row per sample, 22 columns |

**A test writes only to its test pair.** In the combined test database, `runs`
contains summaries, `test_samples` contains telemetry, `test_readings` exposes
the readings for queries, and `events` retains received `DETECTOR` messages.
The test CSV contains summary rows rather than every sensor sample.

Earlier calibration sessions 1–35 retain their 10,465 readings. The 601 readings
from earlier test sessions 36–37 were moved to the combined test database with
session IDs and labels preserved; original reading IDs are kept in
`legacy_record_id`. Their expected label remains empty and their observed
verdict remains `NOT_RECORDED`, because those facts were not originally saved.
The existing files were backed up before migration. Free recording starts with
its own measurement files, without those old test rows.

Free recordings display detector events and retain all received `DETECTOR`
messages in their measurement database's `detector_events` table. Routine
statuses appear through the live counter. They have no expected verdict or separate
test-summary files. Calibration keeps measurements only.

`--db` and `--csv` override the selected mode's pair directly. For tests they
select the combined test database and summary CSV; for free/calibration they
select the measurement database and CSV. The old separate `--verdict-db` and
`--verdict-csv` controls have been removed. Names and expected labels never
select output destinations; the explicit mode does.

SQLite is authoritative. Free/calibration CSVs are flushed after each sample;
a missing CSV or missing tail can be recovered from the database. Test summaries
are exported atomically from their database. If a CSV belongs to another dataset
or contains incompatible edits, the logger stops rather than mixing records.
Open CSVs in Excel after recording and save your analysis as a separate file.

### Test verdicts

Compare **`expected_verdict` with `observed_verdict`** in the test CSV, and check
`recording_status`, `sensor_health` and `diagnostic_coverage` before interpreting
an outcome. A new test starts with no observed detector result; its summary
updates from the messages received, independently of your expected label.

An explicit `POSSIBLE_FALL` or `NEAR_FALL` is recorded, both together produce
`MIXED_DECISIONS`, and an unconfirmed latched-alarm status is distinguished from a
new event. Incomplete observation, uncertainty and sensor faults are retained.
In Prototype 2, `SPIKE` is provisional. `DISTURBANCE_REJECTED` means the
baseline comparison did not qualify the candidate; it is not a near-fall.
`DISTURBANCE_UNKNOWN` means the comparison lacked usable evidence, and the
detector returns to warmup. An unknown candidate remains
`OBSERVATION_INCOMPLETE` in the saved summary even after a later `READY`, unless
an explicit decision takes precedence. A rejected candidate can leave
`NO_EVENT_OBSERVED`; that is not proof of ordinary activity. See the
[gate and timing explanation](../docs/prototype-2.md#the-baseline-comparison).
See the [verdict fields and definitions](../data/README.md#reading-the-prototype-verdicts).

`run_id` links each test's `runs`, `test_samples` and `events` rows in the same
database. `test_readings` provides the telemetry together with its session
information. Imported historical rows retain their source identity and start
timestamps for reference.

An active test has `recording_status=OPEN`. Timer expiry or Ctrl+C finalizes it;
a killed process can leave the row open so the unfinished recording is visible.
`FINISHED` describes saved end metadata, not a guaranteed successful detection.

### Reading fields

Each free/calibration CSV row, and each test's saved sensor reading, includes:

| Fields | Meaning |
|---|---|
| `record_id`, `session_id` | Unique database row and recording session |
| `timestamp_utc` | Time your Mac received the complete sample, not a board clock |
| `activity`, `notes` | Labels supplied by you for this recording |
| `sample_number` | Number printed by the board; can restart after a reset |
| `board_time_ms` | Board HAL tick at acquisition start; milliseconds since boot, wraps after about 49.7 days |
| `accel_x_mps2`, `accel_y_mps2`, `accel_z_mps2`, `accel_magnitude_mps2` | Filtered acceleration components and vector magnitude in m/s² |
| `gyro_x_dps`, `gyro_y_dps`, `gyro_z_dps`, `gyro_magnitude_dps` | Filtered angular velocity components and vector magnitude in degrees/second |
| `accel_msd`, `gyro_msd` | Mean squared change across the three axes since the previous sample |
| `slope_window_samples` | Number of values fitted for each slope: 5 in the current firmware |
| `accel_magnitude_slope`, `gyro_magnitude_slope` | Signed slope fitted through the latest five magnitude values |
| `accel_msd_slope`, `gyro_msd_slope` | Signed slope fitted through the latest five valid MSD values |

XYZ and Magnitude are **the printed values derived from EWMA-filtered axes**,
at the three-decimal precision sent by the firmware. Magnitude is
`sqrt(X² + Y² + Z²)` for one sample; it is not a time average. The logger preserves
the board's printed magnitude, which can differ slightly from the magnitude
calculated using its rounded XYZ. The board calculates metrics before rounding
XYZ/Magnitude for display, and prints metrics in scientific notation (for
example, `1.200000e-04` means `0.00012`).
The logger saves those metrics directly, without recalculating from rounded XYZ.

### If you open an older Avg-format dataset later

If you open an older Avg-format measurement dataset in free or calibration mode,
the logger upgrades it automatically. Current recordings use Magnitude.
Timestamped `before-magnitude` copies of that database and CSV are created in a
`backups` folder beside the files before conversion. The upgraded files replace the two
Avg fields and two Avg-slope fields with magnitude and magnitude-slope fields;
the old average values are not relabelled as magnitudes.

Historical magnitudes are calculated from the saved, rounded XYZ values.
Historical magnitude slopes are fitted again using the saved board timestamps
and window sizes. A new history starts for each session, sample-counter gap or
board reset, so a five-reading window leaves its first four historical slopes
empty. Missing timestamps or otherwise insufficient history also leave slopes
empty. This differs from a live recording started after the board has filled
its windows: those first saved live rows can already contain valid slopes.

XYZ, MSD, MSD slopes, IDs and labels are retained. The rounding of historical XYZ
means reconstructed magnitudes/slopes can differ slightly from values the new
firmware would have calculated before display rounding. The original files in
the backups preserve the old averages for reference.

The logger also removes retired `*_rate` columns when they contain no values.
If an older database contains actual values in those columns, the logger stops
rather than discarding them. Retain that dataset and choose a new pair of
`--db` and `--csv` filenames for subsequent recordings.

`normal` is a manual label, not a determination made by the fall detector. Only
perform the intended activity during that recording. If the board has just
started, early samples include the filter's startup settling. Record board
placement and procedure in `--notes`.

The logger ignores partial readings until it sees a complete new
`Sample / Accel / Gyro` sequence. It does not invent missing samples. Keep the
firmware's current output labels and `Magnitude` fields so they can be parsed.

## Motion metrics and sampling

Each sensor has its own history in `Core/Inc/motion_metrics.h`, used by `main.c`.
For sample `i`, the calculations are:

```text
Magnitude_i = sqrt(X_i^2 + Y_i^2 + Z_i^2)
MSD_i = ((X_i - X_previous)^2 + (Y_i - Y_previous)^2
       + (Z_i - Z_previous)^2) / 3

For each set of five (time, value) pairs:
    mean_time  = average of the five timestamps, in seconds
    mean_value = average of the five values
    slope = sum((time - mean_time) * (value - mean_value))
            / sum((time - mean_time)^2)

MagnitudeSlope = slope fitted to the latest five Magnitude values
MSDSlope = slope fitted to the latest five valid MSD values
```

The firmware fits a straight line through each recent window and reports its
slope in units per second. Positive means increasing; negative means decreasing.
For example, values `2, 3, 4, 5, 6` at times `0, 0.1, 0.2, 0.3, 0.4` seconds
give a slope of `+10` units/second. This fit uses all five points, so it can
estimate a trend despite small fluctuations between individual readings.

Each new reading drops the oldest value from the window. **These calculations
do not accumulate from startup.** Five readings at 100 ms spacing span 400 ms
from first to last; the slope updates every 100 ms. Actual timestamps determine
the fit when intervals vary. Restarting the board clears the windows. Restarting
only the Mac logger does not clear them, because the board computes the metrics.

There is no previous reading for the first sample, so MSD is initially `NA`.
MSD becomes available at the second sample. MagnitudeSlope first becomes
available at the fifth sample; MSDSlope needs five valid MSDs, so it becomes
available at the sixth sample. A window with no elapsed time produces `NA`, avoiding division by
zero. SQLite stores unavailable metrics as NULL; CSV leaves their cells empty.
A logger opened later may immediately receive valid slopes because the board
has already filled its windows.

The new serial format explicitly identifies the window:

```text
Sample 5 TimeMs=600 SlopeWindow=5
Accel EWMA ASM [m/s^2]: X=... Y=... Z=... Magnitude=... MSD=... MagnitudeSlope=... MSDSlope=...
Gyro  EWMA ASM [dps]  : X=... Y=... Z=... Magnitude=... MSD=... MagnitudeSlope=... MSDSlope=...
```

| Metric | Accelerometer unit | Gyroscope unit |
|---|---|---|
| Magnitude | m/s² | degrees/s |
| MSD | (m/s²)² | (degrees/s)² |
| MagnitudeSlope | (m/s²)/s | (degrees/s)/s |
| MSDSlope | (m/s²)²/s | (degrees/s)²/s |

`SAMPLE_INTERVAL_MS` in `main.c` remains `100`, targeting 10 samples per second.
UART remains at 115200 baud; this update does not enable 50 Hz sampling.
LED blinking has independent timing and no longer blocks sampling for one
second. The BSP initializes these sensors at 52 Hz, so the sensor update rate
supports this polling interval. Processing, UART output, and debug pauses can
make intervals longer; slopes use actual elapsed board ticks, not a hard-coded
0.1 seconds. Adjacent `board_time_ms` values let you check the achieved interval.
Firmware arithmetic handles tick rollover, but a reset begins a new history.

Faster sampling gives more detail over time; it does not increase the sensor's
measurement accuracy. All metrics use the **EWMA-filtered axes**. Alpha is still
25%, meaning 25% new input plus 75% previous filtered output, with integer
truncation. At 100 ms, this filter responds about ten times faster in seconds
than the same alpha at 1 second. Filtering suppresses noise but can also reduce
or delay brief peaks. The initial zero filter state creates a startup transient
even with a stationary board. Keep sampling and alpha settings consistent when
comparing activity recordings or choosing thresholds.

Magnitude is nonnegative and avoids cancellation between positive and negative
axis components. Once startup settling has passed, a stationary board should
have an acceleration magnitude near **9.81 m/s²** because it senses gravity,
and a gyroscope magnitude near **0 degrees/s**, subject to sensor noise and
bias. Magnitude uses the already filtered XYZ values; it does not add another
EWMA filter. Filtering components before taking their magnitude can suppress
the magnitude during rapid direction changes.

For fall-detection experiments, view magnitude and MSD alongside the slopes.
A rise followed by a fall within one window can give a near-zero slope despite
a large movement:
`0, 0, 10, 0, 0` has zero fitted slope at equally spaced times. Magnitude alone
cannot identify a fall; ordinary handling can also cause large readings. A recent
peak measurement could be added later. The experimental fall-decision logic is
described in the [Prototype 2 guide](../docs/prototype-2.md).

## Useful commands

Record a one-minute test whose expected result is normal:

```bash
python3 tools/record_activity.py --test --name walking-control --verdict normal \
  --notes "Board held at waist" --duration 60
```

Record a near-fall test with an automatic name:

```bash
python3 tools/record_activity.py --test --verdict near-fall
```

Record freely until the board reports a fall, or until you press Ctrl+C:

```bash
python3 tools/record_activity.py --free --name everyday-activity
```

Record 30 seconds of calibration measurements:

```bash
python3 tools/record_activity.py --calibration --name walking-slowly \
  --notes "Calibration: consistent hand movement"
```

Choose a custom combined test database and its summary CSV:

```bash
python3 tools/record_activity.py --test --name control --verdict normal \
  --db data/control_tests.sqlite3 --csv data/control_tests.csv
```

Choose custom free-recording files:

```bash
python3 tools/record_activity.py --free --db data/exploration.sqlite3 \
  --csv data/exploration.csv
```

Show saved summaries without opening the serial port. Select a mode even for
`--summary`; a test summary does not require `--verdict`:

```bash
python3 tools/record_activity.py --test --summary
python3 tools/record_activity.py --free --summary
python3 tools/record_activity.py --calibration --summary
```

Inspect the last five test sensor readings:

```bash
sqlite3 -header -column data/prototype_verdicts.sqlite3 'SELECT * FROM test_readings ORDER BY record_id DESC LIMIT 5;'
```

Inspect the last five free-recording rows:

```bash
sqlite3 -header -column data/prototype_readings.sqlite3 'SELECT * FROM readings ORDER BY record_id DESC LIMIT 5;'
```

Stop a test after five samples for a quick recording check:

```bash
python3 tools/record_activity.py --test --name verification --verdict normal --samples 5
```

Recorded files are ignored by Git by default. The logger and this guide can be
shared with your teammate. Share measurement files separately when needed.

## Verification

```bash
python3 -m unittest discover -s tools -p 'test_*.py' -v
cc -std=c11 -Wall -Wextra -Werror -pedantic -I CG2028_Assignment/Core/Inc tools/test_motion_metrics.c -lm -o /tmp/cg2028-motion-metrics-test
/tmp/cg2028-motion-metrics-test
```
