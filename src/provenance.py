"""Reproducible identities and the conservative daily data cutoff contract."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = 2


def cutoff_timestamp(value=None) -> pd.Timestamp:
    stamp = pd.Timestamp.now(tz="America/New_York") if value is None else pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("America/New_York")
    return stamp.tz_convert("UTC")


def local_day(value=None) -> pd.Timestamp:
    return cutoff_timestamp(value).tz_convert("America/New_York").tz_localize(None).normalize()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def frame_digest(frame: pd.DataFrame) -> str:
    ordered = frame.reindex(sorted(frame.columns), axis=1)
    return hashlib.sha256(ordered.to_csv(index=False, float_format="%.12g").encode()).hexdigest()


def implementation_id() -> str:
    root = Path(__file__).resolve().parent
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.glob("*.py"))})


def runtime_versions() -> dict[str, str]:
    return {name: version(name) for name in ("numpy", "pandas", "scikit-learn", "joblib")}


def git_revision() -> str | None:
    executable = shutil.which("git")
    if executable is None:
        candidate = Path("C:/Program Files/Git/cmd/git.exe")
        executable = str(candidate) if candidate.exists() else None
    if executable is None:
        return None
    try:
        return subprocess.check_output([executable, "rev-parse", "HEAD"],
                                       cwd=Path(__file__).resolve().parents[1],
                                       stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return None
