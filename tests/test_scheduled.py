import os
from datetime import datetime, timedelta

from sftp_file_transfer.components.history_tracker import HistoryTracker
from sftp_file_transfer.scheduled import select_files_to_send


def _set_mtime(file_path, when):
    timestamp = when.timestamp()
    os.utime(file_path, (timestamp, timestamp))


def test_select_files_bootstrap_with_empty_history(tmp_path):
    """Test that with no history, only the exact target day is selected."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    today_file = source_dir / 'today.txt'
    old_file = source_dir / 'old.txt'
    today_file.touch()
    old_file.touch()
    _set_mtime(old_file, datetime.now() - timedelta(days=5))

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        selected = select_files_to_send([str(source_dir)], None, '0', tracker)

    assert [f.name for f in selected] == ['today.txt']


def test_select_files_range_continuation_from_last_sent(tmp_path):
    """Test that the range starts the day after the last successful send."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    day_minus_2 = source_dir / 'day_minus_2.txt'
    day_minus_1 = source_dir / 'day_minus_1.txt'
    day_0 = source_dir / 'day_0.txt'
    for f in (day_minus_2, day_minus_1, day_0):
        f.touch()
    _set_mtime(day_minus_2, datetime.now() - timedelta(days=2))
    _set_mtime(day_minus_1, datetime.now() - timedelta(days=1))

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(day_minus_2, success=True)
        selected = select_files_to_send([str(source_dir)], None, '0', tracker)

    assert {f.name for f in selected} == {'day_minus_1.txt', 'day_0.txt'}


def test_select_files_unions_failed_files(tmp_path):
    """Test that previously-failed files are retried regardless of date."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    old_failed_file = source_dir / 'old_failed.txt'
    today_file = source_dir / 'today.txt'
    old_failed_file.touch()
    today_file.touch()
    _set_mtime(old_failed_file, datetime.now() - timedelta(days=30))

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(old_failed_file, success=False, error='boom')
        selected = select_files_to_send([str(source_dir)], None, '0', tracker)

    assert {f.name for f in selected} == {'old_failed.txt', 'today.txt'}


def test_select_files_dedupes_overlap_between_range_and_failed(tmp_path):
    """Test that a file present in both range and failed lists appears once."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    today_file = source_dir / 'today.txt'
    today_file.touch()

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(today_file, success=False, error='boom')
        selected = select_files_to_send([str(source_dir)], None, '0', tracker)

    assert [f.name for f in selected] == ['today.txt']


def test_select_files_skips_missing_failed_file(tmp_path):
    """Test that a failed file no longer on disk is skipped, not erroring."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    deleted_file = source_dir / 'deleted.txt'
    deleted_file.touch()

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(deleted_file, success=False, error='boom')
        deleted_file.unlink()
        selected = select_files_to_send([str(source_dir)], None, '0', tracker)

    assert selected == []


def test_select_files_no_time_delta_sends_everything_plus_failed(tmp_path):
    """Without TIME_DELTA, all files send; failed ones are still unioned."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    old_file = source_dir / 'old.txt'
    new_file = source_dir / 'new.txt'
    old_file.touch()
    new_file.touch()
    _set_mtime(old_file, datetime.now() - timedelta(days=30))

    other_dir = tmp_path / 'other'
    other_dir.mkdir()
    failed_file = other_dir / 'failed.txt'
    failed_file.touch()

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(failed_file, success=False, error='boom')
        selected = select_files_to_send(
            [str(source_dir)], None, None, tracker,
        )

    assert {f.name for f in selected} == {'old.txt', 'new.txt', 'failed.txt'}
