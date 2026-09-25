# Record normal activities

`record_activity.py` runs on Windows, macOS, or Linux, reads the application's UART
output, and appends complete samples to **both a SQLite database and a CSV file**.
No additional Python packages are needed. **Rebuild and flash the updated `CG2028_Assignment`
firmware before recording:** it now sends vector magnitude instead of the
arithmetic mean across axes. Old `Avg` serial output produces an explicit error
so that averages cannot be mistaken for magnitudes. Sampling remains at 100 ms
and the baud rate remains 115200.

The [Prototype 1 guide](../docs/prototype-1.md) explains the experimental
fall/near-fall decisions, LED behavior and board-reset procedure. Its `DETECTOR`
messages appear in Terminal; measurement files retain their 22-column format.

```text
STM32 accelerometer + gyroscope → UART output → Python logger
                                                       ├─ SQLite database
                                                       └─ CSV for Excel
```

## Start a recording

1. Build/run `CG2028_Assignment` on the board and click Resume in CubeIDE if it
   is paused at `main()`.
2. Close CubeIDE's serial terminal, `screen`, PuTTY, or any other serial viewer so
   the logger can own the port. On Windows, find the board under **Device Manager
   → Ports (COM & LPT)**.
   For a named session, use `screen -S stm32 -X quit` in another Terminal tab.
   Otherwise, use `screen -ls` and `screen -S SESSION_ID -X quit`.
3. Run:

   ```bash
   cd /Users/ryantan/MyWork/CG2028workspace/cg2028-Assignment
   python3 tools/record_activity.py --activity normal
   ```

The logger detects the USB serial device and uses 115200 baud, 8 data bits,
no parity, 1 stop bit and no flow control. If multiple devices are connected,
choose explicitly using `--port /dev/cu.usbmodemXXXX` (use the current name from
`ls /dev/cu.usbmodem*`). The detected name can change when reconnecting the board.

On Windows, automatic detection reads the registered COM ports. If exactly one is
present it is selected; if there is more than one, choose the board explicitly:

```powershell
python tools\record_activity.py --port COM3 --activity normal-walking --notes "Trial 1"
```

If no Windows port is found, check **Device Manager → Ports (COM & LPT)** and
rerun with that `COMx` name. CubeIDE's serial terminal, PuTTY, and other serial
monitors must release the port before recording.

Watch the readings and saved-row count in Terminal. **Each run automatically
stops after 30 seconds**, keeping every complete sample saved to SQLite and CSV.
The timer starts when the recording session opens, including time spent waiting
for the board, so resume the board before starting. Partial samples at the time
limit are discarded. The board continues running after the logger stops.
You can **press Ctrl+C to stop early**; there is no `screen` shortcut involved.
An explicit `--duration 60` overrides the default for a one-minute recording.
If `--samples` is supplied, recording ends at that count or the time limit,
whichever happens first.
The logger enables Ctrl+C handling and normal newline output on its interactive
terminal at startup, including when an earlier program left those settings
disabled. Redirected files and pipes are left alone.

The board **and this logger** must be running and connected to record. Nothing is
recorded while the logger is closed, the board is paused, or the computer sleeps.
If disconnected, saved data is kept; reconnect and run the command again.

## Files created and retained

- `data/activity_readings.sqlite3`: the SQLite database, a normal local file.
- `data/activity_readings.csv`: the same measurements in a spreadsheet format.

**Reset on 24 September 2026:** all earlier trials, recording backups and
generated analysis were cleared at your request. The database now starts empty
and the CSV contains only the current column headers; the next run is session 1.

The [data README](../data/README.md) explains the **22-column CSV format**.
Current rates appear in the four `*_slope` columns. The four empty legacy
`*_rate` columns have been removed from the CSV and database and will not be
created for future recordings.

Each run creates a new session and appends rows; earlier recordings are retained.
SQLite commits each complete sample, and the CSV is flushed after each row.
The database is the authoritative copy. If the CSV is missing, it is recreated
from the database on the next recording. A missing CSV tail is also recovered.
If an existing CSV was edited or belongs to another database, the logger stops
instead of mixing data; choose a new CSV filename with `--csv` to regenerate it.
Open the CSV in Excel for analysis, but do not save spreadsheet edits over the
logger's CSV while recording.

Each row includes:

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

The earlier workspace recordings were cleared by the reset above. If you later
open another Avg-format dataset, the logger upgrades it automatically.
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
described in the [Prototype 1 guide](../docs/prototype-1.md).

## Useful commands

Record normal walking for one minute:

```bash
python3 tools/record_activity.py --activity walking --notes "Normal walking; board held at waist" --duration 60
```

Record a different activity in a new session, using the same files:

```bash
python3 tools/record_activity.py --activity sitting --notes "Sitting down normally"
```

Choose a different pair of output files:

```bash
python3 tools/record_activity.py --db data/experiment2.sqlite3 --csv data/experiment2.csv
```

Show session counts without opening the serial port:

```bash
python3 tools/record_activity.py --summary
```

Inspect the last five database rows with macOS's SQLite command:

```bash
sqlite3 -header -column data/activity_readings.sqlite3 'SELECT * FROM readings ORDER BY record_id DESC LIMIT 5;'
```

Stop after five samples for a quick recording check:

```bash
python3 tools/record_activity.py --activity verification --samples 5
```

Recorded files are ignored by Git by default. The logger and this guide can be
shared with your teammate. Share measurement files separately when needed.

## Verification

```bash
python3 -m unittest discover -s tools -p 'test_record_activity.py' -v
cc -std=c11 -Wall -Wextra -Werror -pedantic -I CG2028_Assignment/Core/Inc tools/test_motion_metrics.c -lm -o /tmp/cg2028-motion-metrics-test
/tmp/cg2028-motion-metrics-test
```
