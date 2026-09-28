# Trials 41–50: continuous running and ordinary stopping

Analysis date: 25 September 2026. Only new analysis artifacts were created; firmware and existing recordings were not changed.

## Main findings

The new recordings confirm two distinct failure modes: ordinary running is labelled NEAR_FALL, and running followed by holding the board still can produce a false fall alarm. The user confirmed that trials 46–50 were simulated by moving the board by hand, then holding it still, without putting it on the table.

There are ten completed recordings with 3,001 saved sensor pairs in total. Each contains 299–301 pairs; every consecutive board timestamp differs by 100–101 ms. There are no sample-counter discontinuities, timestamp resets or gaps, or missing saved metric fields. This does not suggest a serial-loss explanation for the different detector results.

Trial numbers come from the notes, not session IDs. The completed replacements for Trials 44 and 49 are sessions 48 and 54. Their aborted earlier attempts (sessions 47 and 53) were already deleted and are excluded.

## Actual recorded decisions

These decisions come from the supplied terminal transcript. Calibration SQLite stores measurements, not detector events; the transcript is necessary to establish what the running firmware actually reported.

| Trial | Session | Intended activity | Actual events during recording | Final state |
|---|---:|---|---|---|
| 41 | 44 | Continuous running | 1 NEAR_FALL | NORMAL, Alarm=0 |
| 42 | 45 | Continuous running | 3 NEAR_FALL | OBSERVING, Alarm=0 |
| 43 | 46 | Continuous running | 3 NEAR_FALL | OBSERVING, Alarm=0 |
| 44 | 48 | Continuous running | 3 NEAR_FALL | OBSERVING, Alarm=0 |
| 45 | 49 | Continuous running | 3 NEAR_FALL | OBSERVING, Alarm=0 |
| 46 | 50 | Running then holding still | 2 NEAR_FALL | NORMAL, Alarm=0 |
| 47 | 51 | Running then holding still | NEAR_FALL, UNCERTAIN, POSSIBLE_FALL | FALL_LATCHED, Alarm=1 |
| 48 | 52 | Running then holding still | No new decision printed; inherited latched alarm | FALL_LATCHED, Alarm=1 |
| 49 | 54 | Running then holding still | NEAR_FALL, POSSIBLE_FALL | FALL_LATCHED, Alarm=1 |
| 50 | 55 | Running then holding still | 2 NEAR_FALL | NORMAL, Alarm=0 |

Thus all five continuous-running trials generated false near-fall labels, with 13 such messages in total. Four also ended with another observation pending, whose later outcome is outside the recording.

Trials 47 and 49 contain explicit new POSSIBLE_FALL events despite the intended ordinary running-to-stopping action: these are confirmed false fall alarms for the staged activity. Trial 48 followed Trial 47 on the same MCU boot: board timestamps continued from 203079 ms to 208981 ms, and the latch was not reset. It provides useful movement data but no independent new classification. Do not count its final Alarm=1 as a third detected fall.

Two of the four run-to-stop recordings that supplied fresh classifications raised fall alarms. This small, controlled count is descriptive and is not an estimate of real-world accuracy or an older person's fall risk.

## Why similar stops gave different decisions

The code starts a candidate at acceleration MSD >=5. It waits five seconds, then classifies one-second blocks. Three consecutive moving blocks produce NEAR_FALL and close the candidate; three consecutive quiet blocks produce a latched fall. If the first three blocks do not resolve the candidate, UNCERTAIN continues checking subsequent blocks.

The following times use acquisition timestamps relative to the first saved sample. Printed diagnostic timestamps arrive 72–74 ms later. Exact means and timing are in `event_blocks.json`.

| Trial | Second candidate began | Block sequence from trigger+5s | Outcome |
|---|---:|---|---|
| 46 | 8.955 s | Moving, moving, moving | NEAR_FALL at 17.003 s |
| 47 | 9.353 s | Moving, moving, quiet; then moving, moving, quiet, quiet, quiet | UNCERTAIN at 17.408 s; false fall at 22.449 s |
| 49 | 10.776 s | Quiet, quiet, quiet | False fall at 18.824 s |
| 50 | 9.268 s | Moving, moving, moving | NEAR_FALL at 17.326 s |

For Trials 46 and 50, the candidate had already closed by the time later stillness arrived. No subsequent acceleration-MSD reading crossed 5, so quietness could not initiate another evaluation. Their lack of a fall alarm does not establish that the program successfully recognized ordinary stopping.

For Trial 49, the three decision blocks were already quiet. Their mean (acceleration MSD, gyro MSD, gyro magnitude) values were approximately (0.000282, 0.102, 1.73), (0.000163, 0.055, 1.62), and (0.000824, 0.338, 1.65). All satisfy the current quiet limits of 0.005, 1, and 5 respectively.

Trial 47 illustrates that eight seconds is the earliest decision point, not a hard maximum. Its second candidate took approximately 13.1 seconds to reach a false fall decision after an uncertain period. The early decision blocks alternated moving and quiet; later three quiet blocks satisfied the rule.

A native C replay using the actual `fall_detector.h`, with the detector initially monitoring to match an MCU already running before Python connected, reproduces every listed event for these four recordings. A replay that blindly resets to warmup at each recording start would change early candidate timing and must not be substituted for the actual terminal evidence.

## Additional measurement findings

| Trial | Samples with acceleration MSD >=5 | Percentage | Peak acceleration MSD |
|---|---:|---:|---:|
| 41 | 5 | 1.7% | 6.151 |
| 42 | 78 | 26.0% | 12.389 |
| 43 | 109 | 36.5% | 16.446 |
| 44 | 103 | 34.3% | 11.080 |
| 45 | 86 | 28.8% | 10.842 |
| 46 | 68 | 22.6% | 14.415 |
| 47 | 60 | 19.9% | 13.500 |
| 48 | 65 | 21.7% | 17.946 |
| 49 | 37 | 12.3% | 19.107 |
| 50 | 40 | 13.3% | 8.586 |

These are individual samples, not detector events. Acceleration MSD is in (m/s²)² and uses EWMA-filtered axes.

The new data limits a previous promising idea: spike count alone cannot identify running. Trial 41 has only five threshold crossings, comparable to the earlier staged falls' four to six. Nevertheless, its mean acceleration MSD stays elevated: about 1.55 in its first ten seconds and 2.28 in its last ten seconds. A temporal description of the broader movement is more informative than counting readings above one threshold.

Holding a board still is not always numerically motionless. Trial 46 has late measured hand movement whose metrics overlap the earlier near-fall recovery examples. The other held-still trials contain substantial quiet periods. Neither quietness nor a small hand adjustment establishes whether someone fell or recovered balance.

Raising the trigger above all observed new running peaks would not provide a clean separation. At 20, these running gestures would not trigger, but two of the ten older staged falls and four of the five older staged near-falls also would not trigger. This is retrospective trigger coverage, not full detector validation.

## What to change or test next

1. Qualify an unusual disturbance relative to recent ongoing activity before calling it a fall/near-fall candidate. Examine the initial motion and preceding baseline, rather than treating any acceleration-MSD crossing as evidence of loss of balance. Sustained intensity and repetition are features to investigate, not proven class boundaries.
2. Require more than quietness after an arbitrary running spike for a fall decision. Changing the eight-second delay, tightening quiet thresholds, or merely renaming NEAR_FALL shifts or hides symptoms without fixing this causal ambiguity. Genuine falls can also occur during running, so do not disable detection whenever movement is vigorous.
3. Use the new ordinary running-to-stop trials as explicit non-fall examples when evaluating changes. Retain Trial 48's measurements, but exclude its inherited alarm from counts of fresh detector decisions.
4. Reset the board before each independent test. Start recording before movement, leaving about five seconds of initial stillness to capture a baseline. Nine of these ten captures cross the trigger within their first 2.2 seconds, so the stored data often lacks a full pre-trigger history.
5. Vary stop timing and annotate it. Keep recording long enough after stopping to see any later decision. For scoring the detector, use test mode with expected normal so diagnostics are saved automatically:

```bash
python3 tools/record_activity.py --test --name run-stop-1 --verdict normal   --duration 45 --notes "Hand-held; run then hold still; note movement start and stop times"
```

Hold out whole fresh trials when checking a revised rule. These hand-moved demonstrations can expose implementation weaknesses, but cannot establish performance for real body-worn running, near-falls, or falls.

## Files and evidence

- `summary.csv`: one row per completed new trial, measurement and actual event counts.
- `new_trial_metrics.json`: detailed quality checks and descriptive windows for these ten trials.
- `terminal_decisions.json`: events extracted from the supplied terminal transcript, matched to retained sessions.
- `event_blocks.json`: exact acquisition alignment, one-second block means, and C verification for Trials 46, 47, 49 and 50.
- Measurements: `../calibration_readings.sqlite3`, opened read-only.
- Transcript: `/Users/ryantan/.codex/attachments/1071cb6e-aa0a-4ea9-b97b-ba92a060c869/Pasted text.txt`.
