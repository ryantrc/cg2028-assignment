"""Read-only bridge to the assignment's existing recording implementation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
ASSIGNMENT_ROOT = ROOT.parent
RECORDER_PATH = ASSIGNMENT_ROOT / "tools" / "record_activity.py"


def _load_recorder():
    if not RECORDER_PATH.is_file():
        raise FileNotFoundError(f"Existing recorder was not found: {RECORDER_PATH}")
    # This module is deliberately loaded without allowing Python to write a
    # .pyc file into the pre-existing tools directory.
    sys.dont_write_bytecode = True
    module_name = "cg2028_existing_record_activity_readonly"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(module_name, RECORDER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load existing recorder: {RECORDER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_RECORDER = _load_recorder()
SampleParser = _RECORDER.SampleParser
RecordingDatabase = _RECORDER.RecordingDatabase
CSVRecorder = _RECORDER.CSVRecorder
CSV_FIELDS = _RECORDER.CSV_FIELDS

