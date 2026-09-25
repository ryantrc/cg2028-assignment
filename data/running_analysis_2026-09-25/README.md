# Running simulation analysis — 25 September 2026

The current detector confuses sustained vigorous movement with near-fall recovery. These recordings are useful counterexamples to its existing rule, rather than evidence of successful human near-fall detection. No firmware, thresholds, or recordings were changed for this analysis.

## Sources and method

- Read-only queries of `prototype_verdicts.sqlite3`: `fall-test-7-normal`, run 8 / test session 43.
- Read-only queries of `calibration_readings.sqlite3`: trials 36–40, calibration sessions 38, 39, 41, 42, 43. Trial numbers and session IDs differ.
- User-supplied terminal transcript: `/Users/ryantan/.codex/attachments/071f8fd2-42f4-48bf-882f-d212082aec41/Pasted text.txt`.
- Existing C detector (`CG2028_Assignment/Core/Inc/fall_detector.h`), replayed through `tools/replay_fall_detector.py` against the five new calibration sessions. Replay resets at each saved session; it cannot establish what happened between recordings. Diagnostic report ticks run about 73–74 ms after sample-acquisition ticks in these logs.
- The five new calibration runs have 1,499 samples total, with no sample-counter gaps or >250 ms timestamp gaps. Earlier sessions 27 and 31 contain two stale prefix samples each; comparative metrics use each session's main uninterrupted segment, as documented in `session_metrics.json`.
- Recorded values are EWMA-filtered, approximately 100 ms apart. Whole-session summaries include quiet periods; comparison windows below are aligned to the first acceleration-MSD crossing of 5.

## What happened in fall-test-7

The expected verdict is `normal`, but the observed verdict is `NEAR_FALL`. There are 297 saved samples, two SPIKE events, one NEAR_FALL event, and no POSSIBLE_FALL event or alarm during the captured run.

| Seconds after recorder start (received event) | Recorded event |
|---|---|
| 9.146 | SPIKE; acquisition sample acceleration MSD 6.065177 crosses the threshold of 5 |
| 17.187 | NEAR_FALL; continued movement after the observation window |
| 22.116 | Another SPIKE starts a new observation |
| 30.013 | Recording ends; latest status OBSERVING, Alarm=0 |

The existing summary retains the earlier NEAR_FALL even though another candidate was unfinished at recording end. It is not a claim that all candidates were resolved.

The first trigger's mean acceleration MSD was 1.717 during the preceding 3 seconds, 1.913 during its first 2 seconds, and 1.648 during seconds +5 to +8. This looks like continuing vigorous movement in this capture rather than a brief movement followed by the much weaker recovery captured in the earlier near-fall demonstrations.

## Five new calibration runs

These actual event counts come from the pasted console output; calibration databases retain measurements, not detector diagnostics. Independent C replay of each saved session reproduced three spikes and two near-fall events, with no fall event within the saved interval. Each recording ended with an unfinished observation.

| Trial | Calibration session | Saved samples | Samples with acceleration MSD >=5 | Peak acceleration MSD | Printed near-fall decisions | Final printed state |
|---|---:|---:|---:|---:|---:|---|
| 36 | 38 | 300 | 29 (9.7%) | 10.193 | 2 | OBSERVING, Alarm=0 |
| 37 | 39 | 299 | 55 (18.4%) | 11.167 | 2 | OBSERVING, Alarm=0 |
| 38, completed retry | 41 | 300 | 27 (9.0%) | 16.095 | 2 | OBSERVING, Alarm=0 |
| 39 | 42 | 300 | 37 (12.3%) | 8.069 | 2 | OBSERVING, Alarm=0 |
| 40 | 43 | 300 | 39 (13.0%) | 8.452 | 2 | OBSERVING, Alarm=0 |

The counts of individual threshold samples are not counts of detector alerts. During an observation, later threshold crossings do not create new candidates.

### Trial 37 and the fast LED

Trial 37 ended at 08:46:16.347 UTC with `OBSERVING Alarm=0`, after a final printed SPIKE at board report time 285087 ms. The following aborted Trial 38 / calibration session 40, at 08:46:30.394–08:46:38.162 UTC, ended with `FALL_LATCHED Alarm=1`. That short recording has already been deleted as requested; its earlier backup was inspected read-only, without restoring it.

Thus the alarm was genuinely latched by the later captured status. Its onset and the board's motion in the interval between recordings were not captured. There is no saved POSSIBLE_FALL event proving the exact time. The current firmware's fast LED corresponds to this latch.

A plausible explanation is that recording stopped while a candidate was still being evaluated, then the board became quiet after movement ended. Python stopping does not stop the MCU. The final Trial 37 candidate would reach its first possible decision boundary about 4.6 seconds after the last saved sample. If sufficient quiet blocks followed, the current rule could classify ordinary stopping as a fall. This is an explanation consistent with the evidence, not a reconstruction of unrecorded motion. The user's handling immediately after Trial 37 remains unconfirmed.

## Useful distinctions, and limits

| Group | Individual samples above the trigger per capture | Time from first to last crossing |
|---|---:|---|
| Earlier ordinary activity, trials 1–20 | 0 | None; maximum acceleration MSD 0.523 |
| Earlier staged falls, trials 21–30 | 4–6 | Brief event |
| Earlier staged near-falls, trials 31–35 | 3–10 | Brief event |
| New running simulations, trials 36–40 | 27–55 | 20.5–26.6 seconds |

The older fall and near-fall crossing bursts span roughly 0.4–1.8 seconds. This supports investigating event shape and sustained activity, rather than only peak size.

Ranges of per-session means during seconds +5 to +8 after the first crossing:

| Group | Acceleration MSD, (m/s²)² | Gyroscope MSD, (degrees/s)² | Gyroscope magnitude, degrees/s |
|---|---:|---:|---:|
| Staged falls 21–30 | Approximately 0 | 0.00005–0.0007 | 1.43–1.47 |
| Staged near-falls 31–35 | 0.011–0.052 | 4.46–20.80 | 11.81–18.88 |
| Running simulations 36–40 | 1.492–2.817 | 391.79–662.26 | 34.38–40.30 |

These are descriptive differences in a small set of demonstrations, not validated classification boundaries. Vigorous recovery from a stumble could overlap running in future recordings. Hand-moved board gestures do not establish performance for a body-worn device or older adults.

## Why the rule fails

1. Any single acceleration-MSD value >=5 qualifies as a candidate; it need not be a loss of balance.
2. After ignoring the first 5 seconds for the final still/moving decision, three complete one-second moving blocks yield NEAR_FALL. The moving test has lower limits only: mean acceleration MSD >=0.005, gyro MSD >=1, and gyro magnitude >=5. Running satisfies these conditions.
3. One later acceleration-MSD sample below 5 rearms the detector; a following value >=5 triggers again. The transcript includes re-triggering only 202 ms after a near-fall decision.
4. Three quiet blocks after an arbitrary movement candidate can yield FALL_LATCHED. Ordinary running then stopping or setting the board down can satisfy that pattern.
5. The detector currently uses acceleration MSD, gyro MSD and gyro magnitude. Recorded acceleration magnitude and fitted slopes do not currently affect its decision.

## Recommended next experiment and design

- Keep a recent pre-event history and characterize the first 1–2 seconds of a candidate. Compare the size and shape of the disturbance with the preceding activity, including whether movement is repetitive and whether it quickly returns to its previous pattern. A 2–3-second baseline is a starting hypothesis to test, not a proven setting.
- Only label an event NEAR_FALL after a qualified unusual disturbance followed by recovery. Continued activity following an unqualified threshold crossing should not by itself imply a near-fall. A neutral label such as MOVEMENT_CONTINUED is more accurate until the disturbance classifier is evaluated; relabelling alone does not improve detection.
- Investigate sustained motion intensity as an additional feature because it differs substantially in these recordings. Do not hard-code a running exclusion: genuine falls and near-falls can happen during running.
- Recheck fall confirmation with ordinary run-to-stop and board-set-down examples. Stillness after a generic high-motion candidate is insufficient to establish a fall. Additional event and orientation evidence may help, but each criterion needs testing rather than an assumption that every fall includes it.
- Evaluate running, ordinary stopping, near-fall recovery, and simulated falls as separate classes. Reserve whole new trials for validation instead of splitting adjacent samples from the same trial between tuning and testing. Include running followed by recovery and running followed by a simulated fall.
- Use a longer test recording to capture what happens after movement stops, e.g. `python3 tools/record_activity.py --test --name running-stop-1 --verdict normal --duration 45 --notes "Walk, accelerate, run, then stop normally; record event times"`. Reset before the run. End the intentional movement early enough for at least 8 seconds of subsequent observation while Python still records.

A higher threshold alone is not a clean solution: 16.1 would reject all five new running captures but also miss the trigger in older fall trials 21/22 and near-fall trials 32/34. Adding simultaneous gyro MSD >=500 still leaves a crossing in every new running trial. These figures describe retrospective trigger coverage, not independently measured detector accuracy.

The use of temporal repetition to reject vigorous ordinary activity is also supported by the primary study [Real-Life/Real-Time Elderly Fall Detection with a Triaxial Accelerometer](https://www.mdpi.com/1424-8220/18/4/1101), which analyzes periodicity after threshold crossing. This supports investigating the feature; its results or thresholds cannot be transferred directly to these filtered hand-moved recordings. The [SisFall dataset paper](https://www.mdpi.com/1424-8220/17/1/198) likewise identifies fast jogging among activities that challenge simple thresholds.
