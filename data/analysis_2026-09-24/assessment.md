**Assessment of the four initial recordings — 24 September 2026**

The recordings are useful engineering measurements of hand-operated board
movements. They do not yet provide evidence that a human fall can be detected
reliably. Keep them as an exploratory baseline; do not select a final fall
threshold from them.

The operator clarified that all four trials were simulated by moving the board
by hand. Trial 4 involved slow movement, vigorous shaking for approximately
2–3 seconds, holding the board still in the hands, and then slowly placing it on
a table. This is a shaking-and-settling demonstration. No human fall was
recorded. The original activity labels have been preserved in the source files;
they describe intended simulations, rather than verified human activities.

**Files and checks**

- Source: `../activity_readings.sqlite3`, with the CSV checked against it.
- Nonempty sessions: 3, 4, 5 and 6; 300, 299, 299 and 300 rows respectively.
- Total: 1,198 samples, approximately 30 seconds per trial.
- Board intervals: 100 or 101 ms throughout; consecutive sample counters.
- CSV and database agree. All recorded numeric fields are finite and populated.
- Avg and MSD are consistent with XYZ, allowing for printed-value rounding.
- These checks establish internal consistency, not sensor calibration or
  validity of the simulated activity labels.

The original CSV and SQLite files were opened read-only and their SHA-256 hashes
were checked before and after analysis. See [analysis_checks.json](analysis_checks.json).
No firmware, logger, labels, or original measurements were changed.

**What differs between trials**

These are peaks across each complete trial. All measurements below use the
existing EWMA-filtered signals. Units differ between columns: compare values
down a column, not acceleration MSD against gyroscope MSD.

| Hand demonstration | Peak acceleration magnitude (g) | Peak rotation magnitude (degrees/s) | Peak acceleration MSD ((m/s²)²) | Peak gyroscope MSD ((degrees/s)²) |
|---|---:|---:|---:|---:|
| Normal simulation | 1.099 | 54.6 | 0.757 | 173.3 |
| Slow simulation | 1.042 | 16.6 | 0.027 | 22.1 |
| Quick simulation | 1.300 | 93.4 | 12.458 | 1,822.3 |
| Shake, then rest | 1.296 | 75.1 | 19.508 | 3,518.6 |

The quick-motion trial reaches greater acceleration and rotation magnitudes
than the shake trial. Thus, a simple rule that assumes the largest magnitude
indicates the target event is already contradicted by these examples.

The shake trial has higher peak MSD for both sensors. Its main signal
disturbance occurs around 13.4–14.8 seconds after the first saved sample. This
is consistent with the operator's description, but the precise start and end
of shaking were not independently annotated. Quiet motion follows, with more
handling around 19–21 seconds and 23.6–24.1 seconds before a stationary tail.
Do not interpret all later movement as additional falls, or the final rest on
the table as evidence of a person's lying posture.

Windowed MSD also shows an exploratory distinction. The maximum trailing
one-second mean acceleration MSD is 4.543 for quick movement versus 9.202 for
shaking; corresponding gyroscope values are 906.0 versus 1,753.4. Both shaking
peaks occur in the window ending at 14.437 seconds. This does not establish a
threshold: another ordinary handling trial could overlap these values.

[View comparison plot](trial_comparison.png) or [PDF](trial_comparison.pdf).
The plots use the same vertical scales across trials. Shading identifies the
main observed disturbance, not independently measured event boundaries.
[trial_summary.csv](trial_summary.csv) contains additional statistics and peak
times; [derived_readings.csv](derived_readings.csv) contains derived magnitudes
and window means alongside the original fields in a separate analysis copy.

**Which measurements to use next**

1. **Vector magnitude:** `sqrt(X² + Y² + Z²)`, calculated separately for each
   sensor. Unlike `(X + Y + Z) / 3`, opposite-signed axes do not cancel. The
   accelerometer magnitude includes gravity; a stationary board reads about
   9.81 m/s², not zero. Gyroscope magnitude measures the size of angular
   velocity, not the angle the board has turned. Magnitudes are useful inputs,
   but their overlap above rules out treating either as a sufficient detector.
2. **MSD and a short trailing mean of MSD:** keep both acceleration and
   gyroscope changes. Candidate windows of 0.5 and 1 second can describe a
   burst's duration and strength. The formula is
   `MSD = ((X-Xprev)² + (Y-Yprev)² + (Z-Zprev)²) / 3`.
   This is different from variance of samples within a window.
3. **Activity after an event:** measure whether both sensors settle for a
   sustained interval. Measure variability/change, rather than requiring
   acceleration or gyroscope magnitude to be exactly zero. In this recording
   the stationary gyro magnitude is around 1.45 degrees/s, showing an offset.
4. **Orientation before and after:** once a sensor is fixed to the intended
   body location, compare gravity directions over relatively still periods.
   A board rotated in the hand or laid on a table does not establish a human
   posture change. A gyro alone is not an absolute angle measurement.

Treat these as inputs to a sequence of decisions, such as unusual movement,
then possible orientation change and a sustained reduction in activity.
Normal handling can produce the same sequence, and real falls do not all
produce the same peaks or remain motionless afterward. Both missed events and
false alarms must therefore be measured.

The current five-point slopes describe trends over approximately 0.4 seconds.
A pulse that rises and falls within that window can have a near-zero slope.
Use slopes as supporting features rather than the sole event trigger. The
current across-axis Avg is especially sensitive to orientation and cancellation.

A vector rate of acceleration change can be calculated as
`sqrt((ΔX)² + (ΔY)² + (ΔZ)²) / Δt`. For these definitions it equals
`sqrt(3 * MSD) / Δt` before display rounding, so at a fixed interval it largely
re-expresses information already in MSD. Rotation of gravity within board axes
also contributes; this is not isolated translational jerk.

**Sampling and EWMA affect the conclusions**

The current firmware samples approximately ten times per second. EWMA takes
25% of the latest sample and 75% of the previous output. An isolated input spike
initially contributes only 25% of its amplitude. Ignoring integer truncation,
a step reaches about 95% of its final filtered value after 11 updates, roughly
1.1 seconds. Consequently, the saved peaks and slopes describe smoothed motion.
The original unfiltered waveform cannot be recovered exactly from this CSV.

Filtering each axis before taking its magnitude also mixes earlier directions.
A low filtered acceleration magnitude during rapid rotation is not, by itself,
proof of physical free fall.

For the next acquisition experiment, recording unfiltered BSP XYZ alongside
filtered XYZ would allow direct comparison. Sampling around 50 Hz (20 ms) is
a reasonable starting point for examining brief changes; it is a proposal to
test, not a proven minimum rate or guarantee of fall detection. Display and
feature averaging can still use longer windows. If the sampling rate changes,
EWMA's smoothing time and the number of points in a fixed-duration window must
be reconsidered too.

Do not simply change the delay to 20 ms: the present verbose serial packet is
about 307 bytes. At 115200 baud with 8N1 it takes about 26.6 ms to transmit,
before computation. Faster acquisition needs reduced output, faster transport,
or a suitable buffered acquisition/logging design.

The BSP configures the accelerometer at 52 Hz and ±2 g, and the gyroscope at
52 Hz and nominal ±2000 degrees/s. Consecutive integer EWMA outputs suggest
some sampled acceleration inputs approached the ±2 g range during quick
movement and shaking. This is evidence of a possible range limitation, not
proof of analog clipping. Save unfiltered values and check saturation before
choosing the range. The LSM6DSL supports selectable acceleration ranges up to
±16 g; a higher range trades sensitivity for headroom.
[ST LSM6DSL datasheet](https://www.st.com/resource/en/datasheet/lsm6dsl.pdf).

**A practical next experiment**

For a board-based classroom demonstration, first define the claim as detecting
a specified simulated motion followed by settling. Label these runs as hand
simulations in the analysis. For a wearable claim, ordinary-activity data must
come from the board consistently mounted at the intended body location.
Use the lab's approved procedure or an existing research dataset for fall
examples, rather than treating hand shaking as a measured human fall.

As a manageable pilot, record five separate 30-second repetitions of each:
slow movement, quick movement, target shaking-and-settling, and ordinary
handling/table placement. This is 20 short trials; five is a practical starting
number, not a statistically sufficient validation sample size. Add quick
movement followed by a pause, gentle orientation changes, and the assignment's
near-fall/recovery simulation to challenge any candidate rule. For actual
wearable daily activities, sitting, standing, bending, turning and lying down
are useful additional comparisons.

Keep the setup consistent and record mounting/handling, sampling rate, sensor
range, EWMA setting, and actual event start/end times. Video or an event marker
can separate the intended event from later handling. Do not label every row of
a 30-second event-containing recording as an event.

Reserve entire recordings for evaluation before choosing thresholds. For a
small pilot, three repetitions per condition could be used for development
and two held aside; repeat with more independent trials afterward. Do not
randomly split neighboring rows: EWMA and overlapping windows make them highly
related. Report events detected, events missed, false alarms per minute of
ordinary activity, and delay from annotated event onset. The present roughly
90 seconds of other hand movements and one shaking episode cannot establish
a reliable false-alarm rate or human-fall sensitivity.

For context, the SisFall research dataset uses documented activities, repeated
fall simulations and a consistently waist-mounted device. It is a useful
example of the kind of evidence missing from hand-only demonstrations; its
thresholds should not be copied into this setup without matching the sensing
and processing conditions.
[Original SisFall study](https://pmc.ncbi.nlm.nih.gov/articles/PMC5298771/).
Published testing has also found that algorithms performing well on simulated
falls can perform substantially worse on real-world falls.
[Bagalà et al., real-world validation](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0037062).

The existing firmware still sets `fall_detected = 0` in
`CG2028_Assignment/Core/Src/main.c`; no detector is currently making decisions.
The next implementation should follow repeated, annotated measurements rather
than a threshold selected from this single shaking trial.

The active recordings were subsequently migrated from Avg to Magnitude. This
report remains an analysis of the original readings. To reproduce these
historical artifacts from the repository root, explicitly use their backups:

```bash
/opt/anaconda3/bin/python data/analysis_2026-09-24/analyse_trials.py \
  --db data/backups/activity_readings.before-magnitude-20260924T063825Z-e7v6m_qn.sqlite3 \
  --csv data/backups/activity_readings.before-magnitude-20260924T063825Z-9dnre3r5.csv
```

The analysis uses the Anaconda Python already installed on this computer and
its NumPy, pandas and Matplotlib packages. This does not change the recorder's
standard-library-only dependencies.
