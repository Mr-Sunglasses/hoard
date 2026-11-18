import json
import os

import pytest
from fastapi import HTTPException

from app import storage
from app.config import MODELS_DIR


@pytest.mark.parametrize("name", ["", "..", ".hidden", "a/b", "a\\b", "../etc"])
def test_model_dir_rejects_unsafe_names(name):
    with pytest.raises(HTTPException):
        storage.model_dir(name)


def test_model_dir_accepts_folder_names():
    assert storage.model_dir("org__model") == MODELS_DIR / "org__model"


def test_inside_stays_inside(tmp_path):
    assert storage.inside(tmp_path, "a/b.txt") == (tmp_path / "a/b.txt").resolve()
    assert storage.inside(tmp_path, "../escape") is None


def test_selected():
    assert storage.selected("model.safetensors", None)
    assert storage.selected("config.json", ["config.json"])
    assert storage.selected("x-Q4_K_M.gguf", ["*Q4_K_M*"])
    assert not storage.selected("x-Q8_0.gguf", ["*Q4_K_M*"])


def test_pickle_detection():
    assert storage.is_pickle("pytorch_model.bin")
    assert storage.is_pickle("sub/weights.PTH")
    assert not storage.is_pickle("model.safetensors")


def test_write_json_is_atomic(tmp_path):
    target = tmp_path / "data.json"
    storage.write_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]   # no temp files left


def test_bytes_missing(tmp_path):
    (tmp_path / "have.json").write_text("12345")
    (tmp_path / "short.bin").write_text("12")
    files = [("have.json", 5), ("short.bin", 10), ("absent.gguf", 100)]
    assert storage.bytes_missing(tmp_path, files) == 110


def test_summary_marks_unfinished_and_pickle():
    folder = MODELS_DIR / "org__unfinished"
    folder.mkdir()
    (folder / "pytorch_model.bin").write_bytes(b"x" * 10)
    storage.write_meta(folder, {"repo_id": "org/unfinished", "incomplete": True})
    summary = storage.summarize(folder)
    assert summary["incomplete"] and summary["managed"] and summary["pickle"]
    assert summary["size"] == 10 and summary["formats"] == ["bin"]
    assert not storage.is_in_library("org/unfinished")


def test_human():
    assert storage.human(512) == "512 B"
    assert storage.human(3 * 1024**3) == "3.0 GB"


def test_remove_partial_files(tmp_path):
    downloads = tmp_path / ".cache/huggingface/download/sub"
    downloads.mkdir(parents=True)
    (downloads / "abc.1234.incomplete").write_bytes(b"x" * 7)
    (downloads / "model.safetensors.metadata").write_text("keep")
    assert storage.remove_partial_files(tmp_path) == 7
    assert [p.name for p in downloads.iterdir()] == ["model.safetensors.metadata"]
    assert storage.remove_partial_files(tmp_path / "missing") == 0


def test_setup_check_stops_once_it_passes(monkeypatch, tmp_path):
    readonly = tmp_path / "ro"
    readonly.mkdir()
    readonly.chmod(0o500)
    monkeypatch.setattr(storage, "_setup_ok", False)
    monkeypatch.setattr(storage, "MODELS_DIR", readonly)
    try:
        if os.access(readonly, os.W_OK):
            pytest.skip("running as root: permissions aren't enforced")
        assert storage.setup_problems()           # reported
        assert storage.setup_problems()           # and tested again, so a fix shows up at once
        readonly.chmod(0o700)
        assert storage.setup_problems() == []     # fixed
        writes = []
        monkeypatch.setattr(storage.tempfile, "TemporaryFile", lambda **k: writes.append(k))
        assert storage.setup_problems() == []
        assert writes == []                       # not tested (no disk write) once it passed
    finally:
        readonly.chmod(0o700)


def test_discard_files(tmp_path):
    (tmp_path / "a.gguf").write_text("x")
    note = tmp_path / ".cache/huggingface/download/a.gguf.metadata"
    note.parent.mkdir(parents=True)
    note.write_text("done")
    (tmp_path / "b.gguf").write_text("x")
    storage.discard_files(tmp_path, ["a.gguf", "../escape", "missing.gguf"])
    assert not (tmp_path / "a.gguf").exists() and not note.exists()
    assert (tmp_path / "b.gguf").exists()


def test_bytes_missing_counts_replaced_files(tmp_path):
    (tmp_path / "a").write_bytes(b"12345")
    assert storage.bytes_missing(tmp_path, [("a", 5)]) == 0
    assert storage.bytes_missing(tmp_path, [("a", 5)], frozenset({"a"})) == 5
