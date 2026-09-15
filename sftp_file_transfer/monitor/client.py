import asyncio
import json
from typing import AsyncIterator, Optional

from sftp_file_transfer.monitor.state import DashboardState


class DaemonClient:
    """TCP client for a running MonitorDaemon.

    Connects to the daemon's local server, exposes its stream of
    DashboardState snapshots/updates, and lets the caller send JSON
    commands (`force_run`, `stop`) back to the daemon.
    """

    def __init__(self, port: int, host: str = '127.0.0.1') -> None:
        """Initialize the client with the daemon's connection details.

        Args:
            port: TCP port the daemon's server is listening on.
            host: Host the daemon's server is bound to.

        Returns:
            None.
        """
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> None:
        """Open the TCP connection to the daemon.

        Returns:
            None.
        """
        self._reader, self._writer = await asyncio.open_connection(
            self.host,
            self.port,
        )

    async def state_stream(self) -> AsyncIterator[DashboardState]:
        """Yield each DashboardState received from the daemon.

        The first yielded value is the initial snapshot sent on
        connect; subsequent values are broadcast updates.

        Yields:
            DashboardState: Each state update received from the daemon.
        """
        while True:
            line = await self._reader.readline()
            if not line:
                return
            yield DashboardState.from_json(line.decode('utf-8'))

    async def send_command(self, command: dict) -> None:
        """Send a JSON command to the daemon.

        Args:
            command: Command dict, e.g. {'cmd': 'force_run'}.

        Returns:
            None.
        """
        payload = (json.dumps(command) + '\n').encode('utf-8')
        self._writer.write(payload)
        await self._writer.drain()

    async def close(self) -> None:
        """Close the TCP connection.

        Returns:
            None.
        """
        if self._writer is not None:
            self._writer.close()
