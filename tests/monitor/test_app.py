import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.widgets import Button, SelectionList, TextArea

from sftp_file_transfer.components.history_tracker import HistoryTracker
from sftp_file_transfer.monitor.app import (
    DashboardScreen,
    HelpScreen,
    HistoryScreen,
    MonitorApp,
    ResendScreen,
)
from sftp_file_transfer.monitor.state import DashboardState, LogEntry


@pytest.fixture(autouse=True)
def _isolate_local_path_env(monkeypatch):
    """Prevent HistoryScreen's pending-file scan from picking up the
    developer's real LOCAL_PATH/FILE_EXTENSION — other tests in the
    same process call load_dotenv() (via EnvLoader/NfceConfig), which
    leaks the real .env's values into os.environ for the rest of the
    session unless explicitly cleared here.
    """
    monkeypatch.setenv('LOCAL_PATH', '')
    monkeypatch.setenv('FILE_EXTENSION', '')


def _run(coro):
    asyncio.run(coro)


async def _settle(app, pilot):
    """Wait for background data loads to finish and the UI to catch up.

    The History and Resend screens gather their rows on worker threads,
    so a test must let those finish before asserting on the widgets.
    """
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_default_screen_is_dashboard(tmp_path):
    """Test the app starts on the Dashboard screen."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test():
            assert isinstance(app.screen, DashboardScreen)

    _run(scenario())


def test_f2_switches_to_history_screen(tmp_path):
    """Test pressing F2 switches to the History screen."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            assert isinstance(app.screen, HistoryScreen)

    _run(scenario())


def test_f3_switches_to_help_screen(tmp_path):
    """Test pressing F3 switches to the Help screen."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f3')
            assert isinstance(app.screen, HelpScreen)

    _run(scenario())


def test_f1_returns_to_dashboard_from_another_screen(tmp_path):
    """Test pressing F1 returns to the Dashboard from another screen."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f3')
            await pilot.press('f1')
            assert isinstance(app.screen, DashboardScreen)

    _run(scenario())


def test_q_detaches_without_stopping_the_daemon(tmp_path):
    """Test pressing 'q' marks the app as detached rather than sending a
    stop command to the daemon."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('q')
            assert app.detached is True

    _run(scenario())


def test_ctrl_q_sends_stop_command_and_exits(tmp_path):
    """Test pressing 'ctrl+q' sends a stop command to the daemon and
    exits the app (unlike 'q', which only detaches)."""

    async def empty_stream():
        return
        yield  # pragma: no cover

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.send_command = AsyncMock()
    mock_client.state_stream = MagicMock(return_value=empty_stream())
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        client=mock_client,
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('ctrl+q')
            await pilot.pause()
            mock_client.send_command.assert_called_once_with(
                {'cmd': 'stop'},
            )

    asyncio.run(scenario())


def test_ctrl_q_exits_cleanly_even_if_daemon_is_unreachable(tmp_path):
    """Test 'ctrl+q' still exits if sending the stop command fails
    (e.g. the daemon already died) instead of leaving the TUI hung."""

    async def empty_stream():
        return
        yield  # pragma: no cover

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.send_command = AsyncMock(side_effect=OSError('unreachable'))
    mock_client.state_stream = MagicMock(return_value=empty_stream())
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        client=mock_client,
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('ctrl+q')

    asyncio.run(scenario())


def test_ctrl_q_without_a_client_just_exits(tmp_path):
    """Test 'ctrl+q' exits cleanly even with no daemon client attached."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('ctrl+q')

    _run(scenario())


def test_registers_all_four_required_themes(tmp_path):
    """Test nord, monokai, a light theme, and high-contrast are all
    registered and selectable."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test():
            assert 'nord' in app.available_themes
            assert 'monokai' in app.available_themes
            assert 'solarized-light' in app.available_themes
            assert 'high-contrast' in app.available_themes

    _run(scenario())


def test_incompatible_ansi_themes_are_unregistered(tmp_path):
    """Test ansi-dark/ansi-light are removed, since their 'ansi_*'
    pseudo-colors crash our manually-styled Rich Text rows."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test():
            assert 'ansi-dark' not in app.available_themes
            assert 'ansi-light' not in app.available_themes

    _run(scenario())


def test_every_registered_theme_renders_without_crashing(tmp_path):
    """Test every theme left in available_themes can actually be
    activated and rendered on the Dashboard without raising."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test():
            for name in list(app.available_themes):
                app.theme = name
                app.screen.refresh_from_state()

    _run(scenario())


def test_dashboard_shows_site_name_from_state(tmp_path):
    """Test the Dashboard screen renders the current dashboard state."""
    state = DashboardState(site_name='GRANDHOTEL-SP01', cycle_num=5)
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        initial_state=state,
    )

    async def scenario():
        async with app.run_test():
            content = app.screen.query_one('#status-content').content
            assert 'GRANDHOTEL-SP01' in str(content)

    _run(scenario())


def test_dashboard_shows_log_lines_from_state(tmp_path):
    """Test the Dashboard screen renders log lines from the state."""
    state = DashboardState(
        log_lines=[LogEntry(ts='14:30:01', level='ERROR', msg='boom')],
    )
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        initial_state=state,
    )

    async def scenario():
        async with app.run_test():
            content = app.screen.query_one('#log-content').content
            assert 'boom' in str(content)

    _run(scenario())


def test_dashboard_shows_connection_status(tmp_path):
    """Test the Dashboard renders DB/SFTP connection indicators, including
    the 'N/A' state for a DB that isn't configured for this site."""
    state = DashboardState(db_connected=None, sftp_connected=True)
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        initial_state=state,
    )

    async def scenario():
        async with app.run_test():
            content = str(app.screen.query_one('#status-content').content)
            assert 'DB CONNECTION' in content
            assert 'N/A' in content
            assert 'SFTP CONNECTION' in content
            assert '● CONNECTED' in content

    _run(scenario())


def test_dashboard_shows_disconnected_state(tmp_path):
    """Test a False connection flag renders as disconnected."""
    state = DashboardState(db_connected=False, sftp_connected=False)
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        initial_state=state,
    )

    expected_disconnected_rows = 2

    async def scenario():
        async with app.run_test():
            content = str(app.screen.query_one('#status-content').content)
            disconnected_count = content.count('○ DISCONNECTED')
            assert disconnected_count == expected_disconnected_rows

    _run(scenario())


def test_dashboard_status_rows_are_left_and_right_aligned(tmp_path):
    """Test each STATUS row is a single fixed-width line: label left,
    value right, not just concatenated with a single space."""
    state = DashboardState(site_name='SITE-A', cycle_num=3)
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        initial_state=state,
    )

    async def scenario():
        async with app.run_test():
            content = str(app.screen.query_one('#status-content').content)
            cycle_line = next(
                line for line in content.split('\n') if 'CYCLE #' in line
            )
            assert cycle_line.startswith('CYCLE #')
            assert cycle_line.rstrip().endswith('3')
            assert len(cycle_line) == DashboardScreen._STATUS_ROW_WIDTH

    _run(scenario())


def test_dashboard_colors_last_cycle_by_status(tmp_path):
    """Test LAST CYCLE's row style reflects SUCCESS/PARTIAL/FAILED using
    the active theme's semantic colors."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test():
            theme = app.get_theme(app.theme)
            for status, expected_color in (
                ('SUCCESS', theme.success),
                ('PARTIAL', theme.warning),
                ('FAILED', theme.error),
            ):
                style = DashboardScreen._cycle_status_style(status, theme)
                assert style == expected_color

    _run(scenario())


def test_dashboard_connection_style_uses_theme_colors(tmp_path):
    """Test connection styling maps True/False/None to theme colors."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test():
            theme = app.get_theme(app.theme)
            assert (
                DashboardScreen._connection_style(True, theme)
                == theme.success
            )
            assert (
                DashboardScreen._connection_style(False, theme)
                == theme.error
            )
            assert DashboardScreen._connection_style(None, theme) == 'dim'

    _run(scenario())


def test_format_uptime_renders_days_hours_minutes():
    """Test _format_uptime scales its format to the elapsed duration."""
    minute = 60
    hour = 3600
    day = 86400

    assert DashboardScreen._format_uptime(0) == '0m'
    assert DashboardScreen._format_uptime(5 * minute) == '5m'
    assert DashboardScreen._format_uptime(2 * hour + 5 * minute) == '2h 5m'
    assert (
        DashboardScreen._format_uptime(3 * day + 14 * hour + 22 * minute)
        == '3d 14h 22m'
    )


def test_dashboard_shows_usage_stats_columns(tmp_path):
    """Test the USAGE STATS panel renders each count/label in its own
    column."""
    state = DashboardState(sent_count=12, failed_count=3, pending_count=1)
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        initial_state=state,
    )

    async def scenario():
        async with app.run_test():
            sent = str(app.screen.query_one('#stats-sent').content)
            failed = str(app.screen.query_one('#stats-failed').content)
            pending = str(app.screen.query_one('#stats-pending').content)
            assert '12' in sent
            assert 'SENT' in sent
            assert '3' in failed
            assert 'FAILED' in failed
            assert '1' in pending
            assert 'PENDING' in pending

    _run(scenario())


def test_stat_column_bolds_the_value_and_dims_the_label():
    """Test _stat_column applies bold/color to the number and dim to the
    label, on separate lines."""
    column = DashboardScreen._stat_column(7, 'SENT', '#00ff00')

    assert column.plain == '7\nSENT'
    value_span, label_span = column.spans
    assert 'bold' in value_span.style
    assert '#00ff00' in value_span.style
    assert label_span.style == 'dim'


def test_history_screen_lists_ledger_records(tmp_path):
    """Test the History screen's table is populated from the ledger."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            await _settle(app, pilot)
            assert table.row_count == 1

    _run(scenario())


def test_history_screen_filters_by_failed_status(tmp_path):
    """Test pressing '3' on History filters to failed-only records."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    failed_file = tmp_path / 'failed.txt'
    sent_file.touch()
    failed_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='boom')

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await pilot.press('3')
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            await _settle(app, pilot)
            assert table.row_count == 1
            row = table.get_row_at(0)
            assert row[0] == 'failed.txt'

    _run(scenario())


def test_r_sends_force_run_command_to_the_client(tmp_path):
    """Test pressing 'r' on Dashboard sends a force_run command."""
    async def empty_stream():
        return
        yield  # pragma: no cover

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.send_command = AsyncMock()
    mock_client.state_stream = MagicMock(return_value=empty_stream())
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        client=mock_client,
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('r')
            await pilot.pause()
            mock_client.send_command.assert_called_once_with(
                {'cmd': 'force_run'},
            )

    asyncio.run(scenario())


def test_p_toggles_follow_log(tmp_path):
    """Test pressing 'p' on Dashboard toggles follow_log."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            assert app.follow_log is True
            await pilot.press('p')
            assert app.follow_log is False

    asyncio.run(scenario())


def test_history_screen_filter_all_then_sent_then_clear(tmp_path):
    """Test the '1'/'2' filters and Escape-clear all update the table."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    failed_file = tmp_path / 'failed.txt'
    sent_file.touch()
    failed_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='boom')

    app = MonitorApp(history_db_path=db_path)
    total_records = 2

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await pilot.press('2')
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            await _settle(app, pilot)
            assert table.row_count == 1

            await pilot.press('1')
            await _settle(app, pilot)
            assert table.row_count == total_records

            await pilot.press('/')
            for char in 'sent':
                await pilot.press(char)
            await _settle(app, pilot)
            assert table.row_count == 1

            await pilot.press('escape')
            await _settle(app, pilot)
            assert table.row_count == total_records
            assert not app.screen.query_one('#search-input').value

    asyncio.run(scenario())


def test_history_screen_shows_new_columns(tmp_path):
    """Test the table has FILE/GENERATED/SENT/STATUS/RETRIES columns
    and a ledger row renders correctly across all of them."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=False, error='timeout')
        tracker.record_attempt(sent_file, success=True)

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            columns = [str(c.label) for c in table.columns.values()]
            assert columns == [
                'FILE',
                'GENERATED',
                'SENT',
                'STATUS',
                'RETRIES',
            ]
            row = table.get_row_at(0)
            assert row[0] == 'sent.txt'
            assert str(row[3]) == 'SENT'
            assert row[4] == '1'

    _run(scenario())


def test_history_screen_generated_column_is_a_full_datetime(tmp_path):
    """Test GENERATED shows a full datetime (from the live file's mtime),
    not just the ledger's date-only file_date."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)

    app = MonitorApp(history_db_path=db_path)
    expected_generated = HistoryScreen._file_generated_at(sent_file)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            row = table.get_row_at(0)
            assert row[1] == expected_generated
            assert 'T' in row[1]

    _run(scenario())


def test_history_screen_generated_falls_back_to_ledger_date_if_file_gone(
    tmp_path,
):
    """Test GENERATED falls back to the ledger's date-only file_date when
    the local file has since been moved or deleted."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        expected_file_date = tracker.list_records()[0].file_date
    sent_file.unlink()

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            row = table.get_row_at(0)
            assert row[1] == expected_file_date

    _run(scenario())


def test_history_screen_shows_pending_files_from_local_path(
    tmp_path,
    monkeypatch,
):
    """Test a file present on disk with no ledger record shows up as
    PENDING, distinct from an attempted-and-failed record."""
    db_path = tmp_path / 'history.db'
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    pending_file = source_dir / 'never_attempted.txt'
    failed_file = source_dir / 'failed.txt'
    pending_file.touch()
    failed_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(failed_file, success=False, error='boom')
    monkeypatch.setenv('LOCAL_PATH', str(source_dir))
    monkeypatch.setenv('FILE_EXTENSION', '')

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            statuses = {
                table.get_row_at(i)[0]: str(table.get_row_at(i)[3])
                for i in range(table.row_count)
            }
            assert statuses['never_attempted.txt'] == 'PENDING'
            assert statuses['failed.txt'] == 'FAILED'

    _run(scenario())


def test_history_screen_filter_pending_button_and_key(tmp_path, monkeypatch):
    """Test both the PENDING filter button and the '4' key isolate
    never-attempted files."""
    db_path = tmp_path / 'history.db'
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    pending_file = source_dir / 'pending.txt'
    sent_file = source_dir / 'sent.txt'
    pending_file.touch()
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
    monkeypatch.setenv('LOCAL_PATH', str(source_dir))
    monkeypatch.setenv('FILE_EXTENSION', '')

    app = MonitorApp(history_db_path=db_path)
    total_records = 2

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await pilot.press('4')
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            await _settle(app, pilot)
            assert table.row_count == 1
            await _settle(app, pilot)
            assert table.get_row_at(0)[0] == 'pending.txt'

            await pilot.click('#filter-all')
            await _settle(app, pilot)
            assert table.row_count == total_records

            await pilot.click('#filter-pending')
            await _settle(app, pilot)
            assert table.row_count == 1
            await _settle(app, pilot)
            assert table.get_row_at(0)[0] == 'pending.txt'

    _run(scenario())


def test_history_screen_filter_button_highlights_active_filter(tmp_path):
    """Test clicking a filter button switches its variant to primary and
    resets the previously-active one back to default."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            all_button = app.screen.query_one('#filter-all', Button)
            sent_button = app.screen.query_one('#filter-sent', Button)
            assert all_button.variant == 'primary'
            assert sent_button.variant == 'default'

            await pilot.click('#filter-sent')

            assert all_button.variant == 'default'
            assert sent_button.variant == 'primary'

    _run(scenario())


def test_listens_to_daemon_and_applies_streamed_state(tmp_path):
    """Test the app connects the client and applies streamed states."""
    streamed_cycle_num = 9

    async def fake_stream():
        yield DashboardState(cycle_num=streamed_cycle_num)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.state_stream = MagicMock(return_value=fake_stream())
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        client=mock_client,
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await asyncio.sleep(0.1)
            assert app.dashboard_state.cycle_num == streamed_cycle_num

    asyncio.run(scenario())
    mock_client.connect.assert_called_once()


def test_history_screen_search_filters_by_filename(tmp_path):
    """Test typing in the search box filters the History table."""
    db_path = tmp_path / 'history.db'
    alpha_file = tmp_path / 'alpha.txt'
    beta_file = tmp_path / 'beta.txt'
    alpha_file.touch()
    beta_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(alpha_file, success=True)
        tracker.record_attempt(beta_file, success=True)

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await pilot.press('/')
            for char in 'alpha':
                await pilot.press(char)
            await _settle(app, pilot)
            table = app.screen.query_one('#history-table')
            await _settle(app, pilot)
            assert table.row_count == 1

    _run(scenario())


def test_f4_switches_to_resend_screen(tmp_path):
    """Test pressing F4 switches to the Resend screen."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            assert isinstance(app.screen, ResendScreen)

    _run(scenario())


def test_resend_lookup_resolves_full_filename_and_bare_identifier(
    tmp_path,
):
    """Test lookup resolves both a pasted full filename and a pasted
    bare substring identifier to the same ledger record."""
    db_path = tmp_path / 'history.db'
    failed_file = tmp_path / 'invoice_002356.txt'
    failed_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(failed_file, success=False, error='timeout')

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            textarea = app.screen.query_one('#resend-textarea', TextArea)
            textarea.text = 'invoice_002356.txt\n002356'
            await pilot.click('#lookup-button')
            await _settle(app, pilot)
            results = app.screen.query_one('#resend-results', SelectionList)
            expected_option_count = 2
            await _settle(app, pilot)
            assert results.option_count == expected_option_count

    _run(scenario())


def test_resend_lookup_reports_lines_with_no_match(tmp_path):
    """Test a pasted line with no ledger match is reported, not added
    to the selection list."""
    app = MonitorApp(history_db_path=tmp_path / 'history.db')

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            textarea = app.screen.query_one('#resend-textarea', TextArea)
            textarea.text = 'does-not-exist'
            await pilot.click('#lookup-button')
            await _settle(app, pilot)
            results = app.screen.query_one('#resend-results', SelectionList)
            await _settle(app, pilot)
            assert results.option_count == 0
            status = str(
                app.screen.query_one('#resend-status').content,
            )
            assert 'does-not-exist' in status
            assert '1 not found' in status

    _run(scenario())


def test_resend_lookup_lists_each_ambiguous_match_separately(tmp_path):
    """Test one pasted line matching multiple records produces one
    selectable row per match."""
    db_path = tmp_path / 'history.db'
    subdir = tmp_path / 'batch'
    subdir.mkdir()
    file_a = subdir / 'a.txt'
    file_b = subdir / 'b.txt'
    file_a.touch()
    file_b.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_a, success=True)
        tracker.record_attempt(file_b, success=False, error='boom')

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            textarea = app.screen.query_one('#resend-textarea', TextArea)
            textarea.text = 'batch'
            await pilot.click('#lookup-button')
            await _settle(app, pilot)
            results = app.screen.query_one('#resend-results', SelectionList)
            expected_option_count = 2
            await _settle(app, pilot)
            assert results.option_count == expected_option_count

    _run(scenario())


def test_resend_lookup_includes_never_attempted_local_files(
    tmp_path,
    monkeypatch,
):
    """Test a pasted identifier matching a file that's on disk but has
    no ledger row yet (never attempted) still shows up as PENDING,
    instead of being reported as not found."""
    db_path = tmp_path / 'history.db'
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    never_attempted = source_dir / 'never_attempted.txt'
    never_attempted.touch()
    monkeypatch.setenv('LOCAL_PATH', str(source_dir))
    monkeypatch.setenv('FILE_EXTENSION', '')

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            textarea = app.screen.query_one('#resend-textarea', TextArea)
            textarea.text = 'never_attempted'
            await pilot.click('#lookup-button')
            await _settle(app, pilot)
            results = app.screen.query_one('#resend-results', SelectionList)
            await _settle(app, pilot)
            assert results.option_count == 1
            status = str(
                app.screen.query_one('#resend-status').content,
            )
            assert '1 matched' in status
            assert '0 not found' in status

    _run(scenario())


def test_resend_selected_includes_pending_file_in_forced_cycle(
    tmp_path,
    monkeypatch,
):
    """Test resending a never-attempted local file is a safe no-op on
    the ledger (nothing to reset) but still triggers a force_run so
    the pending file gets picked up immediately."""
    db_path = tmp_path / 'history.db'
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    pending_file = source_dir / 'pending_only.txt'
    pending_file.touch()
    monkeypatch.setenv('LOCAL_PATH', str(source_dir))
    monkeypatch.setenv('FILE_EXTENSION', '')

    async def empty_stream():
        return
        yield  # pragma: no cover

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.send_command = AsyncMock()
    mock_client.state_stream = MagicMock(return_value=empty_stream())
    app = MonitorApp(history_db_path=db_path, client=mock_client)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            textarea = app.screen.query_one('#resend-textarea', TextArea)
            textarea.text = 'pending_only.txt'
            await pilot.click('#lookup-button')
            await pilot.click('#resend-button')
            await _settle(app, pilot)
            await pilot.pause()

    asyncio.run(scenario())

    mock_client.send_command.assert_called_once_with({'cmd': 'force_run'})
    with HistoryTracker(db_path) as tracker:
        assert tracker.find_records('pending_only.txt') == []


def test_resend_selected_resets_sent_record_and_forces_a_cycle(tmp_path):
    """Test resending an already-SENT match flips it back to pending
    and sends exactly one force_run command."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)

    async def empty_stream():
        return
        yield  # pragma: no cover

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.send_command = AsyncMock()
    mock_client.state_stream = MagicMock(return_value=empty_stream())
    app = MonitorApp(history_db_path=db_path, client=mock_client)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            textarea = app.screen.query_one('#resend-textarea', TextArea)
            textarea.text = 'sent.txt'
            await pilot.click('#lookup-button')
            await pilot.click('#resend-button')
            await _settle(app, pilot)
            await pilot.pause()

    asyncio.run(scenario())

    mock_client.send_command.assert_called_once_with({'cmd': 'force_run'})
    with HistoryTracker(db_path) as tracker:
        row = tracker.find_records('sent.txt')[0]
        assert row.sent == 0


def test_resend_selected_with_deselected_row_is_excluded(tmp_path):
    """Test deselecting a matched row before resending excludes it from
    the reset."""
    db_path = tmp_path / 'history.db'
    keep_sent_file = tmp_path / 'keep_sent.txt'
    resend_file = tmp_path / 'resend_me.txt'
    keep_sent_file.touch()
    resend_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(keep_sent_file, success=True)
        tracker.record_attempt(resend_file, success=True)

    async def empty_stream():
        return
        yield  # pragma: no cover

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.send_command = AsyncMock()
    mock_client.state_stream = MagicMock(return_value=empty_stream())
    app = MonitorApp(history_db_path=db_path, client=mock_client)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            textarea = app.screen.query_one('#resend-textarea', TextArea)
            textarea.text = 'keep_sent.txt\nresend_me.txt'
            await pilot.click('#lookup-button')
            await _settle(app, pilot)
            results = app.screen.query_one(
                '#resend-results',
                SelectionList,
            )
            keep_hash = HistoryTracker.hash_path(keep_sent_file)
            results.deselect(keep_hash)
            await pilot.click('#resend-button')
            await _settle(app, pilot)
            await pilot.pause()

    asyncio.run(scenario())

    with HistoryTracker(db_path) as tracker:
        kept = tracker.find_records('keep_sent.txt')[0]
        resent = tracker.find_records('resend_me.txt')[0]
    assert kept.sent == 1
    assert resent.sent == 0


def test_resend_selected_with_nothing_selected_is_a_noop(tmp_path):
    """Test clicking Resend Selected with an empty selection list does
    not send a command or touch the ledger."""
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.send_command = AsyncMock()

    async def empty_stream():
        return
        yield  # pragma: no cover

    mock_client.state_stream = MagicMock(return_value=empty_stream())
    app = MonitorApp(
        history_db_path=tmp_path / 'history.db',
        client=mock_client,
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            await pilot.click('#resend-button')
            await _settle(app, pilot)
            await pilot.pause()
            status = str(
                app.screen.query_one('#resend-status').content,
            )
            assert 'Nothing selected' in status

    asyncio.run(scenario())

    mock_client.send_command.assert_not_called()


def test_history_rows_are_gathered_off_the_ui_thread(tmp_path):
    """Test the ledger read and LOCAL_PATH scan never block the UI."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)

    app = MonitorApp(history_db_path=db_path)
    gathering_threads = []
    original = HistoryScreen._collect_display_rows

    def recording_collect(self):
        gathering_threads.append(threading.get_ident())
        return original(self)

    HistoryScreen._collect_display_rows = recording_collect

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await _settle(app, pilot)
            assert app.screen.query_one('#history-table').row_count == 1

    try:
        _run(scenario())
    finally:
        HistoryScreen._collect_display_rows = original

    assert gathering_threads
    assert threading.get_ident() not in gathering_threads


def test_a_stale_history_load_never_overwrites_a_newer_one(tmp_path):
    """Test an outdated load's rows are discarded, not rendered.

    Every keystroke in the search box starts a load, so a slow one
    must not land after a newer one and show the wrong rows.
    """
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)

    app = MonitorApp(history_db_path=db_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f2')
            await _settle(app, pilot)
            screen = app.screen
            table = screen.query_one('#history-table')
            row_count_before = table.row_count

            stale_token = screen._refresh_token - 1
            screen._populate_table([], stale_token)

            await _settle(app, pilot)
            assert table.row_count == row_count_before

    _run(scenario())


def test_resend_lookup_runs_off_the_ui_thread(tmp_path):
    """Test the Resend screen's lookup never blocks the UI."""
    db_path = tmp_path / 'history.db'
    sent_file = tmp_path / 'resend_me.txt'
    sent_file.touch()
    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)

    app = MonitorApp(history_db_path=db_path)
    lookup_threads = []
    original = ResendScreen._collect_matches

    def recording_collect(self, lines):
        lookup_threads.append(threading.get_ident())
        return original(self, lines)

    ResendScreen._collect_matches = recording_collect

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('f4')
            textarea = app.screen.query_one('#resend-textarea', TextArea)
            textarea.text = 'resend_me.txt'
            await pilot.click('#lookup-button')
            await _settle(app, pilot)
            results = app.screen.query_one('#resend-results', SelectionList)
            await _settle(app, pilot)
            assert results.option_count == 1

    try:
        _run(scenario())
    finally:
        ResendScreen._collect_matches = original

    assert lookup_threads
    assert threading.get_ident() not in lookup_threads
