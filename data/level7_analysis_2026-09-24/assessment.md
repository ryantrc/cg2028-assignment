# Level 7: movement after the spike

Checked 24 September 2026. This analysis compares five near-fall-labelled hand
demonstrations (sessions 31–35) with ten fall-labelled demonstrations (21–30).
The user confirmed that Level 7 stayed in the hand throughout the spike and
continued moving afterward, without table contact.

**The intended signal distinction is present:** the Level 7 recordings have a
sharp event followed by sustained movement, while the earlier fall simulations
mostly settle toward table-rest readings. Peak size alone does not distinguish
the two groups. This supports a feature to test, not a validated inference of
human falls, recovery, injury or pain.

## Comparison after allowing the main event to settle

For a common reference, time zero is each recording's largest acceleration-MSD
sample in its longest continuous segment. This uses the completed recording and
is a retrospective alignment method, not an online event detector.

Each trial has a complete **five-second interval from 3 to 8 seconds after that
peak**, with 50 saved samples. Ranges below span per-trial averages: ten fall
simulations versus five near-fall simulations. Individual samples are not treated
as independent repetitions.

| Measurement averaged over that interval | Fall simulations, Levels 5–6 | Near-fall simulations, Level 7 |
|---|---:|---:|
| Gyro magnitude (°/s) | 1.414–2.136 | 11.601–21.445 |
| Gyro MSD ((°/s)²) | 0.000083–0.196019 | 3.548–37.671 |
| Acceleration MSD ((m/s²)²) | 0.0000013–0.0007565 | 0.01004–0.04154 |

The ranges do not overlap in this dataset. Every Level 7 trial remains active
through its final five seconds too: mean gyro magnitude ranges 9.51–15.47°/s.
These are filtered angular-motion readings, not measurements of walking speed.

| Near-fall trial | Peak acceleration MSD | Time of peak in continuous recording | Gyro MSD mean, 3–8 seconds later | Gyro magnitude mean, 3–8 seconds later |
|---|---:|---:|---:|---:|
| 31 | 16.605 | 11.905 s | 37.671 | 16.717°/s |
| 32 | 12.115 | 17.006 s | 3.548 | 11.601°/s |
| 33 | 18.372 | 14.126 s | 22.680 | 21.445°/s |
| 34 | 13.664 | 10.585 s | 17.107 | 12.566°/s |
| 35 | 31.894 | 16.537 s | 17.895 | 16.218°/s |

Peak acceleration MSD overlaps strongly: **12.115–31.894** for Level 7 versus
**14.662–34.909** for Levels 5–6. A larger spike therefore does not establish which
of these two labelled actions happened. The sharp change itself also does not
prove that a person slipped or lost balance.

## Timing and feature choice matter

The first **1–3 seconds** after the peak overlap substantially. Mean gyro MSD is
2.605–259.970 in fall simulations and 8.134–131.077 in near-fall simulations.
Ongoing motion and EWMA settling can therefore make an immediate decision
ambiguous. The 3–8 second interval is an illustrative comparison, not a validated
requirement to delay an alarm by eight seconds.

Using **5–10 seconds** still separates the per-trial means here, but the margin
narrows: fall gyro MSD reaches 2.297, versus near-fall minimum 3.040. Fall-labelled
Trial 29 moves again late in the recording, demonstrating that a fall label and
later movement already coexist in this dataset.

Recovery movement is often less vigorous than the preliminary motion. Level 7's
3–8 second gyro-MSD means are about 33–94% of their first-ten-second means. A rule
requiring return to the original intensity could miss these demonstrations.

One quiet sample is insufficient: Trial 32 briefly has gyro magnitude 0.820°/s
despite clear continued movement overall. Conversely, acceleration magnitude
near gravity does not establish stillness: the Level 7 final means remain around
9.90–9.93 m/s² while the gyroscope continues responding. Windowed gyro magnitude
and acceleration/gyro MSD are useful complementary features; MSD measures changes
between samples and is not a complete measure of all motion by itself.

## What can and cannot be inferred

The demonstrated contrast is **held and moving versus placed on a table and
quiet**. Those handling choices are tied to the labels, so the data do not yet
establish human fall versus near-fall discrimination. A signal can show low motion
without identifying the floor, pain or loss of consciousness. Continued motion
alone does not establish that no fall occurred or that balance was recovered.

A primary study of real-world falls found that some falls did not end with the
person lying on the floor, and mandatory posture checks could miss events.
This supports treating post-event posture/inactivity as evidence rather than a
definition of every fall: [Bagalà et al., 2012](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0037062).

For a prototype, a useful structure is: identify a candidate abrupt event, observe
motion afterward, and retain outcomes such as **recovery-like movement**, **possible
fall with sustained low motion**, or **uncertain**. Choose thresholds and observation
times on development trials, then assess fresh complete recordings. The current
comparison supplies no sensitivity, specificity or accuracy estimate.

The most informative next hand simulations challenge the distinction directly:

1. A recovery-labelled event followed by a 2–5 second pause, then movement.
2. A fall-labelled event followed by small movements or an attempted recovery.
3. An ordinary quick lift or table placement followed by rest.
4. Similar abrupt movements followed by progressively slower movement.

These remain labelled signal demonstrations. Testing the intended wearable
placement and suitable human datasets is a separate validation step; hand trials
alone cannot establish performance for an older wearer.

## Quality checks and artifacts

- All 1,497 new rows contain a nonzero sensor stream; SQLite and CSV agree.
- Trial 31 begins with two stale-looking frames, then an 84.577-second board-time
  jump. All three initial frames arrived within 1 ms on the computer. Its main
  299-frame segment spans 30.060 seconds; the two early rows are excluded only
  from timing analysis. Trial 27 receives the same previously documented handling.
- Sessions 32–35 have consecutive sample counters and 100/101 ms intervals.
- An independent audit found magnitudes, observable MSDs and 5,890 observable
  new slopes consistent with the firmware within printed precision. Comparisons
  requiring missing history were excluded.
- Existing EWMA and possible ±2 g per-axis range limitations remain. Raw inputs
  were not independently recorded; magnitude dips do not establish free fall.
- Original recordings, firmware and recorder code were not changed.

See [event-aligned plot](post_event_comparison.png), [PDF](post_event_comparison.pdf),
[per-trial summaries](per_trial_summary.csv), [post-event windows](post_event_windows.csv),
[one-second means](event_aligned_one_second_means.csv), [quality](recording_quality.csv),
[input hashes](analysis_checks.json), and [reproducible script](analyse_level7.py).
