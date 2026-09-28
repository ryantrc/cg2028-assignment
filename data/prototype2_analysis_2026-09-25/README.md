# Prototype 2 replay, 25 September 2026

The updated detector preserves the original 35 calibration outcomes and rejects
all completed disturbance candidates in the 15 running recordings. Nine running
captures end during an unfinished candidate, so their final candidates have no
decision. This is a retrospective calibration check, not independent accuracy
validation or a hardware test.

The replay compiles the actual `CG2028_Assignment/Core/Inc/fall_detector.h` into a
native C adapter. SQLite is opened read-only. Recorded measurements, sessions,
and actual firmware verdicts are unchanged. The JSON reports contain the exact
header SHA-256, source recording metadata, event record IDs, gate means/ratios,
and final states.

## Results

| Recordings | Prototype 2 replay result |
|---|---|
| Original calibration sessions 1–20 | 20 recordings with no trigger or decision |
| Original fall gestures, sessions 21–30 | 10 confirmed disturbances followed by `FALL` |
| Original near-fall gestures, sessions 31–35 | 5 confirmed disturbances followed by `NEAR_FALL` |
| Running/ramp/run-stop trials 36–50, 15 recordings | 109 provisional spikes: 100 rejected disturbances and 9 unfinished candidates; no confirmed disturbance, `FALL`, or `NEAR_FALL` within the saved data |
| `fall-test-7-normal`, verdict run 8 / original session 43 | Both candidates rejected; ratios 1.1143 and 1.0453; final state `NORMAL`, alarm clear |

Original fall/near-fall decisions occur 8.031–8.073 seconds after the first
crossing. Their disturbance ratios range from 7.1444 to 145.4735. Completed
running candidates have ratios 0.0755–1.9855, below the configured ratio of 4.
These ranges describe this dataset; they are not independently established
classification margins.

All nine unfinished running candidates are in the five ramp trials 36–40 and
continuous-running trials 42–45. Their recordings end in `OBSERVING` before the
two-second gate can finish. The remaining continuous-running trial 41 and all
five run-stop trials 46–50 end in `NORMAL` after rejecting their candidates.
Do not count the nine unfinished final candidates as confirmed negative results.

## What changed and what this replay cannot establish

An acceleration MSD of at least 5 starts a provisional candidate. Prototype 2
compares the first two seconds of that candidate with the preceding three
seconds of acceleration MSD. It uses a baseline floor of 0.01 and requires a
ratio of at least 4 before the existing observation can produce a fall or
near-fall decision. Insufficient history or event coverage produces an unknown
result and a fresh warmup, rather than an ordinary-movement rejection.

Each replay capture starts with a fresh detector and approximately five seconds
of warmup/history collection. It does not inherit a detector alarm or baseline
from before recording began. In particular, trial 48's inherited live alarm is
not reproduced. The two stale prefixes in calibration sessions 27 and 31 cause
timestamp-gap resets rather than being joined to the later recording.

The inputs are saved, rounded EWMA metrics. Replay cannot reconstruct raw sensor
acquisitions, hardware failures not present in those fields, or the caller's
EWMA restart after a gap. The original and new recordings informed this design;
their labels describe staged board movements, including running followed by
holding the board still. They do not validate detection of falls in people.

## Reports and reproduction

- [All 50 calibration recordings](calibration_replay.json)
- [Fall test 7](fall_test_7_replay.json)

From the repository root:

```sh
python3 tools/replay_fall_detector.py --check-calibration
python3 tools/replay_fall_detector.py --db data/prototype_verdicts.sqlite3 --run-id 8 --events
cc -std=c11 -Wall -Wextra -Werror -pedantic -ICG2028_Assignment/Core/Inc tools/test_fall_detector.c -lm -o /tmp/test_fall_detector
/tmp/test_fall_detector
python3 -m unittest discover -s tools -p 'test_replay_fall_detector.py'
```

To export another replay, add `--json /path/to/a/new-report.json`. The tool refuses
to overwrite an existing output. Reports reflect the header currently checked
out; changing thresholds or collecting additional recordings can change results.
