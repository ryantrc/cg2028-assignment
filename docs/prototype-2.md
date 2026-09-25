# Prototype 2: compare a disturbance with recent activity

Prototype 2 adds a baseline comparison before the existing fall/near-fall
observation. A large accelerometer change first becomes a **provisional
candidate**. It must also represent a sufficiently large change from the
preceding activity before the detector uses later quiet or continued movement
to make a decision.

The detector runs on the STM32. Python records its output; a recording name or
`--verdict` value never changes the firmware's decision. These rules are
experimental settings for hand-moved board trials. They do not establish that
someone fell, is injured, or regained their balance.

Sampling remains **100 ms**, UART remains **115200 baud**, and each axis uses
the existing **25% EWMA**. Magnitude, MSD and five-reading regression slopes
continue to be calculated and saved. The detector uses accelerometer MSD,
gyroscope MSD and gyroscope magnitude; it does not use the regression slopes.

## The baseline comparison

1. After a reset or a restart of detection, allow **two seconds for filter
   settling**, then collect **three seconds of baseline readings**. The state
   remains `WARMUP` until sufficient valid evidence is available. Under normal
   sampling this takes roughly five seconds; `READY` marks the return to
   `NORMAL` monitoring.
2. In monitoring, accelerometer MSD **at least 5** triggers a provisional
   `SPIKE`. The baseline is the arithmetic mean of accelerometer MSD from the
   **previous three seconds**, excluding the triggering sample. It is fixed
   for that candidate, so the event cannot raise its own baseline.
3. Collect the arithmetic mean of accelerometer MSD over the **two seconds
   starting with the triggering sample**. A sample exactly two seconds after
   the trigger belongs outside this event window.
4. Compare the two means:

   ```text
   ratio = event_mean / max(baseline_mean, 0.01)
   ```

   A ratio **at least 4** confirms the disturbance. The floor of `0.01`
   prevents a zero or extremely small baseline from making the comparison
   unstable. Both means and the floor use (m/s²)²; the ratio has no unit.

The value 4 is an **experimental threshold**. The comparison uses means over
time, not `(X + Y + Z) / 3` and not a ratio of single peaks. All inputs are
based on the existing EWMA-filtered acceleration measurements.

With a complete baseline, the comparison is evaluated at the first valid
sample reaching the two-second boundary. Missing baseline evidence can be
detected earlier. The possible outcomes are:

| Result | Meaning and next step |
|---|---|
| `DISTURBANCE_CONFIRMED` | Ratio is at least 4 with adequate evidence. Continue in `OBSERVING`, retaining the original spike time. |
| `DISTURBANCE_REJECTED` | Ratio is below 4 with adequate evidence. Return to `NORMAL`; this is not a `NEAR_FALL` decision. |
| `DISTURBANCE_UNKNOWN` | Evidence is missing or inadequate. Return to `WARMUP` and collect valid history again. Do not interpret this as ordinary movement. |

The three-second baseline keeps updating during ordinary monitoring, so the
comparison reflects recent activity rather than the start of the Python
recording. Startup transients are excluded by the filter-settling period.
Faults and excessive sampling gaps invalidate unfinished candidates; they do
not supply evidence that the board became quiet.

Both a provisional spike and a confirmed disturbance use `State=OBSERVING`.
Use the event names to tell them apart. Confirmation and rejection diagnostic
lines include `BaselineMSD`, `EventMSD` and `IncreaseRatio`, so you can inspect
the two means and their ratio. Routine `STATUS` lines keep their existing
format. The logger retains these diagnostic lines in the test database's
`events` table, or in the free database's `detector_events` table; it does not
add these three values as measurement CSV columns.

The windows must contain readings distributed across their duration, not just
a few samples near one edge. The exact coverage checks are in
[fall_detector.h](../CG2028_Assignment/Core/Inc/fall_detector.h).

## Timing after a confirmed disturbance

The two-second comparison is part of the original eight-second observation,
so confirmation does **not** start another eight-second timer. Let `t = 0` be
the provisional spike:

| Time relative to the spike | What the detector uses |
|---|---|
| `[-3, 0)` seconds | Baseline mean; triggering sample excluded |
| `[0, 2)` seconds | Event mean; triggering sample included |
| At about 2 seconds | Confirm, reject or report insufficient disturbance evidence |
| `[5, 6)`, `[6, 7)`, `[7, 8)` seconds | Three one-second quiet/moving checks for a confirmed disturbance |
| At about 8 seconds | First possible fall/near-fall decision |

Sampling, serial output and LED updates continue throughout; there is no
blocking wait. Three quiet blocks produce `POSSIBLE_FALL`. Three moving blocks
produce `NEAR_FALL`. If the blocks disagree, the state is `UNCERTAIN`; each
subsequent one-second block updates the latest three-block decision. Later
spikes do not restart the original timer.

Each block uses the arithmetic mean of all three measurements across that
second. All three must meet the same category:

| Measurement | Quiet | Moving | Unit |
|---|---|---|---|
| Accelerometer MSD | `< 0.005` | `>= 0.005` | (m/s²)² |
| Gyroscope MSD | `< 1` | `>= 1` | (degrees/s)² |
| Gyroscope magnitude | `< 5` | `>= 5` | degrees/s |

A mixture is uncertain. Each block needs at least nine samples, its first
within 150 ms of the block start, its last at or after 850 ms, and a span of at
least 750 ms. An incomplete block breaks the quiet/moving streak. A sample
exactly on a block boundary belongs to the next block.

## LED, faults and restarting

Monitoring, provisional candidates, observation, uncertainty and near-fall
results keep the normal LED toggle interval of **1000 ms**. A fall result
latches the alarm and toggles the LED every **150 ms** until the board resets.
A full blink takes two toggles. The alarm stays latched even if a later sensor
fault occurs.

After a rejected disturbance or a near-fall, accelerometer MSD must drop below
5 before another spike can start a candidate. Remaining above the trigger
threshold does not repeatedly start new observations.

A sampling gap **greater than 250 ms** clears an unfinished candidate and
restarts filter settling and baseline collection. Invalid readings are
rejected instead of saved as fake samples. Sensor initialization and I²C faults
require a board reset. Valid evidence must return before monitoring resumes.
Missing readings never count as quiet movement.

**Restarting Python does not reset the detector.** Reset the board between
trials, then wait for `READY` / `State=NORMAL Alarm=0`. A previously latched
alarm observed when the logger connects is not a new fall in that recording.

## Build and run a trial

1. In CubeIDE, rebuild and flash **`CG2028_Assignment`** with Prototype 2.
   Resume execution if debugging pauses at `main()`. This update changes
   firmware; updating Python alone does not install it on the STM32.
2. Close any other serial viewer and start the recorder from the repository
   root:

   ```bash
   python3 tools/record_activity.py --test --name prototype-2-trial-1 \
     --verdict fall --notes "Board moved by hand; record baseline and event"
   ```

3. Let the filter settle, then use the ordinary activity that should precede
   the event during baseline collection. Wait for `READY` before the intended
   spike. Record what the baseline activity was; it affects the ratio.
4. Perform the intended movement and allow at least eight seconds afterward
   for the first decision. Leave longer for an uncertain result to resolve.
   Inspect `SPIKE`, the disturbance result, and the later decision separately.
5. Reset the board before the next trial. Keep fresh comparison trials separate
   from recordings used to choose thresholds.

Recording modes and files are unchanged:

- **`--test`** records for the full default **30 seconds**, even when a fall is
  detected early. `--duration` changes the limit; `--samples` or Ctrl+C can end
  a test early. Expected labels, samples and diagnostics go to
  `prototype_verdicts.sqlite3`; its CSV has one summary row per test.
- **`--free`** records to `prototype_readings.sqlite3` and `.csv` until a fall
  is reported or you stop it. A rejected disturbance, near-fall or uncertain
  result does not stop free recording.
- **`--calibration`** records 30 seconds to `calibration_readings.sqlite3` and
  `.csv`. Use `--name` and `--notes` to describe baseline activity and setup.

The timer includes startup/waiting time if the recorder is already running.
Use `--duration 60` for a test needing more preparation. Existing recordings
are retained; offline replay does not overwrite their saved live outcomes.
In a test summary, a rejected candidate can leave `NO_EVENT_OBSERVED`; this
means no fall/near-fall decision was observed, not proof of ordinary activity.
`DISTURBANCE_UNKNOWN` instead leaves `OBSERVATION_INCOMPLETE`, even after a later
`READY` message, unless an explicit fall/near-fall decision takes precedence.
See [the recorder guide](../tools/README.md) and
[saved verdict meanings](../data/README.md#reading-the-prototype-verdicts).

## Replay check

Replay of the original 35 recordings retained the earlier outcomes: 20 without
an event, 10 fall decisions and five near-fall decisions. None of the 15
running/running-then-stopping recordings reached a confirmed disturbance or
fall/near-fall decision. However, **nine ended with unfinished provisional
candidates**, so those nine cannot be counted as successful rejections.

These are checks on previously recorded hand-moved board data, not detection
accuracy on new trials or human falls. The
[reproducible replay report](../data/prototype2_analysis_2026-09-25/README.md)
contains the per-recording results and limitations. Saved live verdicts remain
unchanged.

## What to test next

The baseline ratio is promising for separating some hand-moved board patterns,
but it can also respond to ordinary changes in activity. Starting to run after
a quiet baseline may pass the comparison. A stumble while already running may
fail it because the preceding baseline is high. Passing the comparison does
not prove that a fall occurred, and failing it does not prove normal activity.

Compare repeated trials of steady movement, starting and stopping movement,
sudden movement followed by stillness, and sudden movement followed by continued
movement. Keep board placement, sampling and EWMA settings consistent. Inspect
whether a missed intended event was rejected by the ratio, lacked evidence,
or reached the later quiet/moving checks. Hand-moved demonstrations and replay
results do not establish accuracy for actual human falls.

The application integration is in
[main.c](../CG2028_Assignment/Core/Src/main.c); thresholds and detector logic are
in [fall_detector.h](../CG2028_Assignment/Core/Inc/fall_detector.h).
[Prototype 1](prototype-1.md) documents the earlier behavior without this
baseline comparison.
