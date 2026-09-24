# Level 6: only one usable motion recording

**Historical audit:** the zero recordings discussed below were subsequently
deleted and the session counter reset. For the replacement trials recorded after
09:08 UTC, see the [new assessment](../level6_retest_analysis_2026-09-24/assessment.md).
The old conclusions below do not describe those replacement recordings.

Checked 24 September 2026. Five Level 6 sessions remain: 26, 28, 29, 30 and 31.
Deleted session 27 is still absent. The trial numbers in the notes differ from
database session IDs after that deletion.

| Database session | Trial in notes | Samples | Finding |
|---|---|---:|---|
| 26 | 26 | 299 | Nonzero motion, large burst, then stillness |
| 28 | 27 | 302 | All sensor axes and derived metrics zero throughout |
| 29 | 28 | 302 | All sensor axes and derived metrics zero throughout |
| 30 | 29 | 302 | All sensor axes and derived metrics zero throughout |
| 31 | 30 | 301 | All sensor axes and derived metrics zero throughout |

**Session 26 lines up with the Level 5 pattern. The other four cannot be used to
assess the simulated activity.** Their 1,207 rows have literal zero values in all
14 sensor/derived fields, not blank cells or missing numbers. SQLite and CSV agree.
Sample counters and timestamps advance, with no gaps. A working table-rest
recording in this dataset has acceleration magnitude near 10 m/s², not zero.

## The usable recording

| Metric | Level 5: range of five trial peaks | Session 26 peak |
|---|---:|---:|
| Acceleration magnitude (m/s²) | 12.306–16.509 | 15.981 |
| Acceleration MSD ((m/s²)²) | 14.662–34.909 | 27.111 |
| Gyroscope magnitude (°/s) | 72.250–186.343 | 126.959 |
| Gyroscope MSD ((°/s)²) | 1,970.943–7,356.093 | 5,196.860 |

The main burst is around 14 seconds; acceleration MSD peaks at 14.118 seconds.
The recording then settles near table-rest readings. Over seconds 20–30, mean
gyro magnitude is about 1.457°/s. These values are EWMA-filtered.

Before the burst, the first-ten-second mean gyro MSD is 48.226, compared with
5.126–11.094 across Level 5. Acceleration MSD is 0.13464 versus 0.01033–0.02591.
This is consistent with more vigorous preliminary movement, although the exact
Level 6 simulation method has not yet been confirmed by the user.

Only one usable Level 6 recording is available. It supports the earlier observed
motion sequence but cannot establish repeatability across five Level 6 trials or
reliable human fall detection.

## Why the zero recordings need investigation

The board counter/time reset between session 26 and session 28: the first saved
board time goes from 6,002,580 ms to 32,507 ms. This is consistent with a restart
around the earlier cable-disconnection incident, but does not prove why readings
became zero.

There is a plausible firmware path: [main.c](../../CG2028_Assignment/Core/Src/main.c)
calls both sensor initialization functions without checking their return values
(lines 41–42), and initializes read buffers to zero each loop (lines 77–78).
If initial device identification fails, the BSP driver pointer can remain null;
the subsequent read wrapper then leaves the buffer unchanged. The program can
therefore continue printing zeros despite a sensor initialization failure.
That is a code-supported possibility, not a confirmed diagnosis of these runs.

The recorder parses the numbers actually supplied in complete sensor frames; it
does not synthesize missing axes as zero. There is no evidence that this is merely
a spreadsheet display issue.

Stop debugging, secure the connection and start a fresh debug/run so initialization
executes again. Before collecting replacements, check a short recording: with the
board resting flat, acceleration magnitude should resemble the earlier ~10 m/s²;
moving/turning the board should change the axes and gyro readings. If zeros persist,
inspect the sensor initialization return values in CubeIDE before more trials.

No recordings were deleted or changed, and no firmware/recorder changes were made.
The [quality table](recording_quality.csv), [comparison](session26_comparison.csv),
[input checks](analysis_checks.json), and [script](analyse_level6.py) are saved here.
