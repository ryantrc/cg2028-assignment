# Prototype 3: continue observing after a low baseline ratio

Prototype 3 removes the early rejection of a valid low-ratio candidate. A spike
followed by sustained quiet can now produce a possible fall even when its
two-second acceleration-MSD mean is less than four times the preceding
baseline. The ratio still distinguishes a near-fall result from the neutral
`MOVEMENT_CONTINUED` result when movement continues.

This replaces the ratio veto described in the historical
[Prototype 2 guide](prototype-2.md). It is a limited change to the decision
rule, not a redesign of the observation duration. **Running followed by a stop
can again cause false fall alarms.** Hand-moved board trials do not establish
reliable detection of falls in people.

Sampling stays **100 ms**, UART stays **115200 baud**, and each axis retains
the **25% EWMA** filter. Magnitude, MSD, five-reading slopes, Telegram delivery,
Long Lie escalation and recording destinations remain unchanged. Detection
runs on the STM32; Python records the firmware's results. Your expected test
label never changes the detector's answer.

## Decision timing

Startup still needs two seconds of filter settling followed by three seconds
of baseline collection. Wait for `READY` / `State=NORMAL Alarm=0` before the
intended event. In monitoring, accelerometer MSD **at least 5** starts a
candidate and emits `SPIKE` with `State=OBSERVING`.

The baseline is the arithmetic mean of acceleration MSD over the previous
three seconds, excluding the triggering sample. The event mean covers the
first two seconds starting with that sample:

```text
ratio = event_mean / max(baseline_mean, 0.01)
```

Both means and the `0.01` floor use (m/s²)²; the ratio has no unit. These are
means over time, based on the existing filtered acceleration. The baseline is
fixed for the candidate, so the event cannot raise its own baseline.

Let `t = 0` be the original spike:

| Time | Behavior |
|---|---|
| `[-3, 0)` seconds | Baseline window; trigger excluded |
| `[0, 2)` seconds | Event window; trigger included |
| At about 2 seconds | Ratio at least 4 emits `DISTURBANCE_CONFIRMED`; below 4 emits `DISTURBANCE_LOW`. Both stay `OBSERVING`. |
| `[5, 6)`, `[6, 7)`, `[7, 8)` seconds | Three one-second quiet/moving checks |
| At about 8 seconds | Earliest decision, using the table below |

The first five seconds after the spike are allowed for settling before the
quiet/moving checks. The two-second ratio calculation is inside that period;
it does not add another delay or reset the original timer. Sampling, UART and
LED updates continue throughout.

| Latest three complete blocks | Ratio | Result |
|---|---|---|
| All quiet | Either high or low | `POSSIBLE_FALL`; alarm latches |
| All moving | At least 4 | `NEAR_FALL`; return to `NORMAL` |
| All moving | Below 4 | `MOVEMENT_CONTINUED`; return to `NORMAL` without an alarm |
| Mixed or inconsistent | Either | `UNCERTAIN`; continue checking later one-second blocks |

Eight seconds is the **earliest** decision, not a maximum observation duration.
While uncertain, the existing rolling check continues until three blocks agree.
Later spikes do not restart the original timer. After `NEAR_FALL` or
`MOVEMENT_CONTINUED`, acceleration MSD must drop below 5 before another spike
can start a candidate.

## Quiet, moving and missing evidence

Each block uses the arithmetic mean of each of these measurements across one
second. All three must meet the same category:

| Measurement | Quiet | Moving | Unit |
|---|---|---|---|
| Accelerometer MSD | `< 0.005` | `>= 0.005` | (m/s²)² |
| Gyroscope MSD | `< 1` | `>= 1` | (degrees/s)² |
| Gyroscope magnitude | `< 5` | `>= 5` | degrees/s |

A mixture is uncertain. Each block still needs at least nine samples spread
across its interval: first within 150 ms, last at or after 850 ms, and at least
750 ms between first and last. A sample on a boundary belongs to the next
block. Incomplete blocks do not count as quiet or moving.

Low ratio is different from missing evidence. Inadequate baseline/event
coverage still produces `DISTURBANCE_UNKNOWN` and a fresh warmup, not a normal
outcome. Invalid readings and sampling gaps greater than 250 ms invalidate an
unfinished observation. Sensor initialization/I²C faults require attention;
missing readings never count as stillness. Exact coverage checks are in
[fall_detector.h](../CG2028_Assignment/Core/Inc/fall_detector.h).

## Console, saved results and alerts

`DISTURBANCE_LOW` and `DISTURBANCE_CONFIRMED` both use `State=OBSERVING`. Their
diagnostic lines retain `BaselineMSD`, `EventMSD` and `IncreaseRatio`. The new
`MOVEMENT_CONTINUED` event uses `State=NORMAL Alarm=0`; it describes continued
motion after a low-ratio candidate, not a detected near-fall or proof of safety.

The recorder understands the new events and uses the existing
`observed_verdict` field for `MOVEMENT_CONTINUED`. There are no new database or
CSV columns. Full diagnostics remain in the test database's `events` table or
the free database's `detector_events` table. Historical Prototype 2 rejection
events and saved outcomes remain intact. See the
[saved verdict definitions](../data/README.md#reading-the-prototype-verdicts)
for summary priorities and incomplete recordings.

Normal/observing/uncertain/near-fall/continued-movement states retain the
1000 ms LED toggle interval. A possible fall latches the alarm, changes the
toggle interval to **50 ms**, and queues the existing Telegram fall alert.
After **30 consecutive complete quiet seconds following the fall**, `LONG_LIE`
uses two short LED flashes per second and queues the existing escalation.
These demo timings do not establish a person's condition.

Hold the **PC13 user button for two seconds after an alarm**, then release it,
to clear the episode and restart warmup. A button held before the fall is not
a reset request. A board reset also restarts detection. The user button does
not repair a sensor fault. Restarting Python does not reset the board.

## Rebuild and try it

1. Run `python3 tools/build_telegram.py` from the repository root. This updates
   `CG2028_Assignment/BuildTelegram/CG2028_Assignment.elf`; it does not flash it.
   Keep local Telegram/Wi-Fi settings in the existing ignored header.
2. Use the existing **Telegram** CubeIDE launch for that private ELF, with
   **Disable auto build** selected and **Perform build** unchecked. Debug, then press
   **Resume / F8** if execution pauses at `main()`. Do not use the ordinary
   managed **Build Project** action for this firmware. The
   [Mac setup guide](../README.md#3-build-and-flash) has the complete settings.
3. Reset between trials and wait for `READY`. Close other serial viewers, then
   start a 90-second test:

   ```bash
   python3 tools/record_activity.py --test --name prototype-3-trial-1 \
     --verdict fall --duration 90 --notes "Describe baseline, event and subsequent movement"
   ```

4. Perform the intended event after startup, noting when it occurs. Leave
   enough time for the eight-second decision and, when testing Long Lie,
   thirty quiet seconds after the fall decision. The test continues recording
   after an early fall; `--free` instead stops Python at the first fall.

The board sends Telegram alerts directly over Wi-Fi and can continue after
Python stops, but the Mac saves no further readings once the recorder closes.
Changing the firmware requires reflashing; changing only the recorder command
does not install Prototype 3.

## Replay findings

Replay through the compiled C detector retained the original 35 outcomes:
20 recordings without an event, 10 fall decisions and five near-fall decisions.
The 15 running recordings produced 35 spikes, all with `DISTURBANCE_LOW`,
25 `MOVEMENT_CONTINUED` completions and **three false fall decisions**. Seven
recordings ended in `OBSERVING`; their unfinished candidates are not negative
results. The false falls were in calibration sessions 50, 51 and 55 (trial
labels 46, 47 and 50), which simulated ordinary running followed by stopping.
Event totals can exceed recording totals because a recording can contain more
than one candidate.

For the saved Telegram tests, replay of verdict run 11 (`telegram-test-1-fall`)
completed one moving candidate, then detected a fall from a later spike and
eventually reached Long Lie. **Run 12 (`telegram-test-2-fall`) still produced no
fall.** Its second burst happened during the existing observation, so it did
not start another candidate; the original candidate then completed as
`MOVEMENT_CONTINUED` and returned to `NORMAL`. The later quiet period did not
reopen it. Thus this minimal change does not fix that latest missed trial.

These are retrospective results on saved hand-moved board measurements, not
new hardware trials or independent accuracy estimates. Existing recorded
verdicts and the historical Prototype 2 results are unchanged.

Implementation checks on 30 September 2026 passed: 207 Python tests, 20 native
C detector test groups, and the offline Telegram queue/format/retry checks.
The ARM firmware built successfully into the ignored
`CG2028_Assignment/BuildTelegram/CG2028_Assignment.elf`. This build has not been
flashed or tested on the board by these checks.

Reproduce the checks from the repository root:

```bash
python3 tools/replay_fall_detector.py --db data/calibration_readings.sqlite3 --check-calibration
python3 tools/replay_fall_detector.py --db data/calibration_readings.sqlite3 --session 50 --session 51 --session 55 --events
python3 tools/replay_fall_detector.py --db data/prototype_verdicts.sqlite3 --run-id 11 --run-id 12 --events
```

The replay compiles the current C detector using a native compiler and opens
the recording databases read-only. It does not flash the board or send alerts.
Changing the detector or using different recordings can change the results.

## Limitations and comparison trials

Removing the ratio veto may recover a low-ratio fall-like sequence that becomes
quiet later, but it also removes the previous protection against running then
stopping. Such a stop can satisfy the same quiet-block rule and produce a false
fall. Compare both sequences using consistent board placement and movement.

The candidate can also close **before later quiet begins**: three moving blocks
produce `NEAR_FALL` or `MOVEMENT_CONTINUED`. Quiet after that completion does not
retroactively change the result. This change therefore does not guarantee that
a previously missed trial will become a fall, and it adds no new observation
timeout or mechanism for reopening a completed candidate.

Test repeated running-and-stopping controls, sudden movement followed promptly
by stillness, longer movement followed by delayed stillness, and continued
ordinary movement. Inspect the event timeline to distinguish a completed moving
candidate from an uncertain or incomplete observation. Offline replay can show
what the rule does to saved metrics; it cannot replace new board trials or
establish human-fall accuracy.
