# CG2028 sensor and fall-detection experiments

The current application is **Prototype 2** in
[`CG2028_Assignment`](CG2028_Assignment/Core/Src/main.c). It filters the
accelerometer and gyroscope readings, checks a sudden change against the
preceding activity, then observes whether movement continues or becomes quiet.
These are experimental rules for hand-moved board trials, not validated human
fall detection.

**Rebuild and flash `CG2028_Assignment` in CubeIDE to run Prototype 2.** Updating
the Python recorder alone does not install the new detector on the STM32.
Resume execution, reset the board before a fresh trial, and wait for `READY`
(`State=NORMAL`, `Alarm=0`) before the intended event. Startup needs roughly
five seconds of valid readings: two seconds for filter settling, then three
seconds of baseline activity.

Sampling remains **100 ms**, UART remains **115200 baud**, and each axis uses
the existing **25% EWMA** filter. Recording commands and destinations are
unchanged:

```bash
python3 tools/record_activity.py --test --name trial-1 --verdict fall
python3 tools/record_activity.py --free --name everyday-activity
python3 tools/record_activity.py --calibration --name walking-slowly \
  --notes "Consistent hand movement"
```

Choose one command at a time. Tests record for 30 seconds by default, including
after an early fall decision; calibration records for 30 seconds. Free mode
runs until the board reports a fall or you stop it. `--verdict` is your expected
test outcome and never changes the detector's answer. Existing recordings are
retained.

- [Prototype 2: baseline comparison and decision timing](docs/prototype-2.md)
- [Prototype 2 replay results and limitations](data/prototype2_analysis_2026-09-25/README.md)
- [Recording commands, names and duration](tools/README.md)
- [Saved files, fields and verdict meanings](data/README.md)
- [Prototype 1: historical detector behavior](docs/prototype-1.md)
