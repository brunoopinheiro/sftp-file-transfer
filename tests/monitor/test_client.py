import asyncio

import pytest

from sftp_file_transfer.monitor.client import DaemonClient
from sftp_file_transfer.monitor.daemon import MonitorDaemon


def _make_daemon(tmp_path):
    return MonitorDaemon(
        history_db_path=tmp_path / 'history.db',
        lock_path=tmp_path / 'monitor.lock',
        poll_interval_sec=45,
        site_name='GRANDHOTEL-SP01',
    )


def test_client_receives_snapshot_then_subsequent_events(tmp_path):
    """Test DaemonClient.state_stream() yields the initial snapshot then
    later broadcast events, as DashboardState instances."""
    daemon = _make_daemon(tmp_path)

    async def scenario():
        server = await daemon.start_server()
        port = server.sockets[0].getsockname()[1]
        client = DaemonClient(port)
        try:
            await client.connect()
            stream = client.state_stream()

            first = await anext(stream)
            assert first.site_name == 'GRANDHOTEL-SP01'

            expected_cycle_num = 7
            daemon.state.cycle_num = expected_cycle_num
            daemon.broadcast_snapshot()

            second = await asyncio.wait_for(anext(stream), timeout=2)
            assert second.cycle_num == expected_cycle_num
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_state_stream_ends_when_the_daemon_closes_the_connection(tmp_path):
    """Test state_stream() stops iterating once the daemon disconnects."""
    daemon = _make_daemon(tmp_path)

    async def scenario():
        server = await daemon.start_server()
        port = server.sockets[0].getsockname()[1]
        client = DaemonClient(port)
        try:
            await client.connect()
            stream = client.state_stream()
            await anext(stream)

            for connected_client in list(daemon._clients):
                connected_client.close()
            daemon._clients.clear()
            server.close()
            await server.wait_closed()

            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(stream), timeout=2)
        finally:
            await client.close()

    asyncio.run(scenario())


def test_client_send_command_reaches_the_daemon(tmp_path):
    """Test DaemonClient.send_command() delivers a JSON command that the
    daemon acts on."""
    daemon = _make_daemon(tmp_path)
    daemon.state.countdown_sec = 30

    async def scenario():
        server = await daemon.start_server()
        port = server.sockets[0].getsockname()[1]
        client = DaemonClient(port)
        try:
            await client.connect()
            await client.send_command({'cmd': 'force_run'})
            await asyncio.sleep(0.1)
            assert daemon.state.countdown_sec == 0
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
