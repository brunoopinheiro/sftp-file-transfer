import sqlite3
from datetime import date

import pytest

from sftp_file_transfer.components.history_tracker import HistoryTracker


def test_creates_schema_on_enter(tmp_path):
    """Test that entering the context manager creates the schema."""
    db_path = tmp_path / 'history.db'

    with HistoryTracker(db_path):
        pass

    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    conn.close()
    assert ('send_history',) in tables


def test_hash_path_is_stable_across_relative_and_absolute_paths(tmp_path):
    """Test that hash_path is stable for the same resolved path."""
    file_path = tmp_path / 'file1.txt'
    file_path.touch()

    absolute_hash = HistoryTracker.hash_path(file_path)
    relative_hash = HistoryTracker.hash_path(str(file_path))

    assert absolute_hash == relative_hash


def test_record_attempt_success_sets_sent_true_and_sent_at(tmp_path):
    """Test recording a successful send attempt."""
    db_path = tmp_path / 'history.db'
    file_path = tmp_path / 'file1.txt'
    file_path.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_path, success=True)

        row = tracker._conn.execute(
            'SELECT * FROM send_history WHERE path_hash = ?',
            (HistoryTracker.hash_path(file_path),),
        ).fetchone()

        assert row['sent'] == 1
        assert row['sent_at'] is not None
        assert row['last_error'] is None


def test_record_attempt_failure_sets_sent_false_and_last_error(tmp_path):
    """Test recording a failed send attempt."""
    db_path = tmp_path / 'history.db'
    file_path = tmp_path / 'file1.txt'
    file_path.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(
            file_path, success=False, error='connection lost',
        )

        row = tracker._conn.execute(
            'SELECT * FROM send_history WHERE path_hash = ?',
            (HistoryTracker.hash_path(file_path),),
        ).fetchone()

        assert row['sent'] == 0
        assert row['last_error'] == 'connection lost'
        assert row['sent_at'] is None


def test_record_attempt_upsert_increments_attempts_on_retry(tmp_path):
    """Test that repeated attempts on the same file increment attempts."""
    db_path = tmp_path / 'history.db'
    file_path = tmp_path / 'file1.txt'
    file_path.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_path, success=False, error='timeout')
        tracker.record_attempt(file_path, success=True)

        row = tracker._conn.execute(
            'SELECT * FROM send_history WHERE path_hash = ?',
            (HistoryTracker.hash_path(file_path),),
        ).fetchone()

        expected_attempts = 2
        assert row['attempts'] == expected_attempts
        assert row['sent'] == 1


def test_get_last_sent_date_returns_none_when_empty(tmp_path):
    """Test get_last_sent_date on an empty database."""
    db_path = tmp_path / 'history.db'

    with HistoryTracker(db_path) as tracker:
        assert tracker.get_last_sent_date() is None


def test_get_last_sent_date_returns_max_date_among_sent_rows(tmp_path):
    """Test get_last_sent_date returns the newest successfully-sent date."""
    db_path = tmp_path / 'history.db'
    older_file = tmp_path / 'older.txt'
    newer_file = tmp_path / 'newer.txt'
    older_file.touch()
    newer_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(older_file, success=True)
        tracker.record_attempt(newer_file, success=True)

        assert tracker.get_last_sent_date() == date.today()


def test_get_pending_failed_files_returns_only_unsent_rows(tmp_path):
    """Test get_pending_failed_files filters to sent=0 rows."""
    db_path = tmp_path / 'history.db'
    failed_file = tmp_path / 'failed.txt'
    sent_file = tmp_path / 'sent.txt'
    failed_file.touch()
    sent_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(failed_file, success=False, error='oops')
        tracker.record_attempt(sent_file, success=True)

        pending = tracker.get_pending_failed_files()

        assert pending == [failed_file.resolve()]


def test_context_manager_commits_and_closes_connection(tmp_path):
    """Test that the connection is closed after exiting the context."""
    db_path = tmp_path / 'history.db'

    with HistoryTracker(db_path) as tracker:
        conn_ref = tracker._conn

    assert tracker._conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        conn_ref.execute('SELECT 1')
