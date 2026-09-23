# Record normal activities

`record_activity.py` runs on your Mac, reads the application's UART
output, and appends complete samples to **both a SQLite database and a CSV file**.
No additional Python packages are needed. Python 3 on macOS or Linux is required
for serial recording. Rebuild and flash the updated `CG2028_Assignment` firmware
to get the five-reading regression slopes and 100 ms sampling. Older firmware output is also
accepted, with empty cells for the extra fields.

```text
STM32 accelerometer + gyroscope → UART output → Python logger
                                                       ├─ SQLite database
                                                       └─ CSV for Excel
```

## Start a recording

1. Build/run `CG2028_Assignment` on the board and click Resume in CubeIDE if it
   is paused at `main()`.
2. Close `screen` or any other serial viewer so the logger can own the port.
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

Watch the readings and saved-row count in Terminal. **Press Ctrl+C to stop**;
there is no `screen` shortcut involved. The board continues running.

The board **and this logger** must be running and connected to record. Nothing is
recorded while the logger is closed, the board is paused, or the computer sleeps.
If disconnected, saved data is kept; reconnect and run the command again.

## Files created and retained

- `data/activity_readings.sqlite3`: the SQLite database, a normal local file.
- `data/activity_readings.csv`: the same measurements in a spreadsheet format.

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
| `accel_x_mps2`, `accel_y_mps2`, `accel_z_mps2`, `accel_avg_mps2` | Filtered acceleration in m/s² |
| `gyro_x_dps`, `gyro_y_dps`, `gyro_z_dps`, `gyro_avg_dps` | Filtered angular velocity in degrees/second |
| `accel_msd`, `gyro_msd` | Mean squared change across the three axes since the previous sample |
| `slope_window_samples` | Number of values fitted for each slope: 5 in the current firmware |
| `accel_avg_slope`, `gyro_avg_slope` | Signed slope fitted through the latest five Avg values |
| `accel_msd_slope`, `gyro_msd_slope` | Signed slope fitted through the latest five valid MSD values |
| `accel_avg_rate`, `gyro_avg_rate`, `accel_msd_rate`, `gyro_msd_rate` | Cumulative absolute-change rates from older firmware only; empty for new recordings |

XYZ and Avg are **the printed EWMA-filtered values**, at the three-decimal precision
sent by the firmware. `Avg` is the signed arithmetic mean across axes for one
sample; it is not a time average or vector magnitude. The logger preserves the
board's printed Avg, which can differ slightly from averaging its rounded XYZ.
The board calculates metrics before rounding XYZ/Avg for display, and prints
metrics in scientific notation (for example, `1.200000e-04` means `0.00012`).
The logger saves those metrics directly, without recalculating from rounded XYZ.

Existing databases gain nullable columns automatically. A matching old-format
CSV is verified against its database, then replaced atomically with an expanded
copy containing all saved rows. Earlier records keep their original values,
IDs, and labels; their extra fields remain empty rather than inventing metrics.
This includes recordings made with the previous cumulative-rate firmware:
`*_rate` keeps its original meaning, while new signed rates use `*_slope`.

`normal` is a manual label, not a determination made by the fall detector. Only
perform the intended activity during that recording. Early samples include the
filter's startup settling. Record board placement and procedure in `--notes`.

The logger ignores partial readings until it sees a complete new
`Sample / Accel / Gyro` sequence. It does not invent missing samples. Keep the
firmware's current output labels and `Avg` fields so they can be parsed.

## Motion metrics and sampling

Each sensor has its own history in `Core/Inc/motion_metrics.h`, used by `main.c`.
For sample `i`, the calculations are:

```text
Avg_i = (X_i + Y_i + Z_i) / 3
MSD_i = ((X_i - X_previous)^2 + (Y_i - Y_previous)^2
       + (Z_i - Z_previous)^2) / 3

For each set of five (time, value) pairs:
    mean_time  = average of the five timestamps, in seconds
    mean_value = average of the five values
    slope = sum((time - mean_time) * (value - mean_value))
            / sum((time - mean_time)^2)

AvgSlope = slope fitted to the latest five Avg values
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
MSD becomes available at the second sample. AvgSlope first becomes available at
the fifth sample; MSDSlope needs five valid MSDs, so it becomes available at the
sixth sample. A window with no elapsed time produces `NA`, avoiding division by
zero. SQLite stores unavailable metrics as NULL; CSV leaves their cells empty.
A logger opened later may immediately receive valid slopes because the board
has already filled its windows.

The new serial format explicitly identifies the window:

```text
Sample 5 TimeMs=600 SlopeWindow=5
Accel EWMA ASM [m/s^2]: X=... Y=... Z=... Avg=... MSD=... AvgSlope=... MSDSlope=...
Gyro  EWMA ASM [dps]  : X=... Y=... Z=... Avg=... MSD=... AvgSlope=... MSDSlope=...
```

| Metric | Accelerometer unit | Gyroscope unit |
|---|---|---|
| Avg | m/s² | degrees/s |
| MSD | (m/s²)² | (degrees/s)² |
| AvgSlope | (m/s²)/s | (degrees/s)/s |
| MSDSlope | (m/s²)²/s | (degrees/s)²/s |

`SAMPLE_INTERVAL_MS` in `main.c` is now `100`, targeting 10 samples per second.
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

For fall-detection experiments, view MSD alongside the slopes. A rise followed
by a fall within one window can give a near-zero slope despite a large movement:
`0, 0, 10, 0, 0` has zero fitted slope at equally spaced times. Signed axis
averages can also cancel opposite directions. Possible later additions are
vector magnitude `sqrt(X*X + Y*Y + Z*Z)` and a recent peak measurement. Those
features and the actual fall-decision logic are not implemented by this update.

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
