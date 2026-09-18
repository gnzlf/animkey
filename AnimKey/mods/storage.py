"""Small crash-safe JSON helpers shared by AnimKey preferences."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime


def atomic_write_json(filepath, data, indent=4, ensure_ascii=True, sort_keys=False):
    folder = os.path.dirname(os.path.abspath(filepath))
    if not os.path.exists(folder):
        os.makedirs(folder)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=os.path.basename(filepath) + ".",
        suffix=".tmp",
        dir=folder,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                data,
                stream,
                indent=indent,
                ensure_ascii=ensure_ascii,
                sort_keys=sort_keys,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, filepath)
    except Exception:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise


def backup_corrupt_file(filepath):
    if not os.path.exists(filepath):
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = "{}.corrupt.{}".format(filepath, stamp)
    try:
        shutil.copy2(filepath, backup)
        return backup
    except Exception:
        return None


def read_json(filepath, default=None, backup_corrupt=True):
    try:
        with open(filepath, "r") as stream:
            return json.load(stream)
    except (ValueError, OSError, IOError):
        if backup_corrupt:
            backup_corrupt_file(filepath)
        return default


def deep_merge(defaults, saved):
    """Return defaults recursively updated with compatible saved values."""
    if not isinstance(defaults, dict):
        return saved
    result = {}
    saved = saved if isinstance(saved, dict) else {}
    for key, value in defaults.items():
        if isinstance(value, dict):
            result[key] = deep_merge(value, saved.get(key))
        else:
            result[key] = saved.get(key, value)
    for key, value in saved.items():
        if key not in result:
            result[key] = value
    return result
