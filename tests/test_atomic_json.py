import json
from pathlib import Path

import pytest

from joystick_notify.atomic_json import atomic_write_json


def test_atomic_write_json_writes_readable_file(tmp_path):
    path = Path(tmp_path) / "state.json"
    atomic_write_json(path, {"a": 1}, prefix=".test-")
    assert json.loads(path.read_text()) == {"a": 1}


def test_atomic_write_json_overwrites_existing_file(tmp_path):
    path = Path(tmp_path) / "state.json"
    atomic_write_json(path, {"a": 1}, prefix=".test-")
    atomic_write_json(path, {"a": 2}, prefix=".test-")
    assert json.loads(path.read_text()) == {"a": 2}


def test_atomic_write_json_leaves_no_tempfile_on_success(tmp_path):
    path = Path(tmp_path) / "state.json"
    atomic_write_json(path, {"a": 1}, prefix=".test-")
    leftovers = list(Path(tmp_path).glob(".test-*"))
    assert leftovers == []


def test_atomic_write_json_cleans_up_tempfile_on_failure(tmp_path, monkeypatch):
    import joystick_notify.atomic_json as atomic_json

    def fake_dump(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(atomic_json.json, "dump", fake_dump)

    path = Path(tmp_path) / "state.json"
    with pytest.raises(ValueError):
        atomic_write_json(path, {"a": 1}, prefix=".test-")

    assert not path.exists()
    leftovers = list(Path(tmp_path).glob(".test-*"))
    assert leftovers == []
