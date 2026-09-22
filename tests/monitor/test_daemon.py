import asyncio
import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sftp_file_transfer.components.history_tracker import HistoryTracker
from sftp_file_transfer.monitor.daemon import (
    MonitorDaemon,
    _is_pid_alive,  # noqa: PLC2701
    logger,
)

DEFAULT_TEST_POLL_INTERVAL_SEC = 45


def _make_daemon(tmp_path, **kwargs):
    db_path = tmp_path / 'history.db'
    lock_path = tmp_path / 'monitor.lock'
    kwargs.setdefault('poll_interval_sec', DEFAULT_TEST_POLL_INTERVAL_SEC)
    return MonitorDaemon(
        history_db_path=db_path,
        lock_path=lock_path,
        **kwargs,
    )


def _patch_connections(daemon, *, sftp_ok=True, db_ok=None):
    """Patch a daemon's connectivity probes to avoid real network I/O."""
    daemon._probe_sftp = MagicMock(return_value=sftp_ok)
    daemon._probe_db = MagicMock(return_value=db_ok)


def test_run_cycle_increments_cycle_and_refreshes_counts_from_ledger(
    tmp_path,
):
    """Test run_cycle() bumps cycle_num and pulls counts from the ledger."""
    daemon = _make_daemon(tmp_path)
    _patch_connections(daemon)
    sent_file = tmp_path / 'sent.txt'
    failed_file = tmp_path / 'failed.txt'
    sent_file.touch()
    failed_file.touch()

    with HistoryTracker(daemon.history_db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='boom')

    with patch(
        'sftp_file_transfer.monitor.daemon.scheduled_task',
    ) as mock_scheduled_task:
        daemon.run_cycle()

    mock_scheduled_task.assert_called_once()
    assert daemon.state.cycle_num == 1
    assert daemon.state.sent_count == 1
    assert daemon.state.failed_count == 1
    assert daemon.state.last_cycle_time
    assert daemon.state.countdown_sec == DEFAULT_TEST_POLL_INTERVAL_SEC


def test_run_cycle_appends_start_and_completed_log_lines(tmp_path):
    """Test run_cycle() narrates the cycle in the log ring buffer."""
    daemon = _make_daemon(tmp_path)
    _patch_connections(daemon)

    with patch('sftp_file_transfer.monitor.daemon.scheduled_task'):
        daemon.run_cycle()

    messages = [entry.msg for entry in daemon.state.log_lines]
    assert any('started' in m for m in messages)
    assert any('completed' in m for m in messages)


def test_logger_calls_are_mirrored_into_the_dashboard_log_lines(tmp_path):
    """Test that any logger.* call reaches the dashboard's LIVE LOG panel.

    This is what makes the panel show the same lines as
    `logs/sftp_file_transfer.log`, instead of just the two hand-written
    cycle start/completed narration lines.
    """
    daemon = _make_daemon(tmp_path)

    logger.error('SFTP connectivity probe failed: boom')

    messages = [entry.msg for entry in daemon.state.log_lines]
    levels = [entry.level for entry in daemon.state.log_lines]
    assert 'SFTP connectivity probe failed: boom' in messages
    assert 'ERROR' in levels


def test_creating_a_new_daemon_replaces_the_previous_dashboard_handler(
    tmp_path,
):
    """Test only the most recently created daemon receives log records.

    Guards against unboundedly stacking handlers on the shared logger
    (e.g. one per test/daemon instance created in a process).
    """
    first_daemon = _make_daemon(tmp_path)
    second_daemon = _make_daemon(tmp_path)

    logger.info('only for the current daemon')

    assert not any(
        'only for the current daemon' in entry.msg
        for entry in first_daemon.state.log_lines
    )
    assert any(
        'only for the current daemon' in entry.msg
        for entry in second_daemon.state.log_lines
    )


def test_run_cycle_status_is_success_when_healthy_and_no_new_failures(
    tmp_path,
):
    """Test a healthy cycle with no new ledger failures is SUCCESS."""
    daemon = _make_daemon(tmp_path)
    _patch_connections(daemon, sftp_ok=True, db_ok=True)

    with patch('sftp_file_transfer.monitor.daemon.scheduled_task'):
        daemon.run_cycle()

    assert daemon.state.last_cycle_status == 'SUCCESS'


def test_run_cycle_status_is_success_when_db_not_configured(tmp_path):
    """Test a send-only site (db_connected=None) doesn't count as down."""
    daemon = _make_daemon(tmp_path)
    _patch_connections(daemon, sftp_ok=True, db_ok=None)

    with patch('sftp_file_transfer.monitor.daemon.scheduled_task'):
        daemon.run_cycle()

    assert daemon.state.last_cycle_status == 'SUCCESS'


def test_run_cycle_status_is_failed_when_sftp_unreachable(tmp_path):
    """Test an unreachable SFTP target marks the cycle FAILED."""
    daemon = _make_daemon(tmp_path)
    _patch_connections(daemon, sftp_ok=False, db_ok=None)

    with patch('sftp_file_transfer.monitor.daemon.scheduled_task'):
        daemon.run_cycle()

    assert daemon.state.last_cycle_status == 'FAILED'


def test_run_cycle_status_is_failed_when_configured_db_unreachable(
    tmp_path,
):
    """Test a configured-but-unreachable DB marks the cycle FAILED."""
    daemon = _make_daemon(tmp_path)
    _patch_connections(daemon, sftp_ok=True, db_ok=False)

    with patch('sftp_file_transfer.monitor.daemon.scheduled_task'):
        daemon.run_cycle()

    assert daemon.state.last_cycle_status == 'FAILED'


def test_run_cycle_status_is_partial_when_new_failure_appears(tmp_path):
    """Test connectivity is fine but a new ledger failure is PARTIAL."""
    daemon = _make_daemon(tmp_path)
    _patch_connections(daemon, sftp_ok=True, db_ok=True)
    failed_file = tmp_path / 'failed.txt'
    failed_file.touch()

    def fake_cycle():
        with HistoryTracker(daemon.history_db_path) as tracker:
            tracker.record_attempt(failed_file, success=False, error='boom')

    with patch(
        'sftp_file_transfer.monitor.daemon.scheduled_task',
        side_effect=fake_cycle,
    ):
        daemon.run_cycle()

    assert daemon.state.last_cycle_status == 'PARTIAL'


def test_run_cycle_populates_daily_counts_from_ledger(tmp_path):
    """Test run_cycle() refreshes the 7-day sent-count chart."""
    daemon = _make_daemon(tmp_path)
    _patch_connections(daemon)
    sent_file = tmp_path / 'sent.txt'
    sent_file.touch()

    with HistoryTracker(daemon.history_db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)

    with patch('sftp_file_transfer.monitor.daemon.scheduled_task'):
        daemon.run_cycle()

    days_in_window = 7
    assert len(daemon.state.daily_counts) == days_in_window
    assert daemon.state.daily_counts[-1] == 1


def test_probe_sftp_returns_false_on_connection_error(tmp_path, monkeypatch):
    """Test _probe_sftp() returns False when the SFTP connection fails."""
    monkeypatch.setenv('SFTP_HOST', 'localhost')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'user')
    monkeypatch.setenv('SFTP_PASSWORD', 'pw')
    daemon = _make_daemon(tmp_path)

    with patch(
        'sftp_file_transfer.monitor.daemon.SFTPManager',
        side_effect=OSError('unreachable'),
    ):
        assert daemon._probe_sftp() is False


def test_probe_db_returns_none_when_not_configured(tmp_path, monkeypatch):
    """Test _probe_db() returns None when NFCe DB env vars are unset."""
    for var in (
        'NFCE_DB_HOST',
        'NFCE_DB_PORT',
        'NFCE_DB_NAME',
        'NFCE_DB_USER',
        'NFCE_DB_PASSWORD',
        'NFCE_OUTPUT_PATH',
    ):
        monkeypatch.setenv(var, '')
    daemon = _make_daemon(tmp_path)

    assert daemon._probe_db() is None


def test_probe_db_returns_false_when_configured_but_unreachable(
    tmp_path,
    monkeypatch,
):
    """Test _probe_db() returns False when configured but unreachable."""
    monkeypatch.setenv('NFCE_DB_HOST', 'db-host')
    monkeypatch.setenv('NFCE_DB_PORT', '3306')
    monkeypatch.setenv('NFCE_DB_NAME', 'CHECKPOSTINGDB')
    monkeypatch.setenv('NFCE_DB_USER', 'nfce_user')
    monkeypatch.setenv('NFCE_DB_PASSWORD', 'nfce_pw')
    monkeypatch.setenv('NFCE_OUTPUT_PATH', 'C:/output')
    daemon = _make_daemon(tmp_path)

    with patch(
        'sftp_file_transfer.monitor.daemon.build_engine',
        side_effect=RuntimeError('cannot connect'),
    ):
        assert daemon._probe_db() is False


def test_check_connections_updates_state_from_probes(tmp_path):
    """Test _check_connections() writes both probe results into state."""
    daemon = _make_daemon(tmp_path)
    daemon._probe_sftp = MagicMock(return_value=True)
    daemon._probe_db = MagicMock(return_value=False)

    daemon._check_connections()

    assert daemon.state.sftp_connected is True
    assert daemon.state.db_connected is False


def test_force_run_resets_countdown_to_zero(tmp_path):
    """Test force_run() zeroes the countdown so the next tick runs a cycle."""
    daemon = _make_daemon(tmp_path)
    daemon.state.countdown_sec = 30

    daemon.force_run()

    assert daemon.state.countdown_sec == 0


def test_write_lock_file_then_read_lock_file_round_trips(tmp_path):
    """Test the lock file records pid/port and reads back correctly."""
    daemon = _make_daemon(tmp_path)

    daemon.write_lock_file(port=54321)
    lock_info = MonitorDaemon.read_lock_file(daemon.lock_path)

    assert lock_info == {'pid': os.getpid(), 'port': 54321}


def test_read_lock_file_returns_none_when_missing(tmp_path):
    """Test read_lock_file returns None when no lock file exists."""
    missing_path = tmp_path / 'does-not-exist.lock'

    assert MonitorDaemon.read_lock_file(missing_path) is None


def test_read_lock_file_returns_none_for_corrupt_json(tmp_path):
    """Test read_lock_file returns None instead of raising on bad JSON."""
    lock_path = tmp_path / 'monitor.lock'
    lock_path.write_text('not json', encoding='utf-8')

    assert MonitorDaemon.read_lock_file(lock_path) is None


def test_remove_lock_file_deletes_it(tmp_path):
    """Test remove_lock_file deletes an existing lock file without error."""
    daemon = _make_daemon(tmp_path)
    daemon.write_lock_file(port=1)

    daemon.remove_lock_file()

    assert not daemon.lock_path.exists()


def test_remove_lock_file_is_a_noop_when_missing(tmp_path):
    """Test remove_lock_file doesn't raise when there's nothing to remove."""
    daemon = _make_daemon(tmp_path)

    daemon.remove_lock_file()


def test_is_pid_alive_true_for_current_process():
    """Test _is_pid_alive recognizes the current process as alive."""
    assert _is_pid_alive(os.getpid()) is True


def test_is_pid_alive_false_for_implausible_pid():
    """Test _is_pid_alive returns False for a pid that doesn't exist."""
    assert _is_pid_alive(999_999_999) is False


def test_server_sends_snapshot_then_streams_events_and_handles_commands(
    tmp_path,
):
    """Test the daemon's TCP protocol: snapshot on connect, then a
    broadcast event reaches the client, and force_run/stop commands are
    dispatched from a connected client."""
    daemon = _make_daemon(tmp_path)
    daemon.state.site_name = 'GRANDHOTEL-SP01'

    async def scenario():
        server = await daemon.start_server()
        port = server.sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            snapshot_line = await reader.readline()
            snapshot = json.loads(snapshot_line)
            assert snapshot['site_name'] == 'GRANDHOTEL-SP01'

            daemon.broadcast_snapshot()
            event_line = await asyncio.wait_for(reader.readline(), timeout=2)
            event = json.loads(event_line)
            assert event['site_name'] == 'GRANDHOTEL-SP01'

            with patch(
                'sftp_file_transfer.monitor.daemon.scheduled_task',
            ):
                writer.write(b'{"cmd": "force_run"}\n')
                await writer.drain()
                await asyncio.sleep(0.1)
            assert daemon.state.countdown_sec == 0

            writer.write(b'{"cmd": "stop"}\n')
            await writer.drain()
            await asyncio.sleep(0.1)
            assert daemon.stopped is True
        finally:
            writer.close()
            server.close()
            await server.wait_closed()


def test_stop_signals_only_leaving_server_and_lock_file_intact(tmp_path):
    """Test stop() only sets stopped=True — it must not close the
    server or remove the lock file itself, since a cycle may still be
    finishing on a worker thread and the process may not exit for a
    while. Premature teardown here would make _running_lock_info()
    unable to find the pid to verify/force-terminate later."""
    daemon = _make_daemon(tmp_path)
    daemon.write_lock_file(port=12345)

    async def scenario():
        server = await daemon.start_server()
        await daemon.stop()

        assert daemon.stopped is True
        assert daemon.lock_path.exists()
        assert server.is_serving()

        server.close()
        await server.wait_closed()

    asyncio.run(scenario())

    asyncio.run(scenario())


def test_run_forever_runs_a_cycle_when_countdown_reaches_zero(tmp_path):
    """Test run_forever() calls run_cycle() as soon as countdown is 0."""
    daemon = _make_daemon(tmp_path, poll_interval_sec=0)
    calls = []

    def fake_run_cycle():
        calls.append(1)
        daemon.stopped = True

    daemon.run_cycle = fake_run_cycle

    asyncio.run(daemon.run_forever())

    assert calls == [1]


def test_run_forever_decrements_countdown_each_tick(tmp_path, monkeypatch):
    """Test run_forever() ticks the countdown down to zero, then cycles."""
    daemon = _make_daemon(tmp_path, poll_interval_sec=2)
    daemon.state.countdown_sec = 2
    daemon.run_cycle = MagicMock(
        side_effect=lambda: setattr(daemon, 'stopped', True),
    )

    async def instant_sleep(_seconds):
        return None

    monkeypatch.setattr(
        'sftp_file_transfer.monitor.daemon.asyncio.sleep',
        instant_sleep,
    )

    asyncio.run(daemon.run_forever())

    daemon.run_cycle.assert_called_once()
    assert daemon.state.countdown_sec == 0


def test_run_forever_updates_uptime_sec_each_tick(tmp_path, monkeypatch):
    """Test run_forever() refreshes uptime_sec from a fixed start time."""
    daemon = _make_daemon(tmp_path, poll_interval_sec=2)
    daemon.state.countdown_sec = 1
    fixed_elapsed_sec = 42
    daemon._started_at = datetime.now() - timedelta(seconds=fixed_elapsed_sec)
    daemon.run_cycle = MagicMock(
        side_effect=lambda: setattr(daemon, 'stopped', True),
    )

    async def instant_sleep(_seconds):
        return None

    monkeypatch.setattr(
        'sftp_file_transfer.monitor.daemon.asyncio.sleep',
        instant_sleep,
    )

    asyncio.run(daemon.run_forever())

    assert daemon.state.uptime_sec >= fixed_elapsed_sec


def test_server_ignores_malformed_json_command(tmp_path):
    """Test a malformed command line is skipped without breaking the
    connection or crashing the daemon."""
    daemon = _make_daemon(tmp_path)

    async def scenario():
        server = await daemon.start_server()
        port = server.sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            await reader.readline()

            writer.write(b'not valid json\n')
            await writer.drain()

            writer.write(b'{"cmd": "force_run"}\n')
            await writer.drain()
            await asyncio.sleep(0.1)

            assert daemon.state.countdown_sec == 0
        finally:
            writer.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_broadcast_snapshot_drops_disconnected_clients_silently(tmp_path):
    """Test broadcasting to a closed writer doesn't raise."""
    daemon = _make_daemon(tmp_path)
    broken_writer = MagicMock()
    broken_writer.write.side_effect = ConnectionResetError()
    daemon._clients.add(broken_writer)

    daemon.broadcast_snapshot()

    assert broken_writer not in daemon._clients


def test_probe_db_disposes_the_engine_it_opened(tmp_path, monkeypatch):
    """Test that the per-cycle connectivity probe never leaks an engine."""
    monkeypatch.setenv('NFCE_DB_HOST', 'db-host')
    monkeypatch.setenv('NFCE_DB_PORT', '3306')
    monkeypatch.setenv('NFCE_DB_NAME', 'CHECKPOSTINGDB')
    monkeypatch.setenv('NFCE_DB_USER', 'nfce_user')
    monkeypatch.setenv('NFCE_DB_PASSWORD', 'nfce_pw')
    monkeypatch.setenv('NFCE_OUTPUT_PATH', str(tmp_path))

    fake_engine = MagicMock()
    fake_engine.connect.side_effect = OSError('unreachable')

    with patch(
        'sftp_file_transfer.monitor.daemon.build_engine',
        return_value=fake_engine,
    ):
        daemon = MonitorDaemon(history_db_path=tmp_path / 'history.db')

        assert daemon._probe_db() is False

    fake_engine.dispose.assert_called_once_with()
