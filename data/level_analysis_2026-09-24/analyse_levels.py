"""Compare the first 20 magnitude-format hand demonstrations, read-only.

Run with /opt/anaconda3/bin/python. Requires numpy, pandas and matplotlib.
Each recording is the experimental unit; window distributions are descriptive.
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
with sqlite3.connect(inputs[0].as_uri() + "?mode=ro", uri=True) as connection:
    all_rows = pd.read_sql_query("SELECT * FROM readings ORDER BY session_id, record_id", connection)
    sessions = pd.read_sql_query("SELECT * FROM sessions ORDER BY id", connection)
pd.testing.assert_frame_equal(all_rows, pd.read_csv(inputs[1]), check_dtype=False)
frame = all_rows[all_rows.session_id.between(1, 20)].copy()
assert frame.session_id.nunique() == 20
assert np.isfinite(frame.select_dtypes(include=np.number)).all().all()
metrics = ["accel_magnitude_mps2", "gyro_magnitude_dps", "accel_msd", "gyro_msd",
           "accel_magnitude_slope", "gyro_magnitude_slope", "accel_msd_slope", "gyro_msd_slope"]
summary_rows, window_rows, timing_rows = [], [], []

for sid, d in frame.groupby("session_id", sort=True):
    match = re.search(r"level\s+(\d+)", d.notes.iloc[0], re.IGNORECASE)
    assert match is not None
    level = int(match[1])
    assert level == (sid - 1) // 5 + 1
    elapsed_ms = d.board_time_ms.to_numpy(dtype=np.int64) - int(d.board_time_ms.iloc[0])
    elapsed_s = elapsed_ms / 1000.0
    delta = np.diff(elapsed_ms)
    assert np.all(np.diff(d.sample_number) == 1)
    assert np.isin(delta, [100, 101]).all()
    timing_rows.append(dict(session_id=int(sid), level=level, samples=len(d),
                            span_s=float(elapsed_s[-1]), min_interval_ms=int(delta.min()),
                            max_interval_ms=int(delta.max()), mean_interval_ms=float(delta.mean())))
    row = dict(session_id=int(sid), level=level, activity=d.activity.iloc[0],
               notes=d.notes.iloc[0], samples=len(d))
    for key in metrics:
        values = d[key].to_numpy()
        row[key + "_mean"] = float(values.mean())
        row[key + "_median"] = float(np.median(values))
        row[key + "_p95"] = float(np.quantile(values, .95))
        row[key + "_max"] = float(values.max())
        row[key + "_sd"] = float(values.std(ddof=0))
        if "slope" in key:
            row[key + "_abs_median"] = float(np.median(np.abs(values)))
            row[key + "_abs_p95"] = float(np.quantile(np.abs(values), .95))
    middle = d[(elapsed_s >= 2) & (elapsed_s <= elapsed_s[-1] - 2)]
    row["trimmed_gyro_msd_mean"] = float(middle.gyro_msd.mean())
    row["trimmed_accel_magnitude_slope_abs_median"] = float(middle.accel_magnitude_slope.abs().median())
    summary_rows.append(row)
    # Complete wall-clock bins only. Exclude the final partly observed second.
    for second in range(int(elapsed_ms[-1] // 1000)):
        w = d[(elapsed_ms >= second * 1000) & (elapsed_ms < (second + 1) * 1000)]
        window_rows.append(dict(session_id=int(sid), level=level, start_s=second, end_s=second + 1,
                                samples=len(w), gyro_msd_mean=float(w.gyro_msd.mean()),
                                accel_msd_mean=float(w.accel_msd.mean()),
                                gyro_magnitude_mean=float(w.gyro_magnitude_dps.mean())))

summary = pd.DataFrame(summary_rows)
windows = pd.DataFrame(window_rows)
summary.to_csv(OUT / "per_trial_summary.csv", index=False)
windows.to_csv(OUT / "one_second_windows.csv", index=False)
pd.DataFrame(timing_rows).to_csv(OUT / "recording_quality.csv", index=False)

colours = ["#64748b", "#2563eb", "#098574", "#c67514"]
labels = ["1: table rest", "2: slow hand", "3: normal hand", "4: quick hand"]
panels = [
    ("gyro_msd_mean", "Mean gyroscope MSD", "(degrees/s)²", True),
    ("accel_magnitude_slope_abs_median", "Median absolute acceleration magnitude slope", "(m/s²)/s", False),
    ("accel_msd_mean", "Mean acceleration MSD", "(m/s²)²", True),
    ("gyro_magnitude_dps_median", "Median rotation magnitude", "degrees/s", False),
]
plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
fig, axes = plt.subplots(2, 2, figsize=(13, 8))
for ax, (column, title, unit, logarithmic) in zip(axes.flat, panels):
    for level, colour in zip(range(1, 5), colours):
        group = summary[summary.level == level]
        values = group[column].to_numpy()
        assert len(values) == 5
        if logarithmic:
            assert np.all(values > 0)
        ax.vlines(level, values.min(), values.max(), color=colour, alpha=.3, linewidth=7)
        ax.scatter(level + np.linspace(-.14, .14, 5), values, color=colour, s=46, zorder=3)
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10)
    ax.set_ylabel(unit + (" — log scale" if logarithmic else ""))
    ax.set_xticks(range(1, 5), labels, fontsize=10)
    ax.set_xlim(.65, 4.35)
    ax.grid(axis="y", alpha=.2)
    if logarithmic:
        ax.set_yscale("log")
    else:
        ax.set_ylim(bottom=0)
fig.suptitle("Levels 1–4: repeatability across five recordings per level", fontsize=17, y=.97)
fig.text(.5, .035, "Each dot is one ~30-second recording; vertical bars span the five results.\n"
         "The top two features separate these whole recordings. Bottom-row features overlap between Levels 3 and 4.\n"
         "Hand demonstrations and table rest; no human gait or fall-detection accuracy is established.",
         ha="center", fontsize=10)
fig.subplots_adjust(left=.08, right=.98, top=.88, bottom=.18, hspace=.35, wspace=.25)
fig.savefig(OUT / "level_comparison.png", dpi=150)
fig.savefig(OUT / "level_comparison.pdf")
plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 5.5))
values = [windows.loc[windows.level == level, "gyro_msd_mean"].to_numpy() for level in range(1, 5)]
boxes = ax.boxplot(values, tick_labels=labels, patch_artist=True, whis=(5, 95), showfliers=True)
for patch, colour in zip(boxes["boxes"], colours):
    patch.set_facecolor(colour)
    patch.set_alpha(.5)
ax.set_yscale("log")
ax.set_ylabel("Gyroscope MSD, one-second mean\n(degrees/s)² — log scale")
ax.set_title("Short windows show overlapping motion intensities", fontweight="bold")
ax.grid(axis="y", alpha=.2)
fig.text(.5, .03, "145 complete one-second windows per level, pooled from five recordings.\n"
         "Boxes: 25th–75th percentiles; whiskers: 5th–95th; dots: tails.\n"
         "Windows from the same run are related; this is descriptive, not a classifier accuracy test.",
         ha="center", fontsize=10)
fig.subplots_adjust(left=.12, right=.98, top=.89, bottom=.22)
fig.savefig(OUT / "short_window_overlap.png", dpi=150)
plt.close(fig)

assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}, "Inputs changed during analysis"
manifest = {
    "input_sha256": hashes, "sessions": 20, "samples": len(frame),
    "sample_counters_consecutive": True, "all_intervals_100_or_101_ms": True,
    "csv_matches_database": True, "all_numeric_values_finite": True,
    "recording_setup_confirmed_by_user": "Level1 rested on table; Levels2–4 moved by hand.",
    "firmware_configuration": "100ms polling,115200baud,EWMA25%,five-point slopes.",
    "trial_summary_unit": "One entire recording; five recordings per level.",
    "window_definition": "Nonoverlapping [start,start+1s) bins from first board tick; exclude partly observed last bin.",
    "first_saved_slope_caveat": "Firmware windows may contain pre-recording readings for initial rows.",
    "unchanged_original_files": True,
}
(OUT / "analysis_checks.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("Analysed", len(frame), "samples,", len(summary), "trials and", len(windows), "complete one-second windows.")
print(summary.groupby("level")[[p[0] for p in panels]].agg(["min", "max"]).to_string())
print("Saved analysis only:", OUT)
