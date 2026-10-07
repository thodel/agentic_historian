
"""Tests for shared_lock module (#393)."""
import json
from pathlib import Path

from shared_lock import locked_append, write_json_atomic, read_json, read_jsonl

def test_locked_append_and_read(tmp_path: Path):
    f = tmp_path / 'data.jsonl'
    # Append two JSON lines
    locked_append(f, json.dumps({"a": 1}))
    locked_append(f, json.dumps({"b": 2}))
    # Read back lines via plain read
    lines = f.read_text(encoding='utf-8').splitlines()
    assert lines == [json.dumps({"a": 1}), json.dumps({"b": 2})]

def test_write_json_atomic_and_read(tmp_path: Path):
    f = tmp_path / 'data.json'
    data = {"x": [1,2,3], "y": "test"}
    write_json_atomic(f, data)
    # Read back via read_json
    read_back = read_json(f)
    assert read_back == data

def test_locked_jsonl_reader(tmp_path: Path):
    f = tmp_path / 'data2.jsonl'
    locked_append(f, json.dumps({"c": 3}))
    # Use read_jsonl context manager
    for lines in read_jsonl(f):
        assert lines == [json.dumps({"c": 3})]
