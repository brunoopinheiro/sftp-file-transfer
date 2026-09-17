import asyncio
import os
from unittest.mock import patch

from typer.testing import CliRunner

from sftp_file_transfer.monitor import cli
from sftp_file_transfer.monitor.daemon import MonitorDaemon
from sftp_file_transfer.monitor.state import DashboardState

runner = CliRunner()


def test_running_lock_info_returns_none_when_no_lock_file(tmp_path):
    """Test _running_lock_info returns None when nothing is running."""
    lock_path = tmp_path / 'monitor.lock'

    assert cli._running_lock_info(lock_path) is None


def test_running_lock_info_returns_info_for_a_live_pid(tmp_path):
    """Test _running_lock_info returns the lock dict for a live process
    whose recorded port also responds."""
    lock_path = tmp_path / 'monitor.lock'
    daemon = MonitorDaemon(
        history_db_path=tmp_path / 'history.db',
        lock_path=lock_path,
    )
    daemon.write_lock_file(port=5555)

    with patch(
        'sftp_file_transfer.monitor.cli._port_responds',
        return_value=True,
    ):
        lock_info = cli._running_lock_info(lock_path)

    assert lock_info == {'pid': os.getpid(), 'port': 5555}


def test_running_lock_info_cleans_up_a_stale_lock(tmp_path):
    """Test _running_lock_info removes and ignores a lock for a dead pid."""
    lock_path = tmp_path / 'monitor.lock'
    daemon = MonitorDaemon(
        history_db_path=tmp_path / 'history.db',
        lock_path=lock_path,
    )
    daemon.write_lock_file(port=5555)

    with patch(
        'sftp_file_transfer.monitor.cli._is_pid_alive',
        return_value=False,
    ):
        lock_info = cli._running_lock_info(lock_path)

    assert lock_info is None
    assert not lock_path.exists()


def test_running_lock_info_cleans_up_a_lock_for_a_reused_pid(tmp_path):
    """Test a lock is treated as stale when the pid is alive but belongs
    to an unrelated process — e.g. after an ungraceful reboot recycled
    the pid number for something else. Pid liveness alone must not be
    trusted; the recorded port must also respond."""
    lock_path = tmp_path / 'monitor.lock'
    daemon = MonitorDaemon(
        history_db_path=tmp_path / 'history.db',
        lock_path=lock_path,
    )
    daemon.write_lock_file(port=5555)

    with patch(
        'sftp_file_transfer.monitor.cli._port_responds',
        return_value=False,
    ):
        lock_info = cli._running_lock_info(lock_path)

    assert lock_info is None
    assert not lock_path.exists()


def test_port_responds_false_when_nothing_listening():
    """Test _port_responds returns False for an unreachable port."""
    assert cli._port_responds(1, timeout=0.2) is False


def test_port_responds_true_for_a_real_listening_daemon(tmp_path):
    """Test _port_responds returns True against a genuine running server."""
    daemon = MonitorDaemon(
        history_db_path=tmp_path / 'history.db',
        lock_path=tmp_path / 'monitor.lock',
    )

    async def scenario():
        server = await daemon.start_server()
        port = server.sockets[0].getsockname()[1]
        try:
            result = await asyncio.to_thread(cli._port_responds, port)
        finally:
            server.close()
            await server.wait_closed()
        return result

    assert asyncio.run(scenario()) is True


def test_daemon_spawn_command_uses_module_invocation_when_not_frozen(
    monkeypatch,
):
    """Test the spawn command uses `-m` invocation for a source checkout."""
    monkeypatch.setattr(cli.sys, 'frozen', False, raising=False)

    command = cli._daemon_spawn_command()

    assert command == [
        cli.sys.executable,
        '-m',
        'sftp_file_transfer.monitor.cli',
        '_run_daemon',
    ]


def test_daemon_spawn_command_uses_executable_directly_when_frozen(
    monkeypatch,
):
    """Test the spawn command re-invokes the frozen exe when frozen."""
    monkeypatch.setattr(cli.sys, 'frozen', True, raising=False)

    command = cli._daemon_spawn_command()

    assert command == [cli.sys.executable, '_run_daemon']


def test_spawn_detached_daemon_uses_windows_detach_flags():
    """Test spawning passes Windows detached-process creation flags."""
    with patch(
        'sftp_file_transfer.monitor.cli.subprocess.Popen',
    ) as mock_popen:
        cli._spawn_detached_daemon()

    args, kwargs = mock_popen.call_args
    expected_flags = (
        cli.subprocess.CREATE_NEW_PROCESS_GROUP
        | cli.subprocess.DETACHED_PROCESS
    )
    assert kwargs['creationflags'] == expected_flags


def test_send_stop_command_stops_a_real_running_daemon(tmp_path):
    """Test _send_stop_command actually stops a live daemon over TCP."""
    daemon = MonitorDaemon(
        history_db_path=tmp_path / 'history.db',
        lock_path=tmp_path / 'monitor.lock',
    )

    async def scenario():
        server = await daemon.start_server()
        port = server.sockets[0].getsockname()[1]

        result = await asyncio.to_thread(cli._send_stop_command, port)

        assert result is True
        await asyncio.sleep(0.1)
        assert daemon.stopped is True
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())


def test_send_stop_command_returns_false_when_nothing_listening():
    """Test _send_stop_command returns False if the port is unreachable."""
    assert cli._send_stop_command(1) is False


def test_attach_command_errors_when_no_daemon_running(tmp_path):
    """Test `attach` exits non-zero with a clear message if nothing runs."""
    with patch(
        'sftp_file_transfer.monitor.cli._running_lock_info',
        return_value=None,
    ):
        result = runner.invoke(cli.app, ['attach'])

    assert result.exit_code != 0
    assert 'No running monitor daemon' in result.stdout


def test_attach_command_launches_the_tui_when_daemon_running():
    """Test `attach` builds a client for the daemon's port and runs the TUI."""
    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 123, 'port': 4444},
        ),
        patch('sftp_file_transfer.monitor.cli.DaemonClient') as mock_client,
        patch('sftp_file_transfer.monitor.cli.MonitorApp') as mock_app,
    ):
        result = runner.invoke(cli.app, ['attach'])

    assert result.exit_code == 0
    mock_client.assert_called_once_with(4444)
    mock_app.return_value.run.assert_called_once()


def test_attach_skips_stop_verification_when_user_detached(tmp_path):
    """Test _attach() does not verify/force-terminate the daemon when
    the TUI exited via 'q' (detach), since it's meant to keep running."""
    lock_path = tmp_path / 'monitor.lock'
    lock_path.write_text('{"pid": 123, "port": 4444}', encoding='utf-8')

    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 123, 'port': 4444},
        ),
        patch('sftp_file_transfer.monitor.cli.DaemonClient'),
        patch('sftp_file_transfer.monitor.cli.MonitorApp') as mock_app,
        patch(
            'sftp_file_transfer.monitor.cli._wait_for_exit',
        ) as mock_wait,
        patch(
            'sftp_file_transfer.monitor.cli._terminate_pid',
        ) as mock_terminate,
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
    ):
        mock_app.return_value.detached = True
        result = runner.invoke(cli.app, ['attach'])

    assert result.exit_code == 0
    mock_wait.assert_not_called()
    mock_terminate.assert_not_called()
    assert lock_path.exists()


def test_attach_force_terminates_stuck_daemon_after_stop_and_quit(
    tmp_path,
):
    """Test _attach() verifies the daemon actually exited after
    Ctrl+Q (not detached), and force-terminates it if it's still
    alive when the wait times out — the exact scenario reported: the
    daemon receives 'stop' but is stuck finishing a cycle, so sending
    the command alone is not proof it has exited."""
    lock_path = tmp_path / 'monitor.lock'
    lock_path.write_text('{"pid": 123, "port": 4444}', encoding='utf-8')

    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 123, 'port': 4444},
        ),
        patch('sftp_file_transfer.monitor.cli.DaemonClient'),
        patch('sftp_file_transfer.monitor.cli.MonitorApp') as mock_app,
        patch(
            'sftp_file_transfer.monitor.cli._wait_for_exit',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.monitor.cli._terminate_pid',
        ) as mock_terminate,
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
    ):
        mock_app.return_value.detached = False
        result = runner.invoke(cli.app, ['attach'])

    assert result.exit_code == 0
    mock_terminate.assert_called_once_with(123)
    assert not lock_path.exists()


def test_attach_skips_terminate_when_daemon_exits_on_its_own(tmp_path):
    """Test _attach() doesn't force-terminate when the daemon exits
    gracefully within the wait window after Ctrl+Q."""
    lock_path = tmp_path / 'monitor.lock'
    lock_path.write_text('{"pid": 123, "port": 4444}', encoding='utf-8')

    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 123, 'port': 4444},
        ),
        patch('sftp_file_transfer.monitor.cli.DaemonClient'),
        patch('sftp_file_transfer.monitor.cli.MonitorApp') as mock_app,
        patch(
            'sftp_file_transfer.monitor.cli._wait_for_exit',
            return_value=True,
        ),
        patch(
            'sftp_file_transfer.monitor.cli._terminate_pid',
        ) as mock_terminate,
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
    ):
        mock_app.return_value.detached = False
        result = runner.invoke(cli.app, ['attach'])

    assert result.exit_code == 0
    mock_terminate.assert_not_called()
    assert not lock_path.exists()


def test_stop_command_errors_when_no_daemon_running():
    """Test `stop` exits non-zero with a clear message if nothing runs."""
    with patch(
        'sftp_file_transfer.monitor.cli._running_lock_info',
        return_value=None,
    ):
        result = runner.invoke(cli.app, ['stop'])

    assert result.exit_code != 0
    assert 'No running monitor daemon' in result.stdout


def test_stop_command_falls_back_to_terminate_when_unreachable(
    tmp_path,
    monkeypatch,
):
    """Test `stop` terminates by pid if the daemon doesn't respond and
    stays alive."""
    lock_path = tmp_path / 'monitor.lock'
    lock_path.write_text('{"pid": 123, "port": 4444}', encoding='utf-8')
    monkeypatch.setattr(cli, '_STOP_WAIT_TIMEOUT_SEC', 0.0)

    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 123, 'port': 4444},
        ),
        patch(
            'sftp_file_transfer.monitor.cli._send_stop_command',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.monitor.cli._is_pid_alive',
            return_value=True,
        ),
        patch(
            'sftp_file_transfer.monitor.cli._terminate_pid',
        ) as mock_terminate,
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
    ):
        result = runner.invoke(cli.app, ['stop'])

    assert result.exit_code == 0
    mock_terminate.assert_called_once_with(123)
    assert not lock_path.exists()


def test_stop_command_skips_terminate_once_pid_exits(tmp_path):
    """Test `stop` does not force-terminate a pid that exits on its own
    once the graceful stop command has been sent."""
    lock_path = tmp_path / 'monitor.lock'
    lock_path.write_text('{"pid": 123, "port": 4444}', encoding='utf-8')

    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 123, 'port': 4444},
        ),
        patch(
            'sftp_file_transfer.monitor.cli._send_stop_command',
            return_value=True,
        ),
        patch(
            'sftp_file_transfer.monitor.cli._is_pid_alive',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.monitor.cli._terminate_pid',
        ) as mock_terminate,
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
    ):
        result = runner.invoke(cli.app, ['stop'])

    assert result.exit_code == 0
    mock_terminate.assert_not_called()
    assert not lock_path.exists()


def test_stop_command_force_terminates_a_daemon_stuck_alive(
    tmp_path,
    monkeypatch,
):
    """Test `stop` force-terminates a daemon that acknowledged the stop
    command over TCP but is still alive when the wait times out — the
    exact scenario that used to leave an orphaned process behind."""
    lock_path = tmp_path / 'monitor.lock'
    lock_path.write_text('{"pid": 123, "port": 4444}', encoding='utf-8')
    monkeypatch.setattr(cli, '_STOP_WAIT_TIMEOUT_SEC', 0.0)

    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 123, 'port': 4444},
        ),
        patch(
            'sftp_file_transfer.monitor.cli._send_stop_command',
            return_value=True,
        ),
        patch(
            'sftp_file_transfer.monitor.cli._is_pid_alive',
            return_value=True,
        ),
        patch(
            'sftp_file_transfer.monitor.cli._terminate_pid',
        ) as mock_terminate,
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
    ):
        result = runner.invoke(cli.app, ['stop'])

    assert result.exit_code == 0
    mock_terminate.assert_called_once_with(123)
    assert not lock_path.exists()


def test_wait_for_exit_returns_true_immediately_when_already_dead():
    """Test _wait_for_exit short-circuits when the pid is already gone."""
    with patch(
        'sftp_file_transfer.monitor.cli._is_pid_alive',
        return_value=False,
    ):
        assert cli._wait_for_exit(123, timeout=5.0) is True


def test_wait_for_exit_returns_false_when_pid_outlives_timeout():
    """Test _wait_for_exit reports failure once the timeout elapses."""
    with patch(
        'sftp_file_transfer.monitor.cli._is_pid_alive',
        return_value=True,
    ):
        assert cli._wait_for_exit(123, timeout=0.0) is False


def test_status_command_reports_not_running():
    """Test `status` reports cleanly when no daemon is running."""
    with patch(
        'sftp_file_transfer.monitor.cli._running_lock_info',
        return_value=None,
    ):
        result = runner.invoke(cli.app, ['status'])

    assert result.exit_code == 0
    assert 'No monitor daemon is running' in result.stdout


def test_status_command_reports_live_snapshot_info():
    """Test `status` prints pid/cycle info from a live snapshot."""
    snapshot = DashboardState(site_name='GRANDHOTEL-SP01', cycle_num=42)
    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 123, 'port': 4444},
        ),
        patch(
            'sftp_file_transfer.monitor.cli._fetch_snapshot',
            return_value=snapshot,
        ),
    ):
        result = runner.invoke(cli.app, ['status'])

    assert result.exit_code == 0
    assert 'GRANDHOTEL-SP01' in result.stdout
    assert '42' in result.stdout


def test_main_callback_spawns_daemon_and_attaches_when_none_running():
    """Test the default command spawns a daemon then attaches when none
    is already running."""
    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            side_effect=[None, {'pid': 1, 'port': 4444}],
        ),
        patch(
            'sftp_file_transfer.monitor.cli._spawn_detached_daemon',
        ) as mock_spawn,
        patch('sftp_file_transfer.monitor.cli.DaemonClient'),
        patch('sftp_file_transfer.monitor.cli.MonitorApp') as mock_app,
    ):
        result = runner.invoke(cli.app, [])

    assert result.exit_code == 0
    mock_spawn.assert_called_once()
    mock_app.return_value.run.assert_called_once()


def test_main_callback_attaches_directly_when_daemon_already_running():
    """Test the default command skips spawning if a daemon is already up."""
    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value={'pid': 1, 'port': 4444},
        ),
        patch(
            'sftp_file_transfer.monitor.cli._spawn_detached_daemon',
        ) as mock_spawn,
        patch('sftp_file_transfer.monitor.cli.DaemonClient'),
        patch('sftp_file_transfer.monitor.cli.MonitorApp') as mock_app,
    ):
        result = runner.invoke(cli.app, [])

    assert result.exit_code == 0
    mock_spawn.assert_not_called()
    mock_app.return_value.run.assert_called_once()


def test_main_callback_detach_flag_does_not_attach_a_tui():
    """Test --detach spawns the daemon but never launches a TUI."""
    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            side_effect=[None, {'pid': 1, 'port': 4444}],
        ),
        patch('sftp_file_transfer.monitor.cli._spawn_detached_daemon'),
        patch('sftp_file_transfer.monitor.cli.MonitorApp') as mock_app,
    ):
        result = runner.invoke(cli.app, ['--detach'])

    assert result.exit_code == 0
    mock_app.assert_not_called()


def test_main_callback_errors_when_daemon_never_starts():
    """Test the default command reports failure if the daemon never
    writes its lock file within the wait timeout."""
    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            return_value=None,
        ),
        patch('sftp_file_transfer.monitor.cli._spawn_detached_daemon'),
        patch(
            'sftp_file_transfer.monitor.cli._wait_for_daemon',
            return_value=None,
        ),
    ):
        result = runner.invoke(cli.app, [])

    assert result.exit_code != 0
    assert 'Failed to start' in result.stdout


def test_run_daemon_command_passes_poll_interval_from_env(
    tmp_path,
    monkeypatch,
):
    """Test `_run_daemon` reads POLL_INTERVAL_SECONDS and forwards it to
    MonitorDaemon, matching the plain scheduled.py entrypoint's env var."""
    lock_path = tmp_path / 'monitor.lock'
    db_path = tmp_path / 'history.db'
    configured_poll_interval_sec = 5
    monkeypatch.setenv(
        'POLL_INTERVAL_SECONDS',
        str(configured_poll_interval_sec),
    )
    captured_kwargs = {}

    class ImmediatelyStoppingDaemon(MonitorDaemon):
        def __init__(self, *args, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(*args, **kwargs)

        async def run_forever(self):
            await self.stop()

    with (
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
        patch(
            'sftp_file_transfer.monitor.cli.resolve_history_db_path',
            return_value=db_path,
        ),
        patch(
            'sftp_file_transfer.monitor.cli.MonitorDaemon',
            ImmediatelyStoppingDaemon,
        ),
    ):
        result = runner.invoke(cli.app, ['_run_daemon'])

    assert result.exit_code == 0
    assert captured_kwargs['poll_interval_sec'] == configured_poll_interval_sec


def test_run_daemon_command_defaults_poll_interval_to_30(
    tmp_path,
    monkeypatch,
):
    """Test `_run_daemon` defaults to a 30s cycle when the env var is
    unset, matching scheduled.py's own default."""
    lock_path = tmp_path / 'monitor.lock'
    db_path = tmp_path / 'history.db'
    monkeypatch.delenv('POLL_INTERVAL_SECONDS', raising=False)
    default_poll_interval_sec = 30
    captured_kwargs = {}

    class ImmediatelyStoppingDaemon(MonitorDaemon):
        def __init__(self, *args, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(*args, **kwargs)

        async def run_forever(self):
            await self.stop()

    with (
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
        patch(
            'sftp_file_transfer.monitor.cli.resolve_history_db_path',
            return_value=db_path,
        ),
        patch(
            'sftp_file_transfer.monitor.cli.MonitorDaemon',
            ImmediatelyStoppingDaemon,
        ),
        # Isolate from the developer's real .env — EnvLoader() would
        # otherwise repopulate the just-deleted env var from disk.
        patch('sftp_file_transfer.monitor.cli.EnvLoader'),
    ):
        result = runner.invoke(cli.app, ['_run_daemon'])

    assert result.exit_code == 0
    assert captured_kwargs['poll_interval_sec'] == default_poll_interval_sec


def test_run_daemon_command_starts_server_and_writes_lock(tmp_path):
    """Test the hidden `_run_daemon` command starts the server, writes
    the lock file, and stops cleanly when the daemon is told to stop."""
    lock_path = tmp_path / 'monitor.lock'
    db_path = tmp_path / 'history.db'

    class ImmediatelyStoppingDaemon(MonitorDaemon):
        async def run_forever(self):
            await self.stop()

    with (
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
        patch(
            'sftp_file_transfer.monitor.cli.resolve_history_db_path',
            return_value=db_path,
        ),
        patch(
            'sftp_file_transfer.monitor.cli.MonitorDaemon',
            ImmediatelyStoppingDaemon,
        ),
    ):
        result = runner.invoke(cli.app, ['_run_daemon'])

    assert result.exit_code == 0
    assert not lock_path.exists()


def test_run_daemon_command_keeps_lock_file_until_run_forever_returns(
    tmp_path,
):
    """Test the lock file survives stop() being called and is only
    removed once run_forever() has actually finished — reproducing the
    reported bug where stop/Ctrl+Q made the lock file vanish (and the
    daemon become invisible to _running_lock_info) while the process
    was still alive finishing a cycle."""
    lock_path = tmp_path / 'monitor.lock'
    db_path = tmp_path / 'history.db'
    lock_file_existed_during_stop = False

    class SlowStoppingDaemon(MonitorDaemon):
        async def run_forever(self):
            nonlocal lock_file_existed_during_stop
            await self.stop()
            lock_file_existed_during_stop = lock_path.exists()

    with (
        patch('sftp_file_transfer.monitor.cli.DEFAULT_LOCK_PATH', lock_path),
        patch(
            'sftp_file_transfer.monitor.cli.resolve_history_db_path',
            return_value=db_path,
        ),
        patch(
            'sftp_file_transfer.monitor.cli.MonitorDaemon',
            SlowStoppingDaemon,
        ),
    ):
        result = runner.invoke(cli.app, ['_run_daemon'])

    assert result.exit_code == 0
    assert lock_file_existed_during_stop is True
    assert not lock_path.exists()


def test_main_callback_theme_option_saves_config(tmp_path):
    """Test --theme persists the chosen theme via MonitorConfig."""
    config_path = tmp_path / 'monitor_config.json'
    with (
        patch(
            'sftp_file_transfer.monitor.cli._running_lock_info',
            side_effect=[None, {'pid': 1, 'port': 4444}],
        ),
        patch('sftp_file_transfer.monitor.cli._spawn_detached_daemon'),
        patch('sftp_file_transfer.monitor.cli.DaemonClient'),
        patch('sftp_file_transfer.monitor.cli.MonitorApp'),
        patch(
            'sftp_file_transfer.monitor.cli.DEFAULT_CONFIG_PATH',
            config_path,
        ),
    ):
        result = runner.invoke(cli.app, ['--theme', 'monokai'])

    assert result.exit_code == 0
    assert '"theme": "monokai"' in config_path.read_text(encoding='utf-8')
