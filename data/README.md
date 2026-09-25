# Sensor recordings

Each Python recording session runs for **30 seconds by default** and saves
complete accelerometer and gyroscope samples to both files in this folder:

- `activity_readings.sqlite3`: the SQLite database; the authoritative copy.
- `activity_readings.csv`: the same saved measurements, for Excel or analysis.

**Reset on 24 September 2026:** all earlier trials, recording backups and
generated analysis were cleared at your request. The database was recreated
with no sessions or readings, and the CSV with only its current column headers.
The next recording starts at session 1.

## Start and name a recording

1. Rebuild and flash the updated `CG2028_Assignment` firmware, then run/resume
   it on the board in CubeIDE. This version prints `Magnitude` and
   `MagnitudeSlope`; the logger rejects old `Avg` serial output explicitly.
2. Close `screen` or any other program using the board's serial port.
3. From the repository root (`cg2028-Assignment`), run:

   ```bash
   python3 tools/record_activity.py --activity normal-walking --notes "Trial 1: walking and everyday activity"
   ```

`--activity` labels the activity. `--notes` describes the trial, board placement,
or other useful context. These labels are saved with the measurements; they do
not rename the files or automatically classify the movement.

The logger automatically stops after **30 seconds** and reports how many
samples it saved. The timer starts when the recording session opens, including
time waiting for readings, so resume the board first. Every complete sample is
saved as it arrives. An incomplete sample at the deadline is discarded.
**Ctrl+C stops early** and keeps the readings already saved. The board continues
running after recording stops. The logger enables Ctrl+C handling at startup.

Each run appends a new session to the same files, preserving earlier runs.
Starting the board alone does not save data on the computer: the Python logger
must also be running. No extra Python packages or separate Conda environment are
needed on Windows, macOS, or Linux.

On Windows, find the board under **Device Manager → Ports (COM & LPT)** and use
its COM number. For example:

```powershell
python tools\record_activity.py --port COM3 --activity normal-walking --notes "Trial 1"
```

Close CubeIDE's serial terminal, PuTTY, or another serial viewer first; only one
program can own the COM port at a time.

To use separate filenames for a trial:

```bash
python3 tools/record_activity.py --activity normal-walking --notes "Trial 1" \
  --db data/walking_trial1.sqlite3 --csv data/walking_trial1.csv
```

Reusing those filenames appends another session. To explicitly change the time
limit, add `--duration 60` for one minute. With `--samples`, recording stops at
that sample count or the time limit, whichever happens first.

## Which columns contain the current rates of change?

The CSV has **22 columns**. **The four `*_slope` columns contain the current
rates of change.**

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

The earlier trials from this workspace have been cleared. The following applies
only if you later open another older Avg-format dataset with this logger.

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

IDs can have gaps after deleted trials; they are not a count of remaining rows.
The board's timestamps are used for the slopes, rather than the Mac's receipt
time. XYZ/Magnitude are printed to three decimal places; the board computes
metrics before that display rounding, so recalculating from CSV XYZ can differ
slightly.

## Inspect and retain recordings

Show sessions and sample counts without opening the serial port:

```bash
python3 tools/record_activity.py --summary
```

Open the CSV in Excel after recording. Keep the logger's CSV consistent with
its database: do not overwrite it with spreadsheet edits. Save edited analysis
as a separate file. The logger
can recreate a missing CSV from SQLite and recover missing tail rows.

Measurement files are ignored by Git by default; this README can be shared
with your teammate. See the [full logger guide](../tools/README.md) for more
commands, serial connection details, and verification instructions.
