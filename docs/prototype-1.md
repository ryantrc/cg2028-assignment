# Prototype 1: sudden movement followed by quiet or continued movement

**Historical behavior:** this page describes Prototype 1 before the baseline
comparison was added. The current implementation and setup instructions are in
[the Prototype 2 guide](prototype-2.md). Rebuild and flash the updated firmware
to use Prototype 2; the linked source files now contain that newer version.

This prototype runs on the STM32. It watches for a large accelerometer change,
then uses the following activity to choose a **fall**, **near-fall**, or
**uncertain** result. These names describe the programmed movement patterns;
the measurements do not establish whether someone is injured, in pain, or has
regained their balance. Hand-moved board demonstrations test the prototype's
response to those movements.

Sampling remains **100 ms**, UART remains **115200 baud**, and each axis still
uses the existing **25% EWMA** filter. Magnitude, MSD and the five-reading
regression slopes continue to be printed and recorded. The detector uses
accelerometer MSD, gyroscope MSD and gyroscope magnitude; it does not use the
regression slopes to make its decision.

## What happens after a sudden movement

1. After initialization, the detector waits for **two seconds of valid data**
   before allowing a new trigger. This gives the filter time to settle.
2. An accelerometer MSD of **5 or greater** starts an observation period.
3. The detector observes the next **eight seconds**. Sampling, calculations,
   UART output and LED timing continue during this period; there is no blocking
   eight-second delay.
4. The first five seconds allow the movement and filter transient to settle.
   It then checks the **three one-second blocks covering seconds 5–8** after
   the trigger. Three quiet blocks produce a fall result. Three moving blocks
   produce a near-fall result. The first decision is made on the sample that
   reaches the eight-second boundary.
5. If those blocks do not agree, the result remains uncertain. It continues
   sampling and checks the latest three blocks as each new one-second block
   completes, until three agree.

The thresholds are experimental settings for this prototype. A high MSD means
the filtered acceleration vector changed sharply between consecutive readings;
it is not a direct impact-force measurement.

## How quiet and moving are defined

Each block uses the **arithmetic mean of each measurement across that second**.
These are averages over time, not the removed `(X + Y + Z) / 3` axis average.
All three means must meet the same category:

| Measurement | Quiet | Moving | Unit |
|---|---|---|---|
| Accelerometer MSD | `< 0.005` | `>= 0.005` | (m/s²)² |
| Gyroscope MSD | `< 1` | `>= 1` | (degrees/s)² |
| Gyroscope magnitude | `< 5` | `>= 5` | degrees/s |

A mixture of quiet and moving conditions is **uncertain**. For example, low
accelerometer MSD with high gyroscope activity does not satisfy all the quiet
conditions or all the moving conditions.

Each block must also contain at least **nine samples**, with its first sample
within 150 ms of the block start, its last at or after 850 ms, and at least
750 ms between its first and last samples. An incomplete or mixed block breaks
the streak of quiet or moving blocks. A sample exactly on a one-second boundary
belongs to the next block. Later spikes do not restart the original timer.

Acceleration magnitude is deliberately not tested against zero: a stationary
board senses gravity, so its magnitude is normally near 9.81 m/s². The quiet
test instead examines changes in acceleration and movement measured by the
gyroscope.

## LED and Terminal output

| Detector condition | LED behavior |
|---|---|
| Monitoring, observing, uncertain or near-fall | Toggle every 1000 ms |
| Fall result | Toggle every 150 ms, latched until the board resets |

“Toggle” means changing from on to off or from off to on; a full blink cycle
takes two toggles. A near-fall is reported in Terminal while the LED remains at
its normal slow rate. The detector returns to monitoring after a near-fall.
Accelerometer MSD must then drop below 5 before another spike can trigger;
remaining continuously above 5 does not repeatedly start new observations.

Firmware diagnostic lines begin with `DETECTOR`. The Python logger displays
detector events in Terminal and uses routine `STATUS` messages to update a
single live line instead of scrolling sensor values and status lines:

```text
reading no. = 42 State = NORMAL Alarm = 0
```

The counter counts complete saved readings in the current run. State and alarm
are `UNKNOWN` until a valid diagnostic arrives. Warnings and detector events
still print separately. In **test mode**, `data/prototype_verdicts.sqlite3` stores
everything for the test: its expected label, actual result, sensor samples and
detector messages. `prototype_verdicts.csv` has one summary row per test.
Tests do not also write to the free-recording `prototype_readings` files.

`--verdict` supplies your expected outcome, while `--name` supplies an optional
test name (`--activity` remains an alias). Neither is sent to the firmware or
used to decide the actual result. Compare `expected_verdict` with
`observed_verdict` afterward. In the combined database, `run_id` links summaries,
`test_samples` and `events`; the `test_readings` view exposes sensor measurements.
The summary distinguishes a received `POSSIBLE_FALL` event from an alarm that
was already latched when the logger connected. A run that ends while observing
or uncertain is not silently called normal. See the
[saved verdict definitions](../data/README.md#reading-the-prototype-verdicts)
for the complete statuses and historical `NOT_RECORDED` rows.

For example, a trigger produces a line like:

```text
DETECTOR TimeMs=12345 State=OBSERVING Alarm=0 Sensors=OK Event=SPIKE ...
```

`Event=POSSIBLE_FALL` accompanies the latched fall result, and
`Event=NEAR_FALL` reports a return to monitoring. Other events include `READY`,
`UNCERTAIN`, `SENSOR_FAULT`, `RESTARTED` and periodic `STATUS` messages. A status
message is sent approximately once per second, so a logger opened later can
still display the current state.

## Missing data and faults

A sampling gap **greater than 250 ms** clears an unfinished observation and
restarts the two-second warmup. Detected invalid readings, including all six
raw axes being zero together, are rejected instead of being saved as fake
samples. Valid data must resume and the warmup must complete before detection
continues. A block without enough samples cannot count as quiet.

Sensor initialization or I²C failures report a sensor fault and require a board
reset. These checks avoid using detected communication faults as evidence of
stillness. They do not establish that every conceivable sensor fault is
detectable. Once a fall has latched, it remains latched even if a later sensor
fault occurs; reset the board to begin another trial.

## Run a trial

1. Run the existing Prototype 1 firmware and click **Resume** if CubeIDE stops
   at `main()`. If Prototype 1 has not yet been installed, build and upload it
   first. The recording-mode update changes the Python recorder
   only and does not require another firmware upload.
2. Close `screen` or any other program using the serial port.
3. Start the recorder from the repository root:

   ```bash
   python3 tools/record_activity.py --test --name fall-test-3 --verdict fall \
     --notes "Trial 3: board moved by hand; sudden movement then held still"
   ```

4. Allow startup to settle, then perform the intended movement. Leave enough
   time for the eight-second observation and any additional uncertain period.
   `--test` continues recording for the full **30 seconds** by default, even
   if a fall is detected early. Use `--duration 60` if a trial needs more time;
   a chosen `--samples` limit or Ctrl+C can end the recording early.
5. Check the `DETECTOR` messages and LED. A latched fast blink persists after
   the recorder finishes because the STM32 keeps running.
6. **Reset the board before each new fall trial.** Restarting Python creates a
   new recording session but does not clear the board's filter or latched
   detector state. Receiving a `STATUS` message for an already latched alarm
   does not mean a new fall occurred in the current trial.

Exactly one mode is required: `--test`, `--free`, or `--calibration`. There is
no default. A new test requires `--verdict fall`, `--verdict near-fall` or
`--verdict normal`. The example stores the name `fall-test-3-fall`; without a
name, the recorder uses `test-<session_id>-<expected_verdict>`.

Use `--calibration` for 30 seconds of measurements used to choose thresholds.
These go only to `calibration_readings.sqlite3` and `.csv`. A different duration,
`--samples` or an expected verdict is not accepted in calibration mode.

Use `--free` to explore activity **until the board reports a fall**. This writes
measurements to `prototype_readings.sqlite3` and `.csv`, and keeps detector
messages in that database's `detector_events` table. It has no expected label or
test summary. An explicit possible-fall event or a valid latched-alarm status
prints **Fall detected** and stops Python; near-fall and uncertain results keep
recording. A previously latched alarm also stops the run and is identified as
preexisting, so reset the board before starting again. Free mode rejects
`--duration` and `--samples`; Ctrl+C can stop it early. The board keeps running
after Python stops. Choosing a recording mode does not change the firmware.

For comparison, also record continued movement after a sudden movement, normal
walking-like handling without a deliberate spike, and ordinary placement on a
table. Use consistent board placement and movement procedures. Note roughly
when the event began and what you did afterward.

## What has and has not been established

These settings are a starting point for experiments. Calibration or replay
against previously recorded demonstrations does **not** establish detection
accuracy on new trials or actual human falls. A saved verdict documents the
board's reported decision; compare it with what you actually did during the
trial to evaluate the result.

Keep fresh comparison recordings separate from those used to choose thresholds.
Count the missed intended events, ordinary movements reported as falls, and
time from the event to the decision. If the detector frequently remains
uncertain, inspect which of the three block measurements disagree before
changing thresholds.

See [the recorder guide](../tools/README.md) for saving and naming sessions, and
[main.c](../CG2028_Assignment/Core/Src/main.c) for the application integration.
The state machine and adjustable thresholds are in
[fall_detector.h](../CG2028_Assignment/Core/Inc/fall_detector.h).
