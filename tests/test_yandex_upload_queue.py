from pathlib import Path

from src.report_uploader import UploadQueue, load_queue_state, save_queue_state


def test_enqueue_creates_queued_job():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/report"), meta=None)

    assert job.status == "queued"
    assert job.attempts == 0
    assert queue.next_queued() is job
    assert queue.jobs == [job]


def test_enqueue_assigns_increasing_ids():
    queue = UploadQueue()
    job1 = queue.enqueue(Path("/tmp/a"), meta=None)
    job2 = queue.enqueue(Path("/tmp/b"), meta=None)

    assert job2.id != job1.id
    assert job2.id > job1.id


def test_next_queued_skips_non_queued_jobs():
    queue = UploadQueue()
    job1 = queue.enqueue(Path("/tmp/a"), meta=None)
    job2 = queue.enqueue(Path("/tmp/b"), meta=None)
    queue.mark_uploading(job1)

    assert queue.next_queued() is job2


def test_next_queued_returns_none_when_nothing_queued():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/a"), meta=None)
    queue.mark_uploading(job)

    assert queue.next_queued() is None


def test_mark_done_sets_status():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/a"), meta=None)
    queue.mark_uploading(job)

    queue.mark_done(job)

    assert job.status == "done"


def test_mark_needs_folder_does_not_touch_attempts():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/a"), meta=None)

    queue.mark_needs_folder(job)

    assert job.status == "needs_folder"
    assert job.attempts == 0
    assert job.error is None


def test_mark_failed_or_retry_schedules_retry_with_backoff():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/a"), meta=None)
    queue.mark_uploading(job)

    delay = queue.mark_failed_or_retry(job, "connection reset")

    assert delay == 5000
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.error == "connection reset"


def test_mark_failed_or_retry_second_attempt_uses_longer_delay():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/a"), meta=None)
    queue.mark_uploading(job)
    queue.mark_failed_or_retry(job, "error 1")
    queue.mark_uploading(job)

    delay = queue.mark_failed_or_retry(job, "error 2")

    assert delay == 20000
    assert job.attempts == 2
    assert job.status == "queued"


def test_mark_failed_or_retry_gives_up_after_max_attempts():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/a"), meta=None)

    queue.mark_uploading(job)
    assert queue.mark_failed_or_retry(job, "error 1") == 5000
    queue.mark_uploading(job)
    assert queue.mark_failed_or_retry(job, "error 2") == 20000
    queue.mark_uploading(job)
    delay = queue.mark_failed_or_retry(job, "error 3")

    assert delay is None
    assert job.status == "failed"
    assert job.attempts == 3
    assert job.error == "error 3"


def test_retry_resets_attempts_and_requeues():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/a"), meta=None)
    queue.mark_uploading(job)
    queue.mark_failed_or_retry(job, "err")
    queue.mark_failed_or_retry(job, "err")
    queue.mark_failed_or_retry(job, "err")
    assert job.status == "failed"

    queue.retry(job)

    assert job.status == "queued"
    assert job.attempts == 0
    assert job.error is None


def test_is_uploading_reflects_active_job():
    queue = UploadQueue()
    job = queue.enqueue(Path("/tmp/a"), meta=None)

    assert queue.is_uploading() is False

    queue.mark_uploading(job)
    assert queue.is_uploading() is True

    queue.mark_done(job)
    assert queue.is_uploading() is False


def test_to_dicts_skips_done_jobs(tmp_path):
    queue = UploadQueue()
    done_job = queue.enqueue(tmp_path / "done", meta=None)
    queue.mark_done(done_job)
    queue.enqueue(tmp_path / "pending", meta=None)

    dicts = queue.to_dicts()

    assert len(dicts) == 1
    assert dicts[0]["local_folder"] == str(tmp_path / "pending")


def test_to_dicts_converts_uploading_to_queued(tmp_path):
    queue = UploadQueue()
    job = queue.enqueue(tmp_path / "a", meta=None)
    queue.mark_uploading(job)

    dicts = queue.to_dicts()

    assert dicts[0]["status"] == "queued"


def test_to_dicts_preserves_attempts_and_error(tmp_path):
    queue = UploadQueue()
    job = queue.enqueue(tmp_path / "a", meta=None)
    queue.mark_uploading(job)
    queue.mark_failed_or_retry(job, "boom")
    queue.mark_uploading(job)
    queue.mark_failed_or_retry(job, "boom again")

    dicts = queue.to_dicts()

    assert dicts[0]["attempts"] == 2
    assert dicts[0]["error"] == "boom again"


def test_from_dicts_skips_missing_folders(tmp_path):
    data = [{"id": 1, "local_folder": str(tmp_path / "does_not_exist"), "status": "queued", "attempts": 0, "error": None}]

    queue = UploadQueue.from_dicts(data)

    assert queue.jobs == []


def test_from_dicts_reparses_meta_from_report_filename(tmp_path):
    folder = tmp_path / "отчет_Show_s01_e02_2025_06_23_rus"
    folder.mkdir()
    (folder / "отчет_Show_s01_e02_2025_06_23_rus.docx").write_bytes(b"")
    data = [{"id": 1, "local_folder": str(folder), "status": "queued", "attempts": 1, "error": "was failing"}]

    queue = UploadQueue.from_dicts(data)

    assert len(queue.jobs) == 1
    restored = queue.jobs[0]
    assert restored.local_folder == folder
    assert restored.attempts == 1
    assert restored.error == "was failing"
    assert restored.meta is not None
    assert restored.meta.season == 1
    assert restored.meta.episode == 2


def test_from_dicts_next_id_continues_after_restored_jobs(tmp_path):
    folder = tmp_path / "a"
    folder.mkdir()
    data = [{"id": 5, "local_folder": str(folder), "status": "queued", "attempts": 0, "error": None}]

    queue = UploadQueue.from_dicts(data)
    new_job = queue.enqueue(tmp_path / "b", meta=None)

    assert new_job.id == 6


def test_save_and_load_queue_state_round_trip(tmp_path):
    state_file = tmp_path / "queue.json"
    folder = tmp_path / "report_folder"
    folder.mkdir()

    queue = UploadQueue()
    queue.enqueue(folder, meta=None)
    save_queue_state(queue, state_file)

    assert state_file.exists()

    restored = load_queue_state(state_file)

    assert len(restored.jobs) == 1
    assert restored.jobs[0].local_folder == folder


def test_load_queue_state_missing_file_returns_empty_queue(tmp_path):
    restored = load_queue_state(tmp_path / "does_not_exist.json")

    assert restored.jobs == []


def test_load_queue_state_corrupt_file_returns_empty_queue(tmp_path):
    state_file = tmp_path / "queue.json"
    state_file.write_text("not valid json{{{", encoding="utf-8")

    restored = load_queue_state(state_file)

    assert restored.jobs == []
