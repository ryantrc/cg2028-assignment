"""Compare movement after the event in Levels 5–7 without editing recordings.

Run with /opt/anaconda3/bin/python (numpy, pandas, matplotlib).
Largest acceleration-MSD peaks are retrospective alignment markers, not a tested
online detection rule. Split at missing/nonconsecutive samples; never interpolate.
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
pd.testing.assert_frame_equal(all_rows, pd.read_csv(inputs[1]), check_dtype=False)
frame = all_rows[all_rows.session_id.between(21, 35)]
assert frame.session_id.nunique() == 15
assert np.isfinite(frame.select_dtypes(include=np.number)).all().all()
metrics = ["accel_magnitude_mps2", "gyro_magnitude_dps", "accel_msd", "gyro_msd"]
summary, quality, window_rows, bin_rows = [], [], [], []
for sid, group in frame.groupby("session_id", sort=True):
    level = int(re.search(r"level\s+(\d+)", group.notes.iloc[0], re.I).group(1))
    assert level in [5, 6, 7]
    kind = "near_fall_simulation" if level == 7 else "fall_simulation"
    delta_ms = group.board_time_ms.diff()
    delta_n = group.sample_number.diff()
    breaks = (delta_ms > 150) | (delta_ms <= 0) | (delta_n.fillna(1) != 1)
    segment_id = breaks.cumsum()
    selected = group[segment_id == group.groupby(segment_id).size().idxmax()].copy()
    time_s = (selected.board_time_ms - selected.board_time_ms.iloc[0]) / 1000
    peak_s = float(time_s.loc[selected.accel_msd.idxmax()])
    relative = time_s - peak_s
    all_zero = bool((group[metrics] == 0).all().all())
    assert not all_zero
    quality.append(dict(session_id=int(sid), level=level, saved_samples=len(group),
                        selected_samples=len(selected), excluded_prefix_samples=len(group)-len(selected),
                        discontinuities=int(breaks.sum()), largest_interval_ms=float(delta_ms.max()),
                        selected_span_s=float(time_s.iloc[-1]), first_selected_record_id=int(selected.record_id.iloc[0]),
                        all_sensor_metrics_zero=all_zero))
    row = dict(session_id=int(sid), level=level, kind=kind, peak_time_s=peak_s)
    for key in metrics:
        row[key + "_max"] = float(selected[key].max())
        row[key + "_pre10s_mean"] = float(selected.loc[time_s < 10, key].mean())
        row[key + "_last5s_mean"] = float(selected.loc[time_s >= time_s.iloc[-1]-5, key].mean())
    summary.append(row)
    for start, end in [(1, 3), (3, 8), (5, 10)]:
        assert relative.iloc[-1] >= end
        w = selected[(relative >= start) & (relative < end)]
        result = dict(session_id=int(sid), level=level, kind=kind, start_after_peak_s=start,
                      end_after_peak_s=end, samples=len(w))
        for key in metrics:
            result[key + "_mean"] = float(w[key].mean())
        window_rows.append(result)
    for second in range(-5, 10):
        assert relative.iloc[0] <= second and relative.iloc[-1] >= second+1
        w = selected[(relative >= second) & (relative < second+1)]
        result = dict(session_id=int(sid), level=level, kind=kind, start_after_peak_s=second,
                      end_after_peak_s=second+1, center_after_peak_s=second+.5, samples=len(w))
        for key in metrics:
            result[key + "_mean"] = float(w[key].mean())
        bin_rows.append(result)

windows = pd.DataFrame(window_rows)
bins = pd.DataFrame(bin_rows)
pd.DataFrame(summary).to_csv(OUT / "per_trial_summary.csv", index=False)
pd.DataFrame(quality).to_csv(OUT / "recording_quality.csv", index=False)
windows.to_csv(OUT / "post_event_windows.csv", index=False)
bins.to_csv(OUT / "event_aligned_one_second_means.csv", index=False)

plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
colours = {31:"#087f8c", 32:"#d47716", 33:"#7155b5", 34:"#c33f66", 35:"#2a9134"}
panels = [("accel_msd_mean", "Acceleration MSD\n(m/s²)² — log scale", 1e-7),
          ("gyro_msd_mean", "Gyroscope MSD\n(degrees/s)² — log scale", 1e-6),
          ("gyro_magnitude_dps_mean", "Gyroscope magnitude\n(degrees/s)", None)]
for ax, (key, label, floor) in zip(axes, panels):
    ax.axvspan(3, 8, color="#5ca26c", alpha=.10)
    ax.axvline(0, color="#333333", linestyle=":", linewidth=1)
    for sid, group in bins.groupby("session_id", sort=True):
        values = group[key].to_numpy()
        if floor is not None:
            values = np.maximum(values, floor)
        ax.plot(group.center_after_peak_s, values, color=colours.get(sid, "#858d99"),
                alpha=.9 if sid >=31 else .45, linewidth=1.8 if sid>=31 else 1.0,
                label=f"Level 7 / trial {sid}" if sid>=31 else ("Levels 5–6 / 10 trials" if sid==21 else None))
    ax.set_ylabel(label)
    ax.grid(axis="y", alpha=.15)
    ax.set_xlim(-5, 10)
    if floor is not None:
        ax.set_yscale("log")
        ax.set_ylim(bottom=floor)
    else:
        ax.set_ylim(bottom=0)
axes[-1].set_xlabel("Seconds relative to largest acceleration-MSD sample (retrospective alignment)")
axes[-1].set_xticks(range(-5, 11))
handles, labels = axes[0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(.55,.935), ncol=3, frameon=False)
fig.suptitle("Level 7: continued motion after a burst separates these staged recordings", fontsize=15, y=.98)
fig.text(.55,.035,"Lines show one-second means. Green band marks the 3–8 s comparison window; it is not a validated decision delay.\n"
         "First 1–3 s overlap; later motion in fall-labelled trial 29 narrows separation. Two stale prefix rows in trials 27 and 31 excluded only from timing.\n"
         "Level 7 remained hand-held and moving; earlier fall simulations used table rest. No detector accuracy or human outcome inferred.",
         ha="center", fontsize=9)
fig.subplots_adjust(left=.12,right=.98,top=.865,bottom=.15,hspace=.20)
fig.savefig(OUT / "post_event_comparison.png", dpi=160)
fig.savefig(OUT / "post_event_comparison.pdf")
plt.close(fig)

assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
(OUT / "analysis_checks.json").write_text(json.dumps(dict(
    input_sha256=hashes, csv_matches_database=True, original_files_unchanged=True,
    comparison_sessions=list(range(21,36)), new_sessions=list(range(31,36)),
    new_saved_samples=int((frame.session_id>=31).sum()), new_main_segment_samples=1495,
    event_definition="Largest acceleration-MSD sample in longest continuous segment, selected retrospectively.",
    window_definition="Means over [peak+start,peak+end), all windows fully observed; sensitivity windows1–3,3–8,5–10seconds.",
    recording_setup="User confirmed Level7 board held throughout spike and continued movement, without table contact.",
    quality_caveat="Two stale-looking leading frames in sessions27 and31 excluded from timing only; originals preserved.",
    interpretation="Descriptive gesture separation only; no accuracy estimate or verified online threshold."
),indent=2)+"\n")
print(windows.groupby(["start_after_peak_s","kind"])[["accel_msd_mean","gyro_msd_mean","gyro_magnitude_dps_mean"]].agg(["min","max"]).to_string())
print("Saved analysis:", OUT)
