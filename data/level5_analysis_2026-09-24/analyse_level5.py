"""Read-only descriptive comparison of sessions 21–25 with sessions 1–20.

Run with /opt/anaconda3/bin/python (numpy, pandas, matplotlib).
The event bounds below are observed exceedances, not labelled fall boundaries
or validated alarm thresholds. Original recordings are never modified.
"""
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cg2028-mpl"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
DATA = OUT.parent
inputs = [DATA / "activity_readings.sqlite3", DATA / "activity_readings.csv"]
hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
with sqlite3.connect(inputs[0].as_uri() + "?mode=ro", uri=True) as connection:
    all_rows = pd.read_sql_query("SELECT * FROM readings ORDER BY session_id, record_id", connection)
pd.testing.assert_frame_equal(all_rows, pd.read_csv(inputs[1]), check_dtype=False)
frame = all_rows[all_rows.session_id.between(1, 25)].copy()
assert frame.session_id.nunique() == 25
assert np.isfinite(frame.select_dtypes(include=np.number)).all().all()
baseline = frame[frame.session_id <= 20]
new = frame[frame.session_id >= 21]
metrics = ["accel_magnitude_mps2", "gyro_magnitude_dps", "accel_msd", "gyro_msd",
           "accel_magnitude_slope", "gyro_magnitude_slope", "accel_msd_slope", "gyro_msd_slope"]
baseline[metrics].agg(["min", "max"]).to_csv(OUT / "previous_levels_observed_bounds.csv")
rows, windows, quality = [], [], []
for sid, group in frame.groupby("session_id", sort=True):
    t = (group.board_time_ms.to_numpy(dtype=np.int64) - int(group.board_time_ms.iloc[0])) / 1000
    delta_ms = np.diff(group.board_time_ms)
    assert np.all(np.diff(group.sample_number) == 1)
    assert np.isin(delta_ms, [100, 101]).all()
    quality.append(dict(session_id=int(sid), samples=len(group), span_s=t[-1],
                        min_interval_ms=int(delta_ms.min()), max_interval_ms=int(delta_ms.max()),
                        mean_interval_ms=float(delta_ms.mean()), missing_sample_counters=0))
    row = dict(session_id=int(sid), level=(int(sid) - 1) // 5 + 1,
               activity=group.activity.iloc[0], notes=group.notes.iloc[0], samples=len(group))
    for key in metrics:
        values = group[key].to_numpy()
        row[key + "_mean"] = float(values.mean())
        row[key + "_min"] = float(values.min())
        row[key + "_max"] = float(values.max())
        row[key + "_peak_time_s"] = float(t[values.argmax()])
        if "slope" in key:
            row[key + "_absolute_max"] = float(np.abs(values).max())
    for prefix, mask in [("first_10s", t < 10), ("20_to_30s", (t >= 20) & (t < 30))]:
        for key in metrics[:4]:
            row[prefix + "_" + key + "_mean"] = float(group.loc[mask, key].mean())
    if sid >= 21:
        exceeds = t[group.accel_msd.to_numpy() > baseline.accel_msd.max()]
        row["accel_msd_exceeds_previous_max_first_s"] = float(exceeds[0]) if len(exceeds) else None
        row["accel_msd_exceeds_previous_max_last_s"] = float(exceeds[-1]) if len(exceeds) else None
        row["accel_msd_exceeds_previous_max_samples"] = len(exceeds)
    rows.append(row)
    # Complete [start,start+1s) bins only; omit the final partial second.
    for second in range(int(t[-1] // 1)):
        w = group[(t >= second) & (t < second + 1)]
        window = dict(session_id=int(sid), level=row["level"], start_s=second, end_s=second + 1,
                      samples=len(w))
        for key in metrics[:4]:
            window[key + "_mean"] = float(w[key].mean())
            window[key + "_max"] = float(w[key].max())
        windows.append(window)

summary = pd.DataFrame(rows)
summary.to_csv(OUT / "per_trial_summary.csv", index=False)
pd.DataFrame(windows).to_csv(OUT / "one_second_windows.csv", index=False)
pd.DataFrame(quality).to_csv(OUT / "recording_quality.csv", index=False)

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
fig, axes = plt.subplots(3, 5, figsize=(16, 9.5), sharex=True, sharey="row")
panels = [("accel_magnitude_mps2", "Acceleration magnitude\n(m/s²)", None),
          ("accel_msd", "Acceleration MSD\n(m/s²)² — log scale", 1e-7),
          ("gyro_msd", "Gyroscope MSD\n(degrees/s)² — log scale", 1e-6)]
for column, (sid, group) in enumerate(new.groupby("session_id", sort=True)):
    t = (group.board_time_ms - group.board_time_ms.iloc[0]) / 1000
    for row_index, (key, label, floor) in enumerate(panels):
        ax = axes[row_index, column]
        values = group[key].to_numpy()
        if floor is None:
            ax.axhspan(baseline[key].min(), baseline[key].max(), color="#94a3b8", alpha=.25)
            ax.set_ylim(3.8, 17.5)
        else:
            values = np.maximum(values, floor)
            ax.set_yscale("log")
            ax.set_ylim(floor, 1e2 if key == "accel_msd" else 1e4)
        ax.plot(t, values, color="#087f8c", linewidth=1.05)
        ax.axhline(baseline[key].max(), color="#c13d38", linestyle="--", linewidth=1)
        ax.set_xlim(0, 30.2)
        ax.set_xticks([0, 10, 20, 30])
        ax.grid(axis="y", alpha=.18)
        if column == 0:
            ax.set_ylabel(label)
        if row_index == 0:
            ax.set_title(f"Trial {sid}", fontweight="bold")
        if row_index == 2:
            ax.set_xlabel("Seconds from first saved sample")
fig.suptitle("Level 5: five brief movement bursts followed by a quiet tail", fontsize=17, y=.97)
fig.text(.5, .045, "Red dashed line: highest sample previously recorded across all Levels 1–4 (descriptive comparison, not an alarm threshold).\n"
         "Grey band: previous acceleration magnitude range. MSD log plots clip zero/tiny values to the axis minimum for display only.\n"
         "All values are EWMA-filtered; these board demonstrations do not establish human fall-detection accuracy.",
         ha="center", fontsize=10)
fig.subplots_adjust(left=.075, right=.99, top=.90, bottom=.17, hspace=.26, wspace=.12)
fig.savefig(OUT / "level5_timelines.png", dpi=160)
fig.savefig(OUT / "level5_timelines.pdf")
plt.close(fig)

assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}, "Inputs changed during analysis"
manifest = dict(input_sha256=hashes, total_sessions_compared=25, total_samples_compared=len(frame),
                new_sessions=5, new_samples=len(new), csv_matches_database=True,
                all_values_finite=True, all_intervals_100_or_101_ms=True,
                all_sample_counters_consecutive=True, source_files_unchanged=True,
                unit_of_repetition="One recording, five Level 5 recordings; individual samples are not independent trials.",
                confirmed_setup="Level1 table rest; Levels2–4 hand movement. Level5 normal hand motion, fast upward lift, downward placement on table, then rest, around halfway through each run.",
                event_caveat="Exceedance times are calculated from the data, not externally annotated fall start/end times.",
                filter="Existing integer EWMA alpha25%, ~10Hz sampling, five-point slopes; no configuration changes.")
(OUT / "analysis_checks.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(summary[summary.level == 5][["session_id", "accel_magnitude_mps2_max", "accel_msd_max", "gyro_magnitude_dps_max", "gyro_msd_max"]].to_string(index=False))
print("Saved analysis only:", OUT)
