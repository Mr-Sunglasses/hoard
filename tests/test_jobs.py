import errno
import json
import shutil
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import hub, jobs, storage, verify
from app.config import JOBS_FILE, MIN_FREE_BYTES, MODELS_DIR

real_run = jobs._run   # saved before the fixture replaces it


@pytest.fixture(autouse=True)
def clean_jobs(monkeypatch):
    monkeypatch.setattr(jobs, "_run", lambda job: None)   # never start real downloads
    jobs._jobs.clear()
    jobs._stop.clear()
    jobs._closing.clear()
    yield
    jobs._closing.clear()
    jobs._jobs.clear()
    jobs._stop.clear()
    JOBS_FILE.unlink(missing_ok=True)


def test_merged_selection():
    existing = {"repo_id": "a/b", "include": ["config.json"]}
    assert jobs.merged_selection(existing, "a/b", ["*.gguf"]) == ["*.gguf", "config.json"]
    assert jobs.merged_selection(existing, "a/b", None) is None
    assert jobs.merged_selection({"repo_id": "a/b", "include": None}, "a/b", ["x"]) is None
    assert jobs.merged_selection({}, "a/b", ["x"]) == ["x"]


def test_space_check_counts_reserve_and_other_downloads(monkeypatch):
    monkeypatch.setattr(storage, "free_bytes", lambda: MIN_FREE_BYTES + 1000)
    jobs._check_space(1000)   # fits exactly
    with pytest.raises(HTTPException) as refused:
        jobs._check_space(1001)
    assert refused.value.status_code == 507

    jobs._jobs["q"] = {"status": "queued", "needed_bytes": 600, "total_bytes": 600, "done_bytes": 0}
    with pytest.raises(HTTPException):
        jobs._check_space(500)


def test_restore_requeues_unfinished_downloads():
    (MODELS_DIR / "org__resume").mkdir(exist_ok=True)
    saved = [
        {"id": "a1", "name": "org__resume", "repo_id": "org/resume", "status": "downloading",
         "started_at": "2026-01-01T00:00:00+00:00", "total_bytes": 10, "done_bytes": 3},
        {"id": "b2", "name": "org__gone", "repo_id": "org/gone", "status": "downloading",
         "started_at": "2026-01-01T00:00:01+00:00", "total_bytes": 10, "done_bytes": 0},
        {"id": "c3", "name": "org__old", "repo_id": "org/old", "status": "done",
         "started_at": "2025-01-01T00:00:00+00:00", "total_bytes": 1, "done_bytes": 1},
        {"id": "d4", "name": "org__waiting", "repo_id": "org/waiting", "status": "queued", "attempt": 0,
         "started_at": "2026-01-01T00:00:02+00:00", "total_bytes": 10, "done_bytes": 0},
        {"id": "e5", "name": "org__gone2", "repo_id": "org/gone2", "status": "queued", "attempt": 2,
         "started_at": "2026-01-01T00:00:03+00:00", "total_bytes": 10, "done_bytes": 0},
    ]
    JOBS_FILE.write_text(json.dumps(saved))
    jobs.restore()
    by_id = {j["id"]: j for j in jobs.all_jobs()}
    assert by_id["a1"]["status"] == "downloading"   # requeued and given a slot
    assert "b2" not in by_id                         # had started, and its folder was deleted meanwhile
    assert "e5" not in by_id                         # same, requeued by an earlier restart
    assert by_id["c3"]["status"] == "done"           # history kept
    assert by_id["d4"]["status"] in jobs.ACTIVE      # never started, so it has no folder yet: kept
    assert json.loads(JOBS_FILE.read_text())         # saved again


def test_queue_respects_parallel_limit(monkeypatch):
    monkeypatch.setattr(jobs, "MAX_PARALLEL_DOWNLOADS", 2)
    for i in range(4):
        jobs._jobs[str(i)] = {"id": str(i), "name": f"m{i}", "status": "queued",
                              "started_at": f"2026-01-01T00:00:0{i}+00:00", "needed_bytes": 0}
        jobs._stop[str(i)] = jobs.threading.Event()
    with jobs._lock:
        jobs._fill_slots()
    statuses = [jobs._jobs[str(i)]["status"] for i in range(4)]
    assert statuses == ["downloading", "downloading", "queued", "queued"]

    jobs.cancel("3")
    assert jobs._jobs["3"]["status"] == "cancelled"


def test_failure_classification():
    assert hub.describe_failure(OSError(errno.ENOSPC, "No space left")) == ("The disk is full", False)
    message, retry = hub.describe_failure(ConnectionError("Connection reset by peer"))
    assert retry and "reset" in message


def test_cancelled_queued_job_leaves_model_untouched(monkeypatch):
    folder = MODELS_DIR / "org__kept"
    folder.mkdir(exist_ok=True)
    storage.write_meta(folder, {"repo_id": "org/kept", "sha": "old"})
    job = {"id": "k1", "name": "org__kept", "status": "downloading", "started_at": "x",
           "repo_id": "org/kept", "total_bytes": 1, "done_bytes": 0, "_meta": {"repo_id": "org/kept", "sha": "new"}}
    jobs._jobs["k1"] = job
    jobs._stop["k1"] = jobs.threading.Event()
    jobs._stop["k1"].set()
    real_run(job)
    assert job["status"] == "cancelled"
    assert storage.read_meta(folder) == {"repo_id": "org/kept", "sha": "old"}


def test_internal_fields_hidden():
    jobs._jobs["h"] = {"id": "h", "started_at": "x", "status": "done", "_meta": {"secret": 1}}
    assert "_meta" not in jobs.all_jobs()[0]


# ---------- jobs.start with a fake Hugging Face ----------


def fake_hub(monkeypatch, repo_id, files, sha="new"):
    """Make hub.client().model_info() describe a repo holding `files` ({path: size})."""
    info = SimpleNamespace(
        id=repo_id, sha=sha, pipeline_tag=None, library_name=None, gated=False, card_data=None,
        siblings=[SimpleNamespace(rfilename=path, size=size) for path, size in files.items()],
    )
    monkeypatch.setattr(hub, "client", lambda: SimpleNamespace(model_info=lambda *a, **k: info))
    monkeypatch.setattr(storage, "free_bytes", lambda: MIN_FREE_BYTES + 10**9)


def model(name, files, meta):
    folder = MODELS_DIR / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir()
    for path, size in files.items():
        (folder / path).write_bytes(b"x" * size)
    storage.write_meta(folder, meta)
    return folder


def test_adding_files_counts_the_whole_selection(monkeypatch):
    fake_hub(monkeypatch, "org/add", {"config.json": 10, "a.gguf": 100, "b.gguf": 200})
    meta = {"repo_id": "org/add", "sha": "old", "include": ["config.json", "a.gguf"]}
    model("org__add", {"config.json": 7, "a.gguf": 100}, meta)
    job = jobs.start("org/add", "main", ["b.gguf"])
    assert job["include"] == ["a.gguf", "b.gguf", "config.json"]
    assert job["file_count"] == 3
    assert job["total_bytes"] == 310
    assert job["needed_bytes"] == 210   # b.gguf, plus config.json, which changed upstream


def test_folder_name_collision_refused(monkeypatch):
    fake_hub(monkeypatch, "a__b/c", {"x.gguf": 1})
    model("a__b__c", {"y.gguf": 1}, {"repo_id": "a/b__c", "sha": "old"})
    with pytest.raises(HTTPException) as refused:
        jobs.start("a__b/c", "main", None)
    assert refused.value.status_code == 409 and "a/b__c" in refused.value.detail


def test_no_download_while_checking_files(monkeypatch):
    fake_hub(monkeypatch, "org/busy", {"x.gguf": 1})
    model("org__busy", {"x.gguf": 1}, {"repo_id": "org/busy", "sha": "new"})
    monkeypatch.setitem(verify._runs, "org__busy", {"status": "running"})
    with pytest.raises(HTTPException) as refused:
        jobs.start("org/busy", "main", None)
    assert refused.value.status_code == 409


def test_download_forgets_earlier_check(monkeypatch):
    fake_hub(monkeypatch, "org/stale", {"x.gguf": 1, "y.gguf": 1})
    model("org__stale", {"x.gguf": 1}, {"repo_id": "org/stale", "sha": "old"})
    monkeypatch.setitem(verify._runs, "org__stale", {"status": "done", "problems": []})
    jobs.start("org/stale", "main", None)
    assert verify.status("org__stale") is None


def test_redownload_deletes_files_only_once_accepted(monkeypatch):
    fake_hub(monkeypatch, "org/fix", {"a.gguf": 100, "b.gguf": 100})
    folder = model("org__fix", {"a.gguf": 100, "b.gguf": 100}, {"repo_id": "org/fix", "sha": "new"})
    # Already downloading: refused, and the "broken" file is still there.
    jobs._jobs["other"] = {"id": "other", "name": "org__fix", "status": "downloading", "started_at": "x",
                           "needed_bytes": 0, "total_bytes": 0, "done_bytes": 0}
    with pytest.raises(HTTPException):
        jobs.start("org/fix", "main", None, pin="new", again=["a.gguf"])
    assert (folder / "a.gguf").exists()
    # Not enough space: refused, file still there.
    del jobs._jobs["other"]
    monkeypatch.setattr(storage, "free_bytes", lambda: MIN_FREE_BYTES + 50)
    with pytest.raises(HTTPException) as refused:
        jobs.start("org/fix", "main", None, pin="new", again=["a.gguf"])
    assert refused.value.status_code == 507   # the replaced file counts as needed space
    assert (folder / "a.gguf").exists()
    # Accepted: now it's deleted, so the download fetches it again.
    monkeypatch.setattr(storage, "free_bytes", lambda: MIN_FREE_BYTES + 10**9)
    job = jobs.start("org/fix", "main", None, pin="new", again=["a.gguf"])
    assert job["needed_bytes"] == 100
    assert not (folder / "a.gguf").exists() and (folder / "b.gguf").exists()


def test_repair_route_keeps_files_when_refused(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    fake_hub(monkeypatch, "org/rep", {"a.gguf": 100})
    folder = model("org__rep", {"a.gguf": 100}, {"repo_id": "org/rep", "sha": "new"})
    monkeypatch.setitem(verify._runs, "org__rep", {"status": "done", "problems": [{"path": "a.gguf", "issue": "x"}]})
    monkeypatch.setattr(storage, "free_bytes", lambda: 0)
    response = TestClient(app).post("/api/library/org__rep/repair", headers={"X-Requested-With": "hoard"})
    assert response.status_code == 507
    assert (folder / "a.gguf").exists()


class FakeProcess:
    """Stands in for the download process: each join() 'downloads' 50 more bytes."""

    def __init__(self, folder, rounds):
        self.folder, self.rounds = folder, rounds

    def start(self):
        pass

    def is_alive(self):
        return self.rounds > 0

    def join(self, timeout=None):
        self.rounds -= 1
        with open(self.folder / "b.gguf", "ab") as f:
            f.write(b"x" * 50)


def test_progress_ignores_files_outside_the_selection(monkeypatch):
    folder = model("org__prog", {"a.gguf": 100, "unrelated.bin": 5000}, {"repo_id": "org/prog"})
    job = {"id": "p1", "repo_id": "org/prog", "sha": "s", "include": None, "total_bytes": 200,
           "done_bytes": 0, "_files": [["a.gguf", 100], ["b.gguf", 100]]}
    seen = []
    proc = FakeProcess(folder, rounds=2)
    results = jobs.mp.get_context("spawn").Queue()
    results.put(("done", None, False))
    monkeypatch.setattr(jobs, "_spawn", SimpleNamespace(Queue=lambda: results, Process=lambda **k: proc))
    real_join = proc.join
    proc.join = lambda timeout=None: (seen.append(job["done_bytes"]), real_join())
    assert jobs._download_once(job, folder, jobs.threading.Event())[0] == "done"
    assert seen == [100, 150]   # a.gguf already there, then b.gguf growing; unrelated.bin never counted


def test_shutdown_keeps_downloads_unfinished(monkeypatch):
    terminated = []
    proc = SimpleNamespace(terminate=lambda: terminated.append(1), join=lambda timeout: None)
    jobs._jobs["s1"] = {"id": "s1", "name": "org__s", "repo_id": "org/s", "status": "downloading",
                        "started_at": "x", "needed_bytes": 0, "total_bytes": 1, "done_bytes": 0}
    jobs._jobs["s2"] = {"id": "s2", "name": "org__q", "repo_id": "org/q", "status": "queued",
                        "started_at": "y", "needed_bytes": 0, "total_bytes": 1, "done_bytes": 0}
    jobs._stop["s1"], jobs._stop["s2"] = jobs.threading.Event(), jobs.threading.Event()
    jobs._procs["s1"] = proc
    try:
        jobs.shutdown()
    finally:
        jobs._procs.clear()
    assert terminated == [1]
    # The download thread sees its process die and records an error, as it would for a crash...
    with jobs._lock:
        jobs._finish(jobs._jobs["s1"], "error", "The download process stopped unexpectedly")
    assert jobs._jobs["s2"]["status"] == "queued"   # ...which doesn't start the next one,
    saved = {j["id"]: j["status"] for j in json.loads(JOBS_FILE.read_text())}
    assert saved == {"s1": "downloading", "s2": "queued"}   # nor reach the saved list: both continue later
