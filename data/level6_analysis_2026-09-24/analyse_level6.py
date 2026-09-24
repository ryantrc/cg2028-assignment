"""Read-only quality audit of Level 6 sessions 26, 28, 29, 30, 31.

Run with /opt/anaconda3/bin/python (pandas and numpy).
Session IDs differ from the trial numbers in notes after session 27's deletion.
"""
from pathlib import Path
import hashlib
import json
import re
import sqlite3

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
DATA = OUT.parent
inputs = [DATA / "activity_readings.sqlite3", DATA / "activity_readings.csv"]
hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
with sqlite3.connect(inputs[0].as_uri() + "?mode=ro", uri=True) as connection:
    all_rows = pd.read_sql_query("SELECT * FROM readings ORDER BY record_id", connection)
pd.testing.assert_frame_equal(all_rows, pd.read_csv(inputs[1]), check_dtype=False)
new = all_rows[all_rows.session_id.isin([26, 28, 29, 30, 31])]
assert new.session_id.nunique() == 5
measurements = ["accel_x_mps2", "accel_y_mps2", "accel_z_mps2", "accel_magnitude_mps2",
                "gyro_x_dps", "gyro_y_dps", "gyro_z_dps", "gyro_magnitude_dps",
                "accel_msd", "gyro_msd", "accel_magnitude_slope", "accel_msd_slope",
                "gyro_magnitude_slope", "gyro_msd_slope"]
quality = []
for sid, group in new.groupby("session_id"):
    assert re.search(r"level\s+6\b", group.notes.iloc[0], re.I)
    trial = int(re.search(r"Trial\s+(\d+)", group.notes.iloc[0], re.I).group(1))
    t = (group.board_time_ms - group.board_time_ms.iloc[0]) / 1000
    intervals = np.diff(group.board_time_ms)
    finite = bool(np.isfinite(group[measurements]).all().all())
    zero = bool((group[measurements] == 0).all().all())
    quality.append(dict(session_id=int(sid), trial_in_notes=trial, samples=len(group),
                        span_s=float(t.iloc[-1]), min_interval_ms=int(intervals.min()),
                        max_interval_ms=int(intervals.max()),
                        nonconsecutive_sample_steps=int((np.diff(group.sample_number) != 1).sum()),
                        all_measurements_finite=finite, all_sensor_fields_zero=zero,
                        status="invalid_all_zero_sensor_stream" if zero else "nonzero_motion_stream",
                        first_board_time_ms=int(group.board_time_ms.iloc[0]),
                        first_board_sample=int(group.sample_number.iloc[0])))
pd.DataFrame(quality).to_csv(OUT / "recording_quality.csv", index=False)
usable_ids = [q["session_id"] for q in quality if not q["all_sensor_fields_zero"]]
assert usable_ids == [26], "Quality changed since inspection; review the assessment."
group = new[new.session_id == 26]
t = (group.board_time_ms - group.board_time_ms.iloc[0]) / 1000
comparison = []
for metric in ["accel_magnitude_mps2", "gyro_magnitude_dps", "accel_msd", "gyro_msd"]:
    prior_peaks = all_rows[all_rows.session_id.between(21, 25)].groupby("session_id")[metric].max()
    comparison.append(dict(metric=metric, level5_smallest_trial_peak=float(prior_peaks.min()),
                           level5_largest_trial_peak=float(prior_peaks.max()),
                           session26_peak=float(group[metric].max()),
                           session26_peak_time_s=float(t.loc[group[metric].idxmax()]),
                           session26_first10s_mean=float(group.loc[t < 10, metric].mean()),
                           session26_seconds20to30_mean=float(group.loc[(t >= 20) & (t < 30), metric].mean())))
pd.DataFrame(comparison).to_csv(OUT / "session26_comparison.csv", index=False)
assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
manifest = dict(input_sha256=hashes, source_files_unchanged=True, csv_matches_database=True,
                level6_rows=len(new), nonzero_motion_sessions=usable_ids,
                invalid_zero_sessions=[q["session_id"] for q in quality if q["all_sensor_fields_zero"]],
                invalid_zero_rows=sum(q["samples"] for q in quality if q["all_sensor_fields_zero"]),
                caveat="No fall-detection accuracy established. Four all-zero runs excluded from motion interpretation only; no recorded data deleted.")
(OUT / "analysis_checks.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(pd.DataFrame(quality).to_string(index=False))
print(pd.DataFrame(comparison).to_string(index=False))
