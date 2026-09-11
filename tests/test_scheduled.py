import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sftp_file_transfer.components.history_tracker import HistoryTracker
from sftp_file_transfer.scheduled import scheduled_task, select_files_to_send


def _set_mtime(file_path, when):
    timestamp = when.timestamp()
    os.utime(file_path, (timestamp, timestamp))


def test_select_files_selects_unsent_file(tmp_path):
    """Test that a file with no ledger record is selected."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    new_file = source_dir / 'new.txt'
    new_file.touch()

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        selected = select_files_to_send([str(source_dir)], None, tracker)

    assert [f.name for f in selected] == ['new.txt']


def test_select_files_excludes_already_sent_file(tmp_path):
    """Test that a file already marked sent=1 is not re-selected."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    sent_file = source_dir / 'sent.txt'
    sent_file.touch()

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        selected = select_files_to_send([str(source_dir)], None, tracker)

    assert selected == []


def test_select_files_unions_failed_files(tmp_path):
    """Test that previously-failed files are retried."""
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
        selected = select_files_to_send([str(source_dir)], None, tracker)

    assert {f.name for f in selected} == {'old_failed.txt', 'today.txt'}


def test_select_files_dedupes_overlap_between_candidates_and_failed(
    tmp_path,
):
    """Test that a file present in both candidate and failed lists
    appears once."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    today_file = source_dir / 'today.txt'
    today_file.touch()

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(today_file, success=False, error='boom')
        selected = select_files_to_send([str(source_dir)], None, tracker)

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
        selected = select_files_to_send([str(source_dir)], None, tracker)

    assert selected == []


def test_select_files_across_multiple_directories(tmp_path):
    """Test that unsent files across multiple directories are all
    selected, and failed files from any directory are unioned in."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    new_file = source_dir / 'new.txt'
    new_file.touch()

    other_dir = tmp_path / 'other'
    other_dir.mkdir()
    failed_file = other_dir / 'failed.txt'
    failed_file.touch()

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(failed_file, success=False, error='boom')
        selected = select_files_to_send(
            [str(source_dir), str(other_dir)],
            None,
            tracker,
        )

    assert {f.name for f in selected} == {'new.txt', 'failed.txt'}


def test_select_files_sends_both_charge_and_cancellation_files(tmp_path):
    """Test that a charge file and its differently-suffixed cancellation
    file are both selected as independent, unrelated files."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    charge_file = source_dir / 'folio123_CHARGE.txt'
    cancel_file = source_dir / 'folio123_CANCEL.txt'
    charge_file.touch()
    cancel_file.touch()

    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        selected = select_files_to_send([str(source_dir)], None, tracker)

    assert {f.name for f in selected} == {
        'folio123_CHARGE.txt',
        'folio123_CANCEL.txt',
    }


def test_scheduled_task_still_sends_when_generator_fails(
    tmp_path,
    monkeypatch,
):
    """Test that a generator-script failure is logged but does not
    prevent the send step from running."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    pending_file = source_dir / 'pending.txt'
    pending_file.touch()

    monkeypatch.setenv('SFTP_HOST', 'localhost')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'user')
    monkeypatch.setenv('SFTP_PASSWORD', 'pw')
    monkeypatch.setenv('LOCAL_PATH', str(source_dir))
    monkeypatch.setenv('REMOTE_PATH', '/uploads')
    monkeypatch.setenv('HISTORY_DB_PATH', str(tmp_path / 'history.db'))
    monkeypatch.setenv('GENERATOR_SCRIPT_PATH', 'C:/scripts/generate.ps1')
    monkeypatch.delenv('FILE_EXTENSION', raising=False)
    monkeypatch.delenv('GENERATOR_TIMEOUT_SECONDS', raising=False)

    mock_sftp = MagicMock()
    mock_manager = MagicMock()
    mock_manager.__enter__.return_value = mock_sftp

    with (
        patch(
            'sftp_file_transfer.scheduled.run_generator_script',
            side_effect=RuntimeError('generation failed'),
        ) as mock_generator,
        patch(
            'sftp_file_transfer.scheduled.SFTPManager',
            return_value=mock_manager,
        ),
    ):
        scheduled_task()

    mock_generator.assert_called_once()
    mock_sftp.upload_file.assert_called_once_with(
        local_path=pending_file,
        remote_path=f'/uploads/{pending_file.name}',
    )


def test_scheduled_task_swallows_missing_local_and_remote_path_env(
    monkeypatch,
):
    """Test that missing LOCAL_PATH and REMOTE_PATH env vars are caught
    and logged without raising an exception."""
    monkeypatch.setenv('SFTP_HOST', 'localhost')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'user')
    monkeypatch.setenv('SFTP_PASSWORD', 'pw')
    # Set to '' rather than delenv: EnvLoader() calls load_dotenv(), which
    # only skips vars already present in os.environ (even as ''). If we
    # delenv'd instead, the real project .env (which defines these) would
    # get reloaded here, silently defeating this test.
    monkeypatch.setenv('LOCAL_PATH', '')
    monkeypatch.setenv('REMOTE_PATH', '')
    monkeypatch.delenv('GENERATOR_SCRIPT_PATH', raising=False)

    # Should not raise - the ValueError for missing LOCAL_PATH/REMOTE_PATH
    # is caught by the function's outer except and logged
    scheduled_task()


def test_scheduled_task_continues_after_one_file_upload_fails(
    tmp_path,
    monkeypatch,
):
    """Test that when one file fails to upload, the scheduled task
    continues processing remaining files and records both attempts in
    the history DB."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    fail_file = source_dir / 'fail.txt'
    ok_file = source_dir / 'ok.txt'
    fail_file.touch()
    ok_file.touch()

    monkeypatch.setenv('SFTP_HOST', 'localhost')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'user')
    monkeypatch.setenv('SFTP_PASSWORD', 'pw')
    monkeypatch.setenv('LOCAL_PATH', str(source_dir))
    monkeypatch.setenv('REMOTE_PATH', '/uploads')
    monkeypatch.setenv('HISTORY_DB_PATH', str(tmp_path / 'history.db'))
    monkeypatch.delenv('FILE_EXTENSION', raising=False)
    monkeypatch.delenv('GENERATOR_SCRIPT_PATH', raising=False)

    def upload_side_effect(local_path, remote_path):
        """Raise for fail.txt, succeed for ok.txt."""
        if local_path.name == 'fail.txt':
            raise Exception('Upload failed for fail.txt')

    mock_sftp = MagicMock()
    mock_sftp.upload_file.side_effect = upload_side_effect
    mock_manager = MagicMock()
    mock_manager.__enter__.return_value = mock_sftp

    with patch(
        'sftp_file_transfer.scheduled.SFTPManager',
        return_value=mock_manager,
    ):
        scheduled_task()

    # Both files should have been attempted
    expected_upload_attempts = 2
    assert mock_sftp.upload_file.call_count == expected_upload_attempts

    # Check the history DB records
    db_path = tmp_path / 'history.db'
    with HistoryTracker(db_path) as tracker:
        fail_records = tracker.find_records('fail.txt')
        ok_records = tracker.find_records('ok.txt')

    # fail.txt should have sent=0 and a non-null error
    assert len(fail_records) == 1
    fail_record = fail_records[0]
    assert fail_record['sent'] == 0
    assert fail_record['last_error'] is not None

    # ok.txt should have sent=1
    assert len(ok_records) == 1
    ok_record = ok_records[0]
    assert ok_record['sent'] == 1
