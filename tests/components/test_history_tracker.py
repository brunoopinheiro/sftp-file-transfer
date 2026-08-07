import os
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from sftp_file_transfer.components.history_tracker import HistoryTracker


def _set_mtime(file_path, when):
    timestamp = when.timestamp()
    os.utime(file_path, (timestamp, timestamp))


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


def test_get_sent_hashes_returns_empty_set_when_empty(tmp_path):
    """Test get_sent_hashes on an empty database."""
    db_path = tmp_path / 'history.db'

    with HistoryTracker(db_path) as tracker:
        assert tracker.get_sent_hashes() == set()


def test_get_sent_hashes_returns_only_sent_rows(tmp_path):
    """Test get_sent_hashes excludes failed/pending rows."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    failed_file = tmp_path / 'failed.txt'
    sent_file.touch()
    failed_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='oops')

        assert tracker.get_sent_hashes() == {
            HistoryTracker.hash_path(sent_file),
        }


def test_context_manager_commits_and_closes_connection(tmp_path):
    """Test that the connection is closed after exiting the context."""
    db_path = tmp_path / 'history.db'

    with HistoryTracker(db_path) as tracker:
        conn_ref = tracker._conn

    assert tracker._conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        conn_ref.execute('SELECT 1')


def test_list_records_returns_all_when_no_filter(tmp_path):
    """Test list_records with no filters returns every row."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    failed_file = tmp_path / 'failed.txt'
    sent_file.touch()
    failed_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='oops')

        rows = tracker.list_records()

        expected_len = 2
        assert len(rows) == expected_len


def test_list_records_filters_by_sent_true(tmp_path):
    """Test list_records(sent=True) returns only sent rows."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    failed_file = tmp_path / 'failed.txt'
    sent_file.touch()
    failed_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='oops')

        rows = tracker.list_records(sent=True)

        assert [r['file_name'] for r in rows] == ['sent.txt']


def test_list_records_filters_by_sent_false(tmp_path):
    """Test list_records(sent=False) returns only pending rows."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    failed_file = tmp_path / 'failed.txt'
    sent_file.touch()
    failed_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='oops')

        rows = tracker.list_records(sent=False)

        assert [r['file_name'] for r in rows] == ['failed.txt']


def test_list_records_filters_by_date_range(tmp_path):
    """Test list_records filters by an inclusive file_date range."""
    db_path = tmp_path / 'history.db'
    today = datetime.now()
    old_file = tmp_path / 'old.txt'
    mid_file = tmp_path / 'mid.txt'
    new_file = tmp_path / 'new.txt'
    old_file.touch()
    mid_file.touch()
    new_file.touch()
    _set_mtime(old_file, today - timedelta(days=10))
    _set_mtime(mid_file, today - timedelta(days=5))

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(old_file, success=True)
        tracker.record_attempt(mid_file, success=True)
        tracker.record_attempt(new_file, success=True)

        rows = tracker.list_records(
            since=(today - timedelta(days=6)).date(),
            until=today.date(),
        )

        assert {r['file_name'] for r in rows} == {'mid.txt', 'new.txt'}


def test_list_records_orders_by_file_date_desc(tmp_path):
    """Test list_records orders newest file_date first."""
    db_path = tmp_path / 'history.db'
    today = datetime.now()
    old_file = tmp_path / 'old.txt'
    new_file = tmp_path / 'new.txt'
    old_file.touch()
    new_file.touch()
    _set_mtime(old_file, today - timedelta(days=3))

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(old_file, success=True)
        tracker.record_attempt(new_file, success=True)

        rows = tracker.list_records()

        assert [r['file_name'] for r in rows] == ['new.txt', 'old.txt']


def test_get_summary_on_empty_db_returns_zero_counts_and_none_dates(
    tmp_path,
):
    """Test get_summary on an empty database."""
    db_path = tmp_path / 'history.db'

    with HistoryTracker(db_path) as tracker:
        summary = tracker.get_summary()

    assert summary == {
        'total': 0,
        'total_sent': 0,
        'total_pending': 0,
        'date_range_start': None,
        'date_range_end': None,
        'last_sent_date': None,
    }


def test_get_summary_counts_and_date_range_match_records(tmp_path):
    """Test get_summary aggregates counts and date range correctly."""
    db_path = tmp_path / 'history.db'
    today = datetime.now()
    old_file = tmp_path / 'old.txt'
    new_file = tmp_path / 'new.txt'
    failed_file = tmp_path / 'failed.txt'
    old_file.touch()
    new_file.touch()
    failed_file.touch()
    _set_mtime(old_file, today - timedelta(days=7))

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(old_file, success=True)
        tracker.record_attempt(new_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='oops')

        summary = tracker.get_summary()

        expected_total = 3
        expected_sent = 2
        expected_pending = 1
        assert summary['total'] == expected_total
        assert summary['total_sent'] == expected_sent
        assert summary['total_pending'] == expected_pending
        expected_start = (today - timedelta(days=7)).date()
        assert summary['date_range_start'] == expected_start
        assert summary['date_range_end'] == today.date()


def test_get_summary_last_sent_date_matches_get_last_sent_date(tmp_path):
    """Test get_summary reuses get_last_sent_date rather than
    reimplementing it."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)

        summary = tracker.get_summary()

        assert summary['last_sent_date'] == tracker.get_last_sent_date()


def test_find_records_matches_by_full_hash(tmp_path):
    """Test find_records resolves an exact path_hash."""
    db_path = tmp_path / 'history.db'
    file_path = tmp_path / 'file1.txt'
    file_path.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_path, success=True)

        found = tracker.find_records(HistoryTracker.hash_path(file_path))

        assert [r['file_name'] for r in found] == ['file1.txt']


def test_find_records_matches_by_path_substring_case_insensitive(tmp_path):
    """Test find_records resolves a case-insensitive path substring."""
    db_path = tmp_path / 'history.db'
    file_path = tmp_path / 'File1.txt'
    file_path.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_path, success=True)

        found = tracker.find_records('file1.txt')

        assert [r['file_name'] for r in found] == ['File1.txt']


def test_find_records_returns_empty_for_no_match(tmp_path):
    """Test find_records returns [] when nothing matches."""
    db_path = tmp_path / 'history.db'

    with HistoryTracker(db_path) as tracker:
        assert tracker.find_records('does-not-exist') == []


def test_reset_record_flips_sent_to_zero_without_touching_attempts_or_error(
    tmp_path,
):
    """Test reset_record clears sent but preserves attempts/last_error."""
    db_path = tmp_path / 'history.db'
    file_path = tmp_path / 'file1.txt'
    file_path.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_path, success=False, error='boom')

        updated = tracker.reset_record(HistoryTracker.hash_path(file_path))

        expected_len = 1
        assert len(updated) == expected_len
        row = updated[0]
        assert row['sent'] == 0
        assert row['attempts'] == expected_len
        assert row['last_error'] == 'boom'


def test_reset_record_clears_sent_at(tmp_path):
    """Test reset_record clears sent_at to avoid stale timestamps."""
    db_path = tmp_path / 'history.db'
    file_path = tmp_path / 'file1.txt'
    file_path.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_path, success=True)

        updated = tracker.reset_record(HistoryTracker.hash_path(file_path))

        assert updated[0]['sent_at'] is None


def test_reset_record_on_unknown_identifier_returns_empty_list(tmp_path):
    """Test reset_record returns [] when nothing matches."""
    db_path = tmp_path / 'history.db'

    with HistoryTracker(db_path) as tracker:
        assert tracker.reset_record('does-not-exist') == []


def test_reset_record_bulk_matches_multiple_rows_by_shared_substring(
    tmp_path,
):
    """Test reset_record resets every row matching a shared substring."""
    db_path = tmp_path / 'history.db'
    subdir = tmp_path / 'batch'
    subdir.mkdir()
    file_a = subdir / 'a.txt'
    file_b = subdir / 'b.txt'
    file_a.touch()
    file_b.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_a, success=True)
        tracker.record_attempt(file_b, success=True)

        updated = tracker.reset_record('batch')

        expected_len = 2
        assert len(updated) == expected_len
        assert all(row['sent'] == 0 for row in updated)
