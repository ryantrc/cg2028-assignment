# Level 6 replacement trials: comparison with earlier findings

Checked 24 September 2026. Current Level 6 recordings are sessions/trials 26–30:
retained trial 26 plus replacements 27–30 recorded after 09:08 UTC. These are
different recordings from the deleted all-zero runs that previously used some
of the same session IDs.

**Yes: all five show a large movement burst followed by a quieter period, similar
to Level 5. The all-zero problem is resolved.** Preliminary movement is more
vigorous than in Level 5. Trial 27 has a recording-start timing anomaly; Trial 29
contains renewed movement late in the recording.

## Comparison

Ranges below span five per-trial values, not all individual samples. Peaks are
from the filtered measurements; different metrics can peak at different times.

| Feature | Level 5 | Level 6, including replacements |
|---|---:|---:|
| Peak acceleration magnitude (m/s²) | 12.306–16.509 | 14.819–15.981 |
| Peak acceleration MSD ((m/s²)²) | 14.662–34.909 | 21.261–30.605 |
| Peak gyro magnitude (°/s) | 72.250–186.343 | 99.940–126.959 |
| Peak gyro MSD ((°/s)²) | 1,970.943–7,356.093 | 3,239.783–6,315.515 |
| First 10 seconds: mean acceleration MSD | 0.01033–0.02591 | 0.08180–0.28000 |
| First 10 seconds: mean gyro MSD | 5.126–11.094 | 47.606–124.950 |

The four magnitude/MSD peak ranges lie within the Level 5 ranges. These observations
support similar abrupt events following stronger preliminary motion, rather than
consistently stronger events in Level 6. They do not quantify the severity of a
human fall or demonstrate fall-detector accuracy.

The strongest absolute acceleration-magnitude slopes also broadly overlap:
Level 5 15.20–24.09 m/s³ versus Level 6 14.75–30.81 m/s³. Trial 29 has the largest
absolute slope, but a smaller acceleration-MSD peak than the other Level 6 runs.
Different features describe different aspects of the movement.

| Trial | Peak acceleration MSD | Time of that peak in continuous recording |
|---|---:|---:|
| 26 | 27.111 | 14.118 s |
| 27 | 26.205 | 17.693 s |
| 28 | 28.423 | 13.062 s |
| 29 | 21.261 | 19.670 s |
| 30 | 30.605 | 18.938 s |

Event times are feature-peak times, not externally annotated fall onset.
The exact physical Level 6 procedure has not been independently verified; the
interpretation uses the supplied simulation labels and prior hand-held setup.

## Two qualifications

**Trial 27:** the first two samples (229 and 230) are followed by sample 424.
Board time jumps 19.463 seconds, skipping 193 sample numbers. All three frames
arrived on the computer within one millisecond, consistent with old buffered
frames preceding current data. This does not establish the exact buffering cause
or mean that 19 seconds were lost during the 30-second recording.

The following 300 samples form a continuous 30.042-second segment. Timing plots
and summaries use that segment, starting at record 9155 / board sample 424. The
two initial rows remain untouched in SQLite and CSV. No interpolation or metric
recalculation crosses the gap. Using the original first row as the time origin
would misleadingly place the main peak at 37.256 seconds.

**Trial 29:** after becoming quiet around 24–26 seconds, the board shows renewed
rotation around 27–29 seconds. Gyro magnitude reaches 35.976°/s; the final five
seconds average 7.077°/s, compared with approximately 1.46°/s for the quiet tails
of trials 26, 28 and 30. Trial 27 has a smaller late disturbance, reaching 3.125°/s.
The source of these late movements has not yet been confirmed by the user.
Trial 29 should not be labelled as continuously stationary after the main event.

## What this changes for choosing a detector

The earlier Levels 1–4 maximum acceleration MSD, 0.523263, is not a universal
normal-motion ceiling. In the first ten seconds alone, the five Level 6 runs
reach 0.899, 0.613, 0.647, 2.040 and 0.573 respectively, before their main event.
Using the earlier maximum as an alarm threshold would therefore flag some of
this faster preliminary movement.

The main events still have substantially larger MSD peaks (21.26–30.60), but
the next useful comparison is abrupt non-fall motion and placement, not just
more repetitions of the same event. Evaluate both the short burst and subsequent
motion; include examples with recovery or movement after the event. Choose any
thresholds on development recordings and assess them on fresh whole recordings.

Existing measurement limits remain: values are EWMA-filtered at roughly 10 Hz,
direction averaging can create magnitude dips, and some inferred inputs approach
the configured ±2 g per-axis range. Low filtered magnitude alone does not establish
free fall, nor can the recorded peaks establish physical impact severity.

## Verification and files

- All 1,499 saved Level 6 rows contain a functioning, varying sensor stream;
  there are no all-zero runs. SQLite and CSV agree.
- Apart from Trial 27's one early discontinuity, intervals are 100/101 ms and
  counters are consecutive. Two rows are excluded from timing analysis only.
- An independent audit reproduced observable MSDs and 5,898 slope values within
  firmware output precision, excluding comparisons that require missing history.
- No original recordings, firmware or recorder code were changed.

See [timeline plots](level6_retest_timelines.png), [PDF](level6_retest_timelines.pdf),
[per-trial statistics](per_trial_summary.csv), [quality checks](recording_quality.csv),
[gap details](timestamp_gaps.csv), [input hashes](analysis_checks.json), and
[reproducible script](analyse_retests.py).
