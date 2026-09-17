import asyncio
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer
from typer import Context, Option, Typer

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.history_tracker import (
    resolve_history_db_path,
)
from sftp_file_transfer.monitor.app import MonitorApp
from sftp_file_transfer.monitor.client import DaemonClient
from sftp_file_transfer.monitor.config import (
    DEFAULT_CONFIG_PATH,
    MonitorConfig,
)
from sftp_file_transfer.monitor.daemon import (
    DEFAULT_LOCK_PATH,
    MonitorDaemon,
    _is_pid_alive,
)
from sftp_file_transfer.monitor.state import DashboardState

app = Typer()

_RUN_DAEMON_SUBCOMMAND = '_run_daemon'
_SNAPSHOT_TIMEOUT_SEC = 2.0
_SPAWN_WAIT_TIMEOUT_SEC = 10.0
_STOP_WAIT_TIMEOUT_SEC = 5.0
_PORT_PROBE_TIMEOUT_SEC = 1.0


def _port_responds(
    port: int,
    timeout: float = _PORT_PROBE_TIMEOUT_SEC,
) -> bool:
    """Check whether something is actually listening on a local port.

    Pid liveness alone can't tell a real daemon from an unrelated
    process that happens to have been assigned the same pid number
    after an ungraceful reboot — a lock file surviving a crash can
    point at a stale pid that Windows has since recycled for something
    else entirely. Confirming the recorded port also responds is much
    stronger evidence, since the daemon's port is OS-assigned per run
    and collides with an unrelated process's port by coincidence only
    astronomically rarely.

    Args:
        port: TCP port to probe on 127.0.0.1.
        timeout: Maximum seconds to wait for the connection.

    Returns:
        bool: True if a connection could be opened, else False.
    """

    async def _do() -> None:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection('127.0.0.1', port),
            timeout=timeout,
        )
        writer.close()

    try:
        asyncio.run(_do())
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _running_lock_info(
    lock_path: Path = DEFAULT_LOCK_PATH,
) -> Optional[dict]:
    """Return the running daemon's lock info, cleaning up a stale lock.

    A lock is trusted only if both its recorded pid is alive AND its
    recorded port actually responds — pid liveness alone is not proof
    it's our daemon (see `_port_responds`).

    Args:
        lock_path: Path to the lock file.

    Returns:
        Optional[dict]: {'pid': int, 'port': int} if a live daemon owns
            the lock file, else None (and the stale file, if any, is
            removed).
    """
    lock_info = MonitorDaemon.read_lock_file(lock_path)
    if lock_info is None:
        return None
    if not _is_pid_alive(lock_info['pid']) or not _port_responds(
        lock_info['port'],
    ):
        Path(lock_path).unlink(missing_ok=True)
        return None
    return lock_info


def _daemon_spawn_command() -> List[str]:
    """Build the argv used to launch a detached daemon process.

    Returns:
        List[str]: The command to pass to subprocess.Popen.
    """
    if getattr(sys, 'frozen', False):
        return [sys.executable, _RUN_DAEMON_SUBCOMMAND]
    return [
        sys.executable,
        '-m',
        'sftp_file_transfer.monitor.cli',
        _RUN_DAEMON_SUBCOMMAND,
    ]


def _spawn_detached_daemon() -> None:
    """Spawn the daemon as a detached background process on Windows.

    Returns:
        None.
    """
    subprocess.Popen(
        _daemon_spawn_command(),
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _wait_for_daemon(
    lock_path: Path = DEFAULT_LOCK_PATH,
    timeout: float = _SPAWN_WAIT_TIMEOUT_SEC,
) -> Optional[dict]:
    """Poll for the freshly-spawned daemon's lock file to appear.

    Args:
        lock_path: Path to the lock file.
        timeout: Maximum seconds to wait.

    Returns:
        Optional[dict]: The lock info once available, or None if the
            daemon didn't start within the timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lock_info = _running_lock_info(lock_path)
        if lock_info is not None:
            return lock_info
        time.sleep(0.1)
    return None


def _send_stop_command(port: int) -> bool:
    """Send a stop command to the daemon over TCP.

    Args:
        port: TCP port the daemon's server is listening on.

    Returns:
        bool: True if the command was delivered, False if the daemon
            wasn't reachable.
    """

    async def _do() -> None:
        client = DaemonClient(port)
        await client.connect()
        await client.send_command({'cmd': 'stop'})
        await client.close()

    try:
        asyncio.run(_do())
        return True
    except OSError:
        return False


def _fetch_snapshot(port: int) -> Optional[DashboardState]:
    """Fetch a single state snapshot from a running daemon.

    Args:
        port: TCP port the daemon's server is listening on.

    Returns:
        Optional[DashboardState]: The snapshot, or None if the daemon
            wasn't reachable in time.
    """

    async def _do() -> DashboardState:
        client = DaemonClient(port)
        await client.connect()
        stream = client.state_stream()
        state = await asyncio.wait_for(
            anext(stream),
            timeout=_SNAPSHOT_TIMEOUT_SEC,
        )
        await client.close()
        return state

    try:
        return asyncio.run(_do())
    except (OSError, asyncio.TimeoutError):
        return None


def _terminate_pid(pid: int) -> None:
    """Forcibly terminate a process by pid, as a last resort.

    Args:
        pid: Process id to terminate.

    Returns:
        None.
    """
    process_terminate = 0x0001
    handle = ctypes.windll.kernel32.OpenProcess(process_terminate, False, pid)
    if handle:
        ctypes.windll.kernel32.TerminateProcess(handle, 1)
        ctypes.windll.kernel32.CloseHandle(handle)


def _attach(theme: Optional[str], lock_info: dict) -> None:
    """Attach a TUI to an already-running daemon.

    Loads the project .env so the History screen's pending-file scan
    can read LOCAL_PATH/FILE_EXTENSION — the TUI runs as its own
    process (possibly reattached long after the daemon started), so
    it can't rely on the daemon process having already loaded them.

    If the user didn't explicitly detach (Ctrl+Q's "stop and quit"
    rather than 'q'), verifies the daemon process actually exited
    afterward and force-terminates it if not — mirroring stop_command,
    since sending the daemon a `stop` command is not proof it has
    actually finished exiting (see MonitorDaemon.stop's docstring).

    Args:
        theme: Theme name to activate, or None for the persisted one.
        lock_info: The daemon's {'pid': int, 'port': int}.

    Returns:
        None.
    """
    EnvLoader()
    client = DaemonClient(lock_info['port'])
    monitor_app = MonitorApp(
        client=client,
        history_db_path=resolve_history_db_path(),
        theme_name=theme,
    )
    monitor_app.run()

    if not monitor_app.detached:
        if not _wait_for_exit(
            lock_info['pid'],
            timeout=_STOP_WAIT_TIMEOUT_SEC,
        ):
            _terminate_pid(lock_info['pid'])
        Path(DEFAULT_LOCK_PATH).unlink(missing_ok=True)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: Context,
    detach: bool = Option(
        False,
        '--detach',
        '-d',
        help='Start the daemon in the background without attaching a TUI.',
    ),
    theme: Optional[str] = Option(
        None,
        '--theme',
        help='Theme to use: nord, monokai, solarized-light, high-contrast.',
    ),
) -> None:
    """Start (or attach to) the monitor daemon and its TUI.

    Args:
        ctx: Typer context object.
        detach: If True, spawn the daemon only; don't attach a TUI.
        theme: Theme to activate/persist, or None to keep the current one.

    Returns:
        None.
    """
    if ctx.invoked_subcommand:
        return

    if theme:
        MonitorConfig(theme=theme).save(DEFAULT_CONFIG_PATH)

    lock_info = _running_lock_info(DEFAULT_LOCK_PATH)
    if lock_info is None:
        _spawn_detached_daemon()
        lock_info = _wait_for_daemon(DEFAULT_LOCK_PATH)
        if lock_info is None:
            typer.echo('Failed to start the monitor daemon.')
            raise typer.Exit(1)

    if detach:
        typer.echo(
            f"Monitor daemon started in the background "
            f"(pid={lock_info['pid']}).",
        )
        return

    _attach(theme, lock_info)


@app.command('attach')
def attach_command(
    theme: Optional[str] = Option(
        None,
        '--theme',
        help='Theme to use: nord, monokai, solarized-light, high-contrast.',
    ),
) -> None:
    """Attach a TUI to an already-running monitor daemon.

    Args:
        theme: Theme to activate/persist, or None to keep the current one.

    Returns:
        None.
    """
    lock_info = _running_lock_info(DEFAULT_LOCK_PATH)
    if lock_info is None:
        typer.echo(
            'No running monitor daemon found. Start one with `sftp_monitor`.',
        )
        raise typer.Exit(1)

    if theme:
        MonitorConfig(theme=theme).save(DEFAULT_CONFIG_PATH)

    _attach(theme, lock_info)


def _wait_for_exit(
    pid: int,
    timeout: float = _STOP_WAIT_TIMEOUT_SEC,
) -> bool:
    """Poll until a process exits, or a timeout is reached.

    Args:
        pid: Process id to watch.
        timeout: Maximum seconds to wait.

    Returns:
        bool: True once the process is no longer alive, False if it's
            still alive when the timeout elapses.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _is_pid_alive(pid)


@app.command('stop')
def stop_command() -> None:
    """Stop the running monitor daemon.

    Sends a graceful `stop` command over TCP, then verifies the
    process actually exited within a timeout — a daemon can receive
    and acknowledge the command yet still be mid-cycle (e.g. blocked
    on a slow SFTP call) when the CLI process returns, so delivery of
    the command alone is not proof the process is gone. If it's still
    alive once the timeout elapses, it's force-terminated by pid so a
    stuck daemon can never masquerade as stopped and get silently
    duplicated by a later `sftp_monitor` invocation.

    Returns:
        None.
    """
    lock_info = _running_lock_info(DEFAULT_LOCK_PATH)
    if lock_info is None:
        typer.echo('No running monitor daemon found.')
        raise typer.Exit(1)

    _send_stop_command(lock_info['port'])
    if not _wait_for_exit(lock_info['pid'], timeout=_STOP_WAIT_TIMEOUT_SEC):
        _terminate_pid(lock_info['pid'])
    Path(DEFAULT_LOCK_PATH).unlink(missing_ok=True)
    typer.echo('Monitor daemon stopped.')


@app.command('status')
def status_command() -> None:
    """Report whether a monitor daemon is running.

    Returns:
        None.
    """
    lock_info = _running_lock_info(DEFAULT_LOCK_PATH)
    if lock_info is None:
        typer.echo('No monitor daemon is running.')
        return

    state = _fetch_snapshot(lock_info['port'])
    if state is None:
        typer.echo(
            f"Monitor daemon (pid={lock_info['pid']}) is not responding.",
        )
        return

    typer.echo(
        f"Monitor daemon running "
        f"(pid={lock_info['pid']}, port={lock_info['port']})\n"
        f'  site: {state.site_name}\n'
        f'  cycle #: {state.cycle_num}\n'
        f'  last cycle: {state.last_cycle_status} @ {state.last_cycle_time}',
    )


@app.command(_RUN_DAEMON_SUBCOMMAND, hidden=True)
def run_daemon_command() -> None:
    """Run the daemon in the foreground of the current process.

    This is the internal entrypoint re-invoked by `_spawn_detached_daemon`
    in a detached child process; it is not meant to be run directly.

    Returns:
        None.
    """

    async def _serve() -> None:
        EnvLoader()
        daemon = MonitorDaemon(
            history_db_path=resolve_history_db_path(),
            lock_path=DEFAULT_LOCK_PATH,
            site_name=os.getenv('SITE_NAME', ''),
            poll_interval_sec=int(os.getenv('POLL_INTERVAL_SECONDS', '30')),
        )
        server = await daemon.start_server()
        port = server.sockets[0].getsockname()[1]
        daemon.write_lock_file(port)
        try:
            await daemon.run_forever()
        finally:
            server.close()
            await server.wait_closed()
            daemon.remove_lock_file()

    asyncio.run(_serve())


if __name__ == '__main__':
    app()
