from typer.testing import CliRunner

from sftp_file_transfer.components.history_tracker import HistoryTracker
from sftp_file_transfer.history_cli import app

runner = CliRunner()


def _seed(db_path, sent_name='sent.txt', failed_name='failed.txt'):
    sent_file = db_path.parent / sent_name
    failed_file = db_path.parent / failed_name
    sent_file.touch()
    failed_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='oops')

    return sent_file, failed_file


def test_list_command_shows_all_records(tmp_path):
    """Test the list command with no filters shows every record."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(app, ['list', '--db', str(db_path)])

    assert result.exit_code == 0
    assert 'sent.txt' in result.stdout
    assert 'failed.txt' in result.stdout


def test_list_command_filters_by_status_failed(tmp_path):
    """Test the list command with --status failed shows only failures."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(
        app, ['list', '--status', 'failed', '--db', str(db_path)],
    )

    assert result.exit_code == 0
    assert 'failed.txt' in result.stdout
    assert 'sent.txt' not in result.stdout


def test_list_command_rejects_invalid_status(tmp_path):
    """Test the list command rejects an invalid --status value."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(
        app, ['list', '--status', 'bogus', '--db', str(db_path)],
    )

    assert result.exit_code != 0


def test_failures_command_shows_only_pending(tmp_path):
    """Test the failures command shows only sent=0 rows."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(app, ['failures', '--db', str(db_path)])

    assert result.exit_code == 0
    assert 'failed.txt' in result.stdout
    assert 'sent.txt' not in result.stdout


def test_report_command_shows_summary_counts(tmp_path):
    """Test the report command renders aggregate counts."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(app, ['report', '--db', str(db_path)])

    assert result.exit_code == 0
    assert 'Total tracked' in result.stdout
    assert 'Last sent date' in result.stdout


def test_reset_command_happy_path_single_match_with_yes_flag(tmp_path):
    """Test the reset command resets a single match with --yes."""
    db_path = tmp_path / 'history.db'
    _, failed_file = _seed(db_path)
    identifier = HistoryTracker.hash_path(failed_file)

    result = runner.invoke(
        app, ['reset', identifier, '--yes', '--db', str(db_path)],
    )

    assert result.exit_code == 0
    assert 'Reset 1 record' in result.stdout


def test_reset_command_not_found_exits_nonzero(tmp_path):
    """Test the reset command exits non-zero when nothing matches."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(
        app, ['reset', 'does-not-exist', '--db', str(db_path)],
    )

    assert result.exit_code != 0


def test_reset_command_multiple_matches_without_yes_prompts_and_aborts(
    tmp_path,
):
    """Test the reset command prompts on multiple matches and aborts."""
    db_path = tmp_path / 'history.db'
    subdir = tmp_path / 'batch'
    subdir.mkdir()
    file_a = subdir / 'a.txt'
    file_b = subdir / 'b.txt'
    file_a.touch()
    file_b.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_a, success=False, error='oops')
        tracker.record_attempt(file_b, success=False, error='oops')

    result = runner.invoke(
        app, ['reset', 'batch', '--db', str(db_path)], input='n\n',
    )

    assert result.exit_code == 0
    assert 'Reset 2 record' not in result.stdout

    with HistoryTracker(db_path) as tracker:
        pending = tracker.get_pending_failed_files()

    expected_pending = 2
    assert len(pending) == expected_pending
