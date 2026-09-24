"""Read-only analysis of the four initial hand-movement demonstrations.

Run with /opt/anaconda3/bin/python; requires numpy, pandas and matplotlib.
Outputs stay alongside this script. Original recordings are never modified.
"""

from pathlib import Path
import argparse
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
SIDS = [3, 4, 5, 6]
NAMES = ["Normal simulation", "Slow simulation", "Quick simulation", "Shake, then rest"]
COLOURS = ["#27699c", "#388455", "#c97b1b", "#b74758"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--db", type=Path, default=DATA / "activity_readings.sqlite3")
parser.add_argument("--csv", type=Path, default=DATA / "activity_readings.csv")
args = parser.parse_args()
originals = [args.db.expanduser().resolve(), args.csv.expanduser().resolve()]
before = {p.name: digest(p) for p in originals}
with sqlite3.connect(originals[0].as_uri() + "?mode=ro", uri=True) as con:
    frame = pd.read_sql_query("SELECT * FROM readings ORDER BY record_id", con)
csv_frame = pd.read_csv(originals[1])
if "accel_avg_mps2" not in frame.columns:
    parser.error("This historical analysis uses Avg-format readings. Supply the original "
                 "before-magnitude backups with --db and --csv; see assessment.md.")
pd.testing.assert_frame_equal(frame, csv_frame[frame.columns], check_dtype=False)
frame = frame[frame.session_id.isin(SIDS)].copy()
numeric = frame.select_dtypes(include=np.number)
assert np.isfinite(numeric.to_numpy()).all()

checks = {"csv_matches_database": True, "finite_numeric_fields": True, "sessions": {}}
groups = []
rows = []
features = ["accel_magnitude_g", "gyro_magnitude_dps", "accel_msd", "gyro_msd",
            "abs_accel_avg_slope", "abs_accel_msd_slope",
            "abs_gyro_avg_slope", "abs_gyro_msd_slope"]

for sid, name in zip(SIDS, NAMES):
    d = frame[frame.session_id == sid].copy().reset_index(drop=True)
    ticks = d.board_time_ms.to_numpy(dtype=np.int64)
    ticks = (ticks - ticks[0]) % (2 ** 32)
    dt = np.diff(ticks)
    assert np.all(np.diff(d.sample_number) == 1)
    assert np.all((dt == 100) | (dt == 101))
    d["elapsed_s"] = ticks / 1000
    accel = d[[f"accel_{a}_mps2" for a in "xyz"]].to_numpy()
    gyro = d[[f"gyro_{a}_dps" for a in "xyz"]].to_numpy()
    d["accel_magnitude_g"] = np.linalg.norm(accel, axis=1) / 9.80665
    d["gyro_magnitude_dps"] = np.linalg.norm(gyro, axis=1)
    for sensor, axes in [("accel", accel), ("gyro", gyro)]:
        avg_col = "accel_avg_mps2" if sensor == "accel" else "gyro_avg_dps"
        avg_error = np.max(np.abs(d[avg_col] - axes.mean(axis=1)))
        assert avg_error <= 0.00101  # Rounding of each displayed axis and Avg.
        delta = np.diff(axes, axis=0)
        computed_msd = np.mean(delta ** 2, axis=1)
        stored_msd = d[f"{sensor}_msd"].to_numpy()[1:]
        # Axis display errors <=0.0005 each; delta error <=0.001.
        rounding_bound = np.mean(2 * np.abs(delta) * 0.001 + 0.001 ** 2, axis=1)
        rounding_bound += 5e-7 * np.maximum(1, np.abs(stored_msd))
        assert np.all(np.abs(computed_msd - stored_msd) <= rounding_bound)
        for metric in ["avg", "msd"]:
            d[f"abs_{sensor}_{metric}_slope"] = d[f"{sensor}_{metric}_slope"].abs()
        for window_ms in [500, 1000]:
            # Trailing (time-window, time] windows, excluding partial startup.
            values = d[f"{sensor}_msd"].to_numpy()
            rolling = np.full(len(d), np.nan)
            for j, end in enumerate(ticks):
                if end >= window_ms:
                    start = np.searchsorted(ticks, end - window_ms, side="right")
                    rolling[j] = values[start:j + 1].mean()
            d[f"{sensor}_msd_mean_{window_ms}ms"] = rolling

    checks["sessions"][str(sid)] = {
        "sample_count": len(d), "span_s": float(ticks[-1] / 1000),
        "interval_min_ms": int(dt.min()), "interval_max_ms": int(dt.max()),
        "sample_counters_consecutive": True, "avg_consistent_with_xyz": True,
        "msd_consistent_with_xyz_within_rounding": True,
    }
    row = {"session_id": sid, "demonstration": name, "recorded_label": d.activity.iloc[0],
           "samples": len(d), "span_s": ticks[-1] / 1000}
    for feature in features:
        vals = d[feature]
        for stat, value in [("min", vals.min()), ("median", vals.median()),
                            ("p95", vals.quantile(.95)), ("max", vals.max())]:
            row[f"{feature}_{stat}"] = value
        row[f"{feature}_peak_time_s"] = d.loc[vals.idxmax(), "elapsed_s"]
    for sensor in ["accel", "gyro"]:
        for window_ms in [500, 1000]:
            col = f"{sensor}_msd_mean_{window_ms}ms"
            row[f"{col}_max"] = d[col].max()
            row[f"{col}_peak_time_s"] = d.loc[d[col].idxmax(), "elapsed_s"]
    rows.append(row)
    groups.append(d)

summary = pd.DataFrame(rows)
summary.to_csv(OUT / "trial_summary.csv", index=False)
pd.concat(groups).to_csv(OUT / "derived_readings.csv", index=False)

plt.rcParams.update({"font.size": 11, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.titleweight": "bold"})
fig, axes = plt.subplots(4, 4, figsize=(16, 10), sharex=True, sharey="row")
plots = [
    ("accel_magnitude_g", "Acceleration magnitude\n(g)", (0.45, 1.4)),
    ("gyro_magnitude_dps", "Rotation magnitude\n(degrees/s)", (0, 105)),
    ("accel_msd", "Acceleration MSD\n((m/s²)²)", (0, 21)),
    ("gyro_msd", "Gyroscope MSD\n((degrees/s)²)", (0, 3800)),
]
for col, (d, name, colour) in enumerate(zip(groups, NAMES, COLOURS)):
    axes[0, col].set_title(f"Trial {col + 1}: {name}\n{len(d)} samples", fontsize=12, pad=12)
    for row, (field, label, limits) in enumerate(plots):
        ax = axes[row, col]
        ax.plot(d.elapsed_s, d[field], color=colour, linewidth=1.1)
        ax.set_ylim(*limits)
        ax.set_xlim(0, 30)
        ax.set_xticks([0, 10, 20, 30])
        ax.grid(alpha=.2)
        if field == "accel_magnitude_g":
            ax.axhline(1, color="#777777", linestyle=":", linewidth=.8)
        if col == 0:
            ax.set_ylabel(label)
        if row == 3:
            ax.set_xlabel("Seconds from first saved sample")
        if col == 3:
            ax.axvspan(13.43, 14.84, color="#b74758", alpha=.12)
fig.suptitle("Four hand-movement demonstrations: recorded motion, not validated human falls", y=.98, fontsize=17)
fig.text(.5, .022,
         "All signals use EWMA (25% new input); board samples every 100–101 ms. Scales match across trials.\n"
         "Shading marks the main signal disturbance (~13.4–14.8 s); exact shaking start/end were not annotated.",
         ha="center", fontsize=11)
fig.subplots_adjust(left=.085, right=.985, top=.87, bottom=.12, hspace=.27, wspace=.14)
fig.savefig(OUT / "trial_comparison.png", dpi=150)
fig.savefig(OUT / "trial_comparison.pdf")
plt.close(fig)

after = {p.name: digest(p) for p in originals}
assert before == after, "Input recordings changed during analysis; rerun on a stable copy."
checks["input_sha256"] = before
checks["original_files_unchanged"] = True
checks["description_from_user"] = (
    "All four trials were simulated by moving the board by hand. Trial 4: slow movement, "
    "vigorous shaking for approximately 2–3 seconds, holding still in the hands, then "
    "slowly placing the board on a table. No human fall was recorded. Exact event "
    "timestamps were not annotated."
)
checks["feature_definitions"] = {
    "accel_magnitude_g": "norm(recorded filtered acceleration XYZ) / 9.80665",
    "gyro_magnitude_dps": "norm(recorded filtered gyroscope XYZ)",
    "msd": "Saved firmware mean of squared successive filtered XYZ differences.",
    "abs_slopes": "Absolute value of saved five-point least-squares signed slope.",
    "msd_mean_windows": "Mean of saved MSD in (t-window,t]; partial startup windows excluded.",
    "time_origin": "First recorded board_time_ms in each session.",
}
(OUT / "analysis_checks.json").write_text(json.dumps(checks, indent=2) + "\n")
print(summary[["session_id", "samples", "span_s", "accel_magnitude_g_max",
               "gyro_magnitude_dps_max", "accel_msd_max", "gyro_msd_max"]].to_string(index=False))
print("Verified CSV/database equality, timing, finite values, Avg and MSD rounding consistency.")
print("Original CSV and SQLite hashes unchanged. Outputs:", OUT)
