"""Read-only comparison of replacement Level 6 recordings with Levels 1–5.

Run with /opt/anaconda3/bin/python (numpy, pandas, matplotlib).
Split recordings at counter/timing discontinuities. Use each run's longest
continuous segment for timing comparisons, without deleting original samples.
"""
from pathlib import Path
import hashlib
import json
import os
import re
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
with sqlite3.connect(inputs[0].as_uri() + "?mode=ro", uri=True) as c:
    all_rows = pd.read_sql_query("SELECT * FROM readings ORDER BY record_id", c)
    sessions = pd.read_sql_query("SELECT * FROM sessions ORDER BY id", c)
pd.testing.assert_frame_equal(all_rows, pd.read_csv(inputs[1]), check_dtype=False)
frame = all_rows[all_rows.session_id.between(1, 30)]
assert frame.session_id.nunique() == 30
assert np.isfinite(frame.select_dtypes(include=np.number)).all().all()
metrics = ["accel_magnitude_mps2", "gyro_magnitude_dps", "accel_msd", "gyro_msd",
           "accel_magnitude_slope", "gyro_magnitude_slope", "accel_msd_slope", "gyro_msd_slope"]
rows, quality, gap_rows = [], [], []
segments = {}
for sid, group in frame.groupby("session_id", sort=True):
    level = int(re.search(r"level\s+(\d+)", group.notes.iloc[0], re.I).group(1))
    delta_ms = group.board_time_ms.diff()
    delta_samples = group.sample_number.diff()
    breaks = (delta_ms > 150) | (delta_ms <= 0) | (delta_samples.fillna(1) != 1)
    segment_id = breaks.cumsum()
    sizes = group.groupby(segment_id).size()
    selected = group[segment_id == sizes.idxmax()].copy()
    t = (selected.board_time_ms - selected.board_time_ms.iloc[0]) / 1000
    segments[int(sid)] = (selected, t)
    for index in group.index[breaks]:
        gap_rows.append(dict(session_id=int(sid), next_record_id=int(group.loc[index, "record_id"]),
                             next_sample_number=int(group.loc[index, "sample_number"]),
                             elapsed_board_ms=int(delta_ms.loc[index]),
                             sample_number_step=int(delta_samples.loc[index])))
    quality.append(dict(session_id=int(sid), level=level,
                        started_at_utc=sessions.loc[sessions.id == sid, "started_at_utc"].iloc[0],
                        total_samples=len(group), discontinuities=int(breaks.sum()),
                        selected_segment_samples=len(selected), excluded_from_timing=len(group)-len(selected),
                        segment_span_s=float(t.iloc[-1]), first_selected_record_id=int(selected.record_id.iloc[0]),
                        all_metrics_zero=bool((group[metrics] == 0).all().all())))
    row = dict(session_id=int(sid), level=level, samples_in_timing_analysis=len(selected))
    for key in metrics:
        row[key + "_max"] = float(selected[key].max())
        row[key + "_min"] = float(selected[key].min())
        row[key + "_mean"] = float(selected[key].mean())
        row[key + "_peak_s"] = float(t.loc[selected[key].idxmax()])
        if "slope" in key:
            row[key + "_absolute_max"] = float(selected[key].abs().max())
    for prefix, mask in [("first10s", t < 10), ("last5s", t >= t.iloc[-1]-5)]:
        for key in metrics[:4]:
            row[prefix + "_" + key + "_mean"] = float(selected.loc[mask, key].mean())
            row[prefix + "_" + key + "_max"] = float(selected.loc[mask, key].max())
    rows.append(row)
summary = pd.DataFrame(rows)
summary.to_csv(OUT / "per_trial_summary.csv", index=False)
pd.DataFrame(quality).to_csv(OUT / "recording_quality.csv", index=False)
pd.DataFrame(gap_rows).to_csv(OUT / "timestamp_gaps.csv", index=False)
assert not any(q["all_metrics_zero"] for q in quality if q["level"] == 6)

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
fig, axes = plt.subplots(3, 5, figsize=(16, 9.5), sharex=True, sharey="row")
baseline = frame[frame.session_id <= 20]
panels = [("accel_magnitude_mps2", "Acceleration magnitude\n(m/s²)", None),
          ("accel_msd", "Acceleration MSD\n(m/s²)² — log scale", 1e-7),
          ("gyro_msd", "Gyroscope MSD\n(degrees/s)² — log scale", 1e-6)]
for column, sid in enumerate(range(26, 31)):
    group, t = segments[sid]
    for row_index, (key, label, floor) in enumerate(panels):
        ax = axes[row_index, column]
        values = group[key].to_numpy()
        if floor is None:
            ax.axhspan(baseline[key].min(), baseline[key].max(), color="#94a3b8", alpha=.25)
            ax.set_ylim(.8, 17.5)
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
            ax.set_title(f"Trial {sid}" + ("*" if sid == 27 else ""), fontweight="bold")
        if row_index == 2:
            ax.set_xlabel("Seconds in continuous recording")
axes[2, 3].annotate("Movement resumes", xy=(28.3, 8), xytext=(13, .03),
                    arrowprops=dict(arrowstyle="->", color="#a13d38"), color="#a13d38", fontsize=9)
fig.suptitle("Level 6 repeats: bursts are consistent; Trial 29 moves again afterward", fontsize=16, y=.97)
fig.text(.5, .045, "Red dashed line: historical Levels 1–4 maximum, not an alarm threshold. Faster preliminary movement can exceed it.\n"
         "*Trial 27: two early samples excluded from timing; plot starts at the uninterrupted 300-sample segment. Original data retained.\n"
         "All readings are EWMA-filtered. MSD log plots display zero/tiny values at the axis minimum. Hand simulations do not validate human fall detection.",
         ha="center", fontsize=10)
fig.subplots_adjust(left=.075, right=.99, top=.90, bottom=.17, hspace=.26, wspace=.12)
fig.savefig(OUT / "level6_retest_timelines.png", dpi=160)
fig.savefig(OUT / "level6_retest_timelines.pdf")
plt.close(fig)

assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
(OUT / "analysis_checks.json").write_text(json.dumps(dict(
    input_sha256=hashes, csv_matches_database=True, source_files_unchanged=True,
    total_samples=len(frame), level6_samples=int((frame.session_id >= 26).sum()),
    level6_nonzero_sessions=[26,27,28,29,30],
    timing_exclusion="Session27 first two rows (record_ids9153,9154) retained in originals but excluded from continuous-segment summaries.",
    gap_handling="Split on >150ms or nonconsecutive/restarted sample count; never interpolate or recompute metrics across a gap.",
    data_identity="Replacement sessions27–30 started after09:08UTC; distinct from deleted zero recordings with reused session IDs."
), indent=2) + "\n")
print(summary[summary.level.isin([5,6])].groupby("level")[[
    "accel_msd_max", "gyro_msd_max", "accel_magnitude_mps2_max", "gyro_magnitude_dps_max",
    "first10s_accel_msd_mean", "first10s_gyro_msd_mean"]].agg(["min", "max"]).to_string())
print("Saved read-only analysis:", OUT)
