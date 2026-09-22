import asyncio
import ctypes
import json
import os
from datetime import date, datetime, timedelta
from logging import Handler, Logger, LogRecord
from pathlib import Path
from typing import List, Optional, Set, Union

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.history_tracker import (
    HistoryTracker,
    resolve_history_db_path,
)
from sftp_file_transfer.components.logger_setup import setup_logger
from sftp_file_transfer.components.nfce_config import NfceConfig
from sftp_file_transfer.components.nfce_db_client import build_engine
from sftp_file_transfer.components.sftp_manager import (
    SFTPManager,
    SFTPManagerConfig,
)
from sftp_file_transfer.monitor.state import DashboardState, LogEntry
from sftp_file_transfer.scheduled import scheduled_task

logger: Logger = setup_logger()

DEFAULT_LOCK_PATH = Path('data') / 'monitor.lock'
MAX_LOG_LINES = 200
DAILY_COUNTS_WINDOW_DAYS = 7
# A snapshot is the complete state, so a later one fully supersedes an
# earlier one. If a client stops reading (minimised terminal, suspended
# RDP session) we drop snapshots for it rather than letting its write
# buffer grow without bound.
MAX_CLIENT_BUFFER_BYTES = 1 * 1024 * 1024


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Return the event loop running on this thread, if there is one.

    Returns:
        Optional[asyncio.AbstractEventLoop]: The running loop, or None
            when called from a worker thread.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given pid is currently running.

    Uses the Windows `OpenProcess` API, consistent with this project's
    Windows-only deployment target.

    Args:
        pid: Process id to check.

    Returns:
        bool: True if a process with that pid exists, else False.
    """
    query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        query_limited_information,
        False,
        pid,
    )
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


class _DashboardLogHandler(Handler):
    """Mirrors every record on the shared logger into the dashboard.

    This keeps the TUI's LIVE LOG panel showing exactly the same lines
    as `logs/sftp_file_transfer.log`, rather than a separate hand-written
    narration stream.
    """

    def __init__(self, daemon: 'MonitorDaemon') -> None:
        """Store the daemon whose dashboard state should receive records.

        Args:
            daemon: The daemon instance to forward log records to.

        Returns:
            None.
        """
        super().__init__()
        self._daemon = daemon

    def emit(self, record: LogRecord) -> None:
        """Forward one log record to the daemon's dashboard ring buffer.

        Args:
            record: The log record emitted by the shared logger.

        Returns:
            None.
        """
        ts = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
        self._daemon._log(ts, record.levelname, record.getMessage())


class MonitorDaemon:
    """Owns the scheduled send/generate loop and broadcasts live state.

    Reuses `scheduled_task` from `sftp_file_transfer.scheduled` for the
    actual generate/send cycle, and serves the resulting dashboard
    state to any number of connected TUI clients over a local TCP
    socket, so the TUI can attach/detach without stopping the loop.
    """

    def __init__(
        self,
        history_db_path: Optional[Union[str, Path]] = None,
        lock_path: Union[str, Path] = DEFAULT_LOCK_PATH,
        poll_interval_sec: int = 30,
        site_name: str = '',
    ) -> None:
        """Initialize the daemon's state and configuration.

        Args:
            history_db_path: Path to the send-history ledger database,
                or None to resolve HISTORY_DB_PATH from the .env file
                (see resolve_history_db_path()) — the same value every
                other entry point (scheduled.py, history_cli.py) uses.
            lock_path: Path to the lock file recording pid/port.
            poll_interval_sec: Seconds between scheduled cycles.
            site_name: Display name of the hotel site being monitored.

        Returns:
            None.
        """
        self.history_db_path = (
            Path(history_db_path)
            if history_db_path is not None
            else resolve_history_db_path()
        )
        self.lock_path = Path(lock_path)
        self.poll_interval_sec = poll_interval_sec
        self.state = DashboardState(
            site_name=site_name,
            countdown_sec=poll_interval_sec,
        )
        self.stopped = False
        self._started_at = datetime.now()
        self._clients: Set[asyncio.StreamWriter] = set()
        self._server: Optional[asyncio.base_events.Server] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        for handler in list(logger.handlers):
            if isinstance(handler, _DashboardLogHandler):
                logger.removeHandler(handler)
        logger.addHandler(_DashboardLogHandler(self))

    def run_cycle(self) -> None:
        """Run one generate/send cycle and refresh dashboard state.

        Probes DB/SFTP connectivity, calls the existing `scheduled_task()`
        (unchanged production logic), then refreshes sent/failed/pending
        counts and the 7-day chart from the send-history ledger, derives
        a real last-cycle status from the probes and ledger delta, and
        narrates the cycle in the log buffer.

        Returns:
            None.
        """
        self.state.cycle_num += 1
        now = datetime.now().strftime('%H:%M:%S')
        logger.info(f'Cycle #{self.state.cycle_num} started')

        self._check_connections()

        with HistoryTracker(self.history_db_path) as tracker:
            pending_before = tracker.get_summary()['total_pending']

        scheduled_task()

        with HistoryTracker(self.history_db_path) as tracker:
            summary = tracker.get_summary()
        self.state.sent_count = summary['total_sent']
        self.state.failed_count = summary['total_pending']
        self.state.pending_count = 0
        self.state.daily_counts = self._compute_daily_counts()

        self.state.last_cycle_status = self._derive_cycle_status(
            new_failures=summary['total_pending'] > pending_before,
        )
        self.state.last_cycle_time = now
        self.state.countdown_sec = self.poll_interval_sec

        logger.info(
            f'Cycle #{self.state.cycle_num} completed: '
            f'{self.state.sent_count} sent, '
            f'{self.state.failed_count} failed',
        )

    def _derive_cycle_status(self, new_failures: bool) -> str:
        """Classify the cycle just run as SUCCESS, PARTIAL, or FAILED.

        A down connection (SFTP unreachable, or DB unreachable despite
        being configured) always means FAILED. A DB that simply isn't
        configured for this site (`db_connected is None`) is not an
        error. Otherwise, any new ledger failures mean PARTIAL.

        Args:
            new_failures: Whether the pending/failed count in the
                ledger grew during this cycle.

        Returns:
            str: 'SUCCESS', 'PARTIAL', or 'FAILED'.
        """
        db_down = self.state.db_connected is False
        if not self.state.sftp_connected or db_down:
            return 'FAILED'
        if new_failures:
            return 'PARTIAL'
        return 'SUCCESS'

    def _check_connections(self) -> None:
        """Probe SFTP and NFCe-DB connectivity and update the state.

        Returns:
            None.
        """
        self.state.sftp_connected = self._probe_sftp()
        self.state.db_connected = self._probe_db()

    @staticmethod
    def _probe_sftp() -> bool:
        """Attempt a real SFTP connection using the configured env vars.

        Returns:
            bool: True if the connection succeeded, else False.
        """
        try:
            env = EnvLoader()
            config: SFTPManagerConfig = {
                'sftp_host': env.SFTP_HOST,
                'sftp_port': int(env.SFTP_PORT),
                'sftp_user': env.SFTP_USER,
                'sftp_password': env.SFTP_PASSWORD,
                'key_filepath': None,
                'key_password': None,
            }
            with SFTPManager(config):
                return True
        except Exception as e:
            logger.error(f'SFTP connectivity probe failed: {e}')
            return False

    @staticmethod
    def _probe_db() -> Optional[bool]:
        """Attempt a real connection to the NFCe source database.

        Returns:
            Optional[bool]: None if this site isn't configured for
                DB-based generation, else True/False for whether the
                configured database is currently reachable.
        """
        try:
            nfce_config = NfceConfig()
        except ValueError:
            return None
        engine = None
        try:
            engine = build_engine(
                host=nfce_config.nfce_db_host,
                port=int(nfce_config.nfce_db_port),
                database=nfce_config.nfce_db_name,
                user=nfce_config.nfce_db_user,
                password=nfce_config.nfce_db_password,
            )
            with engine.connect():
                return True
        except Exception as e:
            logger.error(f'NFCe DB connectivity probe failed: {e}')
            return False
        finally:
            if engine is not None:
                engine.dispose()

    def _compute_daily_counts(self) -> List[int]:
        """Count successfully-sent files per day for the trailing window.

        Returns:
            List[int]: Sent-file counts for each of the last
                `DAILY_COUNTS_WINDOW_DAYS` days, oldest first.
        """
        today = date.today()
        days = [
            today - timedelta(days=offset)
            for offset in range(DAILY_COUNTS_WINDOW_DAYS - 1, -1, -1)
        ]
        with HistoryTracker(self.history_db_path) as tracker:
            sent_rows = tracker.list_records(sent=True)

        counts_by_date = {}
        for row in sent_rows:
            counts_by_date[row.file_date] = (
                counts_by_date.get(row.file_date, 0) + 1
            )
        return [counts_by_date.get(day.isoformat(), 0) for day in days]

    def _log(self, ts: str, level: str, msg: str) -> None:
        """Append a log line to the ring buffer and broadcast it.

        Args:
            ts: Display timestamp.
            level: Log level name.
            msg: Log message text.

        Returns:
            None.
        """
        self.state.append_log_line(
            LogEntry(ts=ts, level=level, msg=msg),
            max_lines=MAX_LOG_LINES,
        )
        self.broadcast_snapshot()

    def force_run(self) -> None:
        """Zero the countdown so the next tick runs a cycle immediately.

        Returns:
            None.
        """
        self.state.countdown_sec = 0

    def write_lock_file(self, port: int) -> None:
        """Write the lock file recording this process's pid and port.

        Args:
            port: TCP port the daemon's server is listening on.

        Returns:
            None.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
            json.dumps({'pid': os.getpid(), 'port': port}),
            encoding='utf-8',
        )

    def remove_lock_file(self) -> None:
        """Delete the lock file, if it exists.

        Returns:
            None.
        """
        self.lock_path.unlink(missing_ok=True)

    @staticmethod
    def read_lock_file(lock_path: Union[str, Path]) -> Optional[dict]:
        """Read a lock file's pid/port, or None if it doesn't exist.

        Args:
            lock_path: Path to the lock file.

        Returns:
            Optional[dict]: {'pid': int, 'port': int}, or None if the
                file doesn't exist or can't be parsed.
        """
        path = Path(lock_path)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            return None

    def broadcast_snapshot(self) -> None:
        """Send the current full state snapshot to every connected client.

        `run_cycle()` runs on a worker thread and every log record it
        emits reaches this method via `_DashboardLogHandler`, but
        `StreamWriter.write()` may only be called on the event loop's
        own thread. When called from anywhere else, the write is handed
        back to the loop instead of mutating the transport's buffer
        from under it.

        Silently drops clients whose connection has broken.

        Returns:
            None.
        """
        loop = self._loop
        if loop is not None and loop is not _running_loop():
            try:
                loop.call_soon_threadsafe(self._write_snapshot)
            except RuntimeError:
                pass
            return
        self._write_snapshot()

    def _write_snapshot(self) -> None:
        """Write the current snapshot to every client, dropping dead ones.

        Must be called on the event loop's thread.

        Returns:
            None.
        """
        payload = (self.state.to_json() + '\n').encode('utf-8')
        for client in list(self._clients):
            try:
                if self._is_backlogged(client):
                    continue
                client.write(payload)
            except (ConnectionError, OSError):
                self._clients.discard(client)

    @staticmethod
    def _is_backlogged(client: asyncio.StreamWriter) -> bool:
        """Report whether a client is too far behind to send more to it.

        Args:
            client: The stream writer for a connected client.

        Returns:
            bool: True if the client's unsent backlog is over the cap.
        """
        transport = client.transport
        if transport is None:
            return False
        return transport.get_write_buffer_size() > MAX_CLIENT_BUFFER_BYTES

    async def _handle_command(self, command: dict) -> None:
        """Dispatch a single command received from a client.

        Args:
            command: Parsed JSON command, e.g. {'cmd': 'force_run'}.

        Returns:
            None.
        """
        cmd = command.get('cmd')
        if cmd == 'force_run':
            self.force_run()
        elif cmd == 'stop':
            await self.stop()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Serve one connected client: snapshot, then commands.

        Args:
            reader: Stream reader for the client connection.
            writer: Stream writer for the client connection.

        Returns:
            None.
        """
        self._clients.add(writer)
        writer.write((self.state.to_json() + '\n').encode('utf-8'))
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    command = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._handle_command(command)
        finally:
            self._clients.discard(writer)
            writer.close()

    async def start_server(self) -> asyncio.base_events.Server:
        """Start the local TCP server on an OS-assigned free port.

        Returns:
            asyncio.base_events.Server: The running server, already
                listening (its bound port is available via
                `server.sockets[0].getsockname()[1]`).
        """
        self._loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(
            self._handle_client,
            host='127.0.0.1',
            port=0,
        )
        return self._server

    async def run_forever(self) -> None:
        """Drive the countdown/cycle loop until stop() is called.

        Runs a cycle whenever the countdown reaches zero, otherwise
        ticks it down by one second and broadcasts the updated state.
        `run_cycle()` performs real network/DB I/O, so it's offloaded
        to a worker thread via `asyncio.to_thread` — otherwise the
        event loop (and this daemon's TCP server) would be unresponsive
        for the whole duration of every cycle.

        Returns:
            None.
        """
        while not self.stopped:
            self.state.uptime_sec = int(
                (datetime.now() - self._started_at).total_seconds(),
            )
            if self.state.countdown_sec <= 0:
                await asyncio.to_thread(self.run_cycle)
            else:
                self.state.countdown_sec -= 1
                self.broadcast_snapshot()
            if self.stopped:
                break
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Signal run_forever() to exit at its next opportunity.

        Deliberately does *not* close the server or remove the lock
        file here: `run_forever()` offloads each cycle to a worker
        thread, so a cycle already in flight (e.g. blocked on a slow
        SFTP connect) can keep the process alive for a while after
        `stopped` is set. Tearing down the server/lock file immediately
        would make the process invisible to `_running_lock_info()` —
        no lock file means no pid to check or force-terminate — even
        though it's still very much alive. The caller (`run_daemon_command`
        in cli.py) tears both down in its `finally` block, once
        `run_forever()` has actually returned.

        Returns:
            None.
        """
        self.stopped = True
