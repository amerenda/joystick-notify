"""Shared atomic-write helper for the two cross-process JSON state files
(health.py's health.json, event_log.py's events.json) -- both need the
same guarantee (a reader in a separate process, the wizard or tray, never
sees a partially-written file) via write-to-tempfile-then-rename, so it
lives in one place instead of being copy-pasted per file.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path: Path, payload: dict, *, prefix: str) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
