# Level 5 recordings: what the data support

Analysis date: 24 September 2026. New recordings: sessions/trials 21–25,
1,495 samples. Comparison: sessions 1–20, 5,974 samples.

**Finding:** every Level 5 run has a strong, brief movement event followed by
extended stillness. Acceleration MSD gives a particularly large separation from
the previous hand-movement recordings. These data support detecting the
demonstrated motion sequence; they do not establish reliable human fall detection.

The user confirmed the procedure: move the board normally, quickly lift it high
by hand around halfway through the recording, then bring it down onto the table.
Level 1 was table rest; Levels 2–4 were hand simulations of slow, normal and quick
walking. Thus Level 5 is a fast lift-and-place demonstration. The data alone cannot
separate the lift, reversal, descent and table contact without event annotations.

## Observed separation

All values below come from the saved **EWMA-filtered** readings. The previous
maximum is the largest individual sample across all 20 earlier trials, not the
largest trial average. Acceleration MSD units are (m/s²)²; gyro MSD units are (°/s)².

| Trial | Peak acceleration magnitude (m/s²) | Peak acceleration MSD | Time of acceleration MSD peak (s) | Peak gyro magnitude (°/s) | Peak gyro MSD |
|---|---:|---:|---:|---:|---:|
| Previous Levels 1–4 maximum | 10.924 | 0.523 | — | 63.264 | 556.741 |
| 21 | 12.306 | 15.268 | 12.332 | 72.250 | 1,970.943 |
| 22 | 13.509 | 14.662 | 14.996 | 186.343 | 3,087.071 |
| 23 | 16.509 | 34.909 | 12.487 | 151.052 | 7,356.093 |
| 24 | 15.433 | 27.410 | 14.000 | 117.677 | 5,061.845 |
| 25 | 15.356 | 27.636 | 13.998 | 119.288 | 6,818.385 |

Times are measured from each run's first saved board timestamp. Different metrics
can peak at different times; table entries do not describe one simultaneous sample.

- Every new acceleration-MSD peak is **28–67 times** the previous maximum.
- Peak one-second mean acceleration MSD is **4.705–10.508** across the five
  new trials, versus a previous maximum of **0.200445**. The separation survives
  averaging into complete one-second bins, though such bins can delay detection.
- Gyro-MSD peaks are **3.54–13.21 times** the previous maximum.
- Peak absolute acceleration-magnitude slopes are **15.20–24.09 m/s³**, versus
  **5.30 m/s³** previously. Slopes also respond clearly, but MSD shows a larger
  observed relative separation in these trials. That is not a comparative accuracy test.
- Peak gyro magnitude alone has a narrower margin: Trial 21 reaches **72.25°/s**,
  only about 14% above the previous **63.264°/s** maximum.

MSD here means `((ΔX)² + (ΔY)² + (ΔZ)²) / 3` between successive filtered samples.
It is not the squared difference between consecutive vector magnitudes.

## Timing and the quiet tail

Acceleration MSD first exceeds the previous maximum at about 11.83, 14.09, 12.09,
13.70 and 13.70 seconds in Trials 21–25 respectively. These are observed exceedance
times, not verified fall-onset times or a proposed alarm threshold.

All five runs become very quiet afterward. Over seconds 20–30, mean gyro magnitude
is **1.437–1.458°/s**, close to the earlier stationary offset. Mean acceleration
vectors are approximately `[0, 0, 10.04] m/s²`, consistent with the confirmed flat
table placement. A stationary gyro does not have to read exactly zero.

Trial 22 differs: it has a second rotation burst around **16.8–17.3 seconds** and
settles around **19–20 seconds**. The other runs approach stationary readings around
16–18 seconds. This variation matters when choosing how long an algorithm waits
for stillness; these times include EWMA settling, not just physical motion.

Whole-recording mean gyro MSD is **44.06–80.91** for Level 5, overlapping Level 4's
**55.04–75.04**. Half a recording being quiet can dilute a short event. Detecting a
sequence of abrupt movement followed by low motion is more consistent with these
waveforms than ranking the whole recording by one mean. Stillness alone cannot
distinguish a fall from rest or putting the board down.

## Measurement limitations

The existing configuration remains ~10 Hz recording, 25% EWMA and five-point
slopes. Firmware averages each axis first and then computes magnitude. Rapid
direction changes can partly cancel within those filtered axes, causing magnitude
dips. The Level 5 minima of **4.678–6.305 m/s²** must not be interpreted as proof of
free fall. EWMA also reduces and spreads brief peaks, and events between the
100 ms samples cannot be reconstructed from these logs.

The accelerometer BSP configures **±2 g per axis**. Inverting the known integer
EWMA within its rounding bounds suggests sampled inputs at or near that range
during these events. Raw sensor counts were not saved, so clipping and the true
peak above the range cannot be conclusively quantified. Filtered peak magnitudes
therefore should not be interpreted as physical impact-force measurements.

One concrete example is Trial 21, samples 27156–27157: filtered Z changes from
7.159 to 10.268 m/s², corresponding to 730 to 1047 integer mg. With alpha 25%,
`1047 = trunc((input + 3*730)/4)` requires input 1998–2001 mg. The configured
driver's ±1998 mg integer output bound leaves 1998 mg. This indicates a near-range
sample; the integer conversion cannot distinguish just-below-limit from clipping.
See the [range configuration](../../CG2028_Assignment/Drivers/BSP/B-L4S5I-IOT01/stm32l4s5i_iot01_accelero.c),
[sensor conversion](../../CG2028_Assignment/Drivers/BSP/Components/lsm6dsl/lsm6dsl.c),
and [EWMA implementation](../../CG2028_Assignment/Core/Src/mov_avg.s).

Final orientation is a tabletop orientation, not evidence of a person's lying
posture. These are five repetitions by the same operator using one scripted
procedure. The 1,495 samples are not 1,495 independent fall trials. No sensitivity,
specificity or accuracy is estimated here, and no threshold has been validated.

## Useful next recordings

1. Add non-fall comparisons that could imitate the same sequence: quick movement
   then stopping, fast lifting and lowering without table contact, normal placement
   on the table, and abrupt motion followed by continued movement. Repeat each;
   label the actual action separately from its intended fall/non-fall category.
2. Keep placement and handling consistent. Hand motion can test an algorithm's
   response, but validating walking or falls requires data from the intended
   mounting setup and a suitable labelled dataset. There is no need to perform
   a human fall to continue this board experiment.
3. Record approximate event times, including the lift and table placement, so
   an eventual alarm can be matched to the action that triggered it.
4. Develop a candidate that considers a brief acceleration-MSD/magnitude event
   and subsequent low motion. Choose parameters on development recordings, then
   evaluate fresh whole recordings, including the non-fall comparisons. Do not
   split neighbouring samples from one run between training and testing.
5. If firmware changes are made later, saving both raw and filtered axes would
   help distinguish physical peaks from filter effects. Sensor range, sampling
   and filtering should be reviewed together before collecting a new dataset;
   a range change also requires checking the sensor conversion scale.

## Files and verification

- [Timeline plots](level5_timelines.png), also available as [PDF](level5_timelines.pdf).
- [Per-trial summaries](per_trial_summary.csv): all 25 recordings.
- [One-second windows](one_second_windows.csv): complete nonoverlapping bins
  relative to each trial's first sample; incomplete final bins are omitted.
- [Recording quality](recording_quality.csv) and [input hashes](analysis_checks.json).
- [Reproducible analysis script](analyse_level5.py).

Each new run has 299 samples, no sample-counter gaps and only 100/101 ms board
intervals. SQLite and CSV agree; numeric fields are finite. The independent
firmware audit reproduced magnitudes, MSDs and 5,890 fully observable new slope
values within printed precision. Initial slope histories can include samples
before recording began; the central events occur well after that boundary.

Source recordings, firmware and recorder were not modified. Only this analysis
directory was created.
