import time

from app.core import config, worker_presence


def test_missing_heartbeat_means_no_worker(tmp_path):
    assert worker_presence.worker_status(tmp_path / "none.json") == {"alive": False, "last_seen": None}


def test_fresh_heartbeat_is_alive_and_old_one_is_not(tmp_path, monkeypatch):
    path = tmp_path / "hb.json"
    worker_presence.write_heartbeat(path)
    assert worker_presence.worker_status(path)["alive"] is True

    monkeypatch.setattr(time, "time", lambda: 10**12)
    assert worker_presence.worker_status(path)["alive"] is False


def test_queue_notice_only_for_queued_jobs_in_worker_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(config, "JOB_RUNNER", "worker")
    assert worker_presence.queue_notice("running") is None
    notice = worker_presence.queue_notice("queued")
    assert notice["alive"] is False and "worker.py" in notice["message"]

    worker_presence.write_heartbeat()
    notice = worker_presence.queue_notice("queued")
    assert notice["alive"] is True and "message" not in notice

    monkeypatch.setattr(config, "JOB_RUNNER", "inline")
    assert worker_presence.queue_notice("queued") is None
