**Assessment of the 20 Level 1–4 recordings — 24 September 2026**

There is meaningful, repeatable separation between the recorded movement
levels, especially in gyroscope MSD and the typical absolute acceleration
magnitude slope. This supports a useful baseline for the board demonstration.
It does not establish human walking-speed classification or fall detection:
the user confirmed Level 1 was recorded with the board resting on a table, and
Levels 2–4 were simulated by moving it by hand. There are no fall trials here.

All 20 sessions contain approximately 30 seconds of data, with five sessions
per level and 5,974 samples overall. All 5,954 intervals within recordings are
100 or 101 ms and sample counters are consecutive. All numeric fields are
finite and populated; CSV and SQLite agree. The sensor calculations are
internally consistent within their printed precision. These are 20 repeated
experiments, not 5,974 independent activity tests.

**The clearest whole-recording differences**

Each range below spans the five trial summaries in that level. A mean MSD is
the average of the MSD column over time within one recording. The absolute
slope statistic first takes the absolute value of each acceleration
MagnitudeSlope, then its median over that recording.

| Level / board demonstration | Mean gyroscope MSD ((degrees/s)²) | Median absolute acceleration MagnitudeSlope ((m/s²)/s) |
|---|---:|---:|
| 1 / resting on table | 0.000052–0.000073 | 0 |
| 2 / slow hand movement | 0.890–1.830 | 0.0748–0.1160 |
| 3 / normal hand movement | 6.086–26.427 | 0.3651–0.7581 |
| 4 / quick hand movement | 55.040–75.040 | 0.9547–1.6093 |

Neither feature's ranges overlap between levels in these 20 trials. The same
ordering remains when the first and last two seconds of each recording are
excluded. This reduces the chance that the whole finding is caused by starting
or stopping handling, but it is still exploratory separation in this dataset.

Gyroscope MSD captures changes in the three angular-velocity components from
one sample to the next. The absolute acceleration magnitude slope describes
how strongly its size is changing, whether increasing or decreasing. Averaging
signed slopes directly can cancel upward and downward changes, so a near-zero
signed average does not imply little motion. No saved slope signs were changed.

The zeros for stationary median absolute acceleration slope mean at least half
the recorded slope values were zero; they do not imply a noiseless sensor.

[Comparison plot](level_comparison.png) · [PDF](level_comparison.pdf) ·
[All 20 trial summaries](per_trial_summary.csv)

**Where the levels overlap**

Magnitude alone provides weaker separation. Trial-median gyroscope magnitude
ranges are 11.888–23.834 degrees/s for Level 3 and 21.324–25.349 degrees/s for
Level 4. Their mean acceleration MSD ranges also overlap: 0.00919–0.04542 and
0.03836–0.06407 (m/s²)² respectively. Thus some normal hand demonstrations
exceed some quick ones on these measurements.

Even the promising mean gyroscope MSD overlaps over shorter intervals:

- Trial 12, Level 3, during 17–18 seconds: **55.771**.
- Trial 18, Level 4, during 21–22 seconds: **23.542**.

A single threshold cannot perfectly separate all these one-second Level 3 and
4 intervals. The full ranges across complete one-second windows are
1.323–55.771 for Level 3 and 23.542–189.056 for Level 4. Their central 90%
intervals are narrower, 4.722–35.078 and 38.787–95.523, but these omit the tails.
These distributions are not classification accuracy estimates.

[Short-window comparison](short_window_overlap.png) ·
[One-second window measurements](one_second_windows.csv)

The window analysis uses nonoverlapping [start, start+1 second) intervals from
each recording's first board tick. It excludes the final partly observed
second: 29 complete windows per trial, 145 per level, 580 total. Each contains
9 or 10 samples. Windows within a recording remain related through the motion
and filter, so they are not independent repetitions.

Level 3 varies substantially across repetitions: trial 12's mean gyro MSD is
26.427 versus trial 11's 6.086, a factor of 4.34. Both retain the same manual
label. Trial 8 also contains a large first-second transient (15.912); removing
the first and last two seconds lowers its mean gyro MSD from 1.354 to 0.701.
These are reasons to standardize the motion and mark setup/handling periods.

**Baseline and measurement limits**

Across the five table-rest trials, acceleration magnitude averages approximately
10.0475 m/s² and gyroscope magnitude approximately 1.4272 degrees/s. Treat these
as measured baselines rather than assuming perfectly stationary readings must
be exactly 9.81 and 0. The stationary gyro mean axis values are approximately
[0.2566, -1.3571, -0.3592] degrees/s. Any later offset correction should operate
on those axes before taking the norm; subtracting 1.4272 from every magnitude
does not generally correct the vector offset. No calibration was performed.

The firmware still uses 100 ms sampling, EWMA alpha 25%, and five-point slopes
spanning about 0.4 seconds. The logger starts after the board is already
running, so early saved slopes can include pre-recording history. Smoothing
attenuates brief peaks, and these data cannot reveal events between readings.
Magnitude is computed after filtering the axes; rapidly changing orientation
can reduce that magnitude without demonstrating physical free fall.

Level 1 is table-supported while Levels 2–4 are hand-supported. Add a held-still
control to learn how much of the stationary/moving distinction reflects hand
support alone. All trials were collected in level order, so interleaving their
order in a later session would reduce confounding from changing technique or
drift. A note mentioning an elderly person is an intended simulation label;
it is not a measurement of that person's gait.

**How to proceed**

Keep these recordings as the non-fall baseline for your hand-operated
demonstration. Gyroscope MSD and absolute acceleration magnitude slope are
promising existing features to compare with upcoming events. One-second
activity summaries may be more stable than individual samples, but do not
require a brief event to remain large for an entire second: retain the original
100 ms readings and examine shorter event peaks as well.

For the next simulated-event recordings, use a repeatable procedure consistent
with the assignment and record when the intended event starts, when it ends,
and when the board is handled afterward. Record multiple independent events
and near-fall/recovery demonstrations, plus ordinary quick movement followed
by stopping or setting the board down. These comparisons test whether a
candidate event rule is responding to an ordinary movement pattern.

All four current levels are labelled non-fall activity. Level 4 alone should
not therefore trigger a fall alarm, and no fall threshold can yet be inferred
from these recordings. Evaluate any later rule on whole recordings not used
to choose it, reporting missed events, false alerts and detection delay. Keep
hand-simulation claims distinct from wearable or real-world fall performance.

Original recordings and firmware were left unchanged. Reproduce these analysis
artifacts from the repository root with:

```bash
/opt/anaconda3/bin/python data/level_analysis_2026-09-24/analyse_levels.py
```

The script deliberately analyzes sessions 1–20 so later fall recordings are not
silently folded into this baseline. Input hashes and checks are recorded in
[analysis_checks.json](analysis_checks.json).
