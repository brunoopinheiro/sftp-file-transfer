from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st
from paramiko import SSHException

from sftp_file_transfer.components.sftp_manager import (
    CHANNEL_TIMEOUT_SECONDS,
    CONNECT_TIMEOUT_SECONDS,
    KEEPALIVE_INTERVAL_SECONDS,
    SFTPManager,
)


def test_sftp_connection(sftp_fixture):
    """Test establishing an SFTP connection."""
    host = sftp_fixture.host
    port = sftp_fixture.port
    username = 'user'
    password = 'pw'

    with SFTPManager({
        'sftp_host': host,
        'sftp_port': port,
        'sftp_user': username,
        'sftp_password': password,
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        assert sftp_manager._transport is not None
        assert sftp_manager._sftp is not None


def test_upload_file_retries_on_transient_exception(sftp_fixture, tmp_path):
    """Test that upload_file retries after a transient SSHException."""
    local_file = tmp_path / 'upload.txt'
    local_file.write_text('content')
    remote_path = '/upload/test_file.txt'

    host = sftp_fixture.host
    port = sftp_fixture.port

    with SFTPManager({
        'sftp_host': host,
        'sftp_port': port,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        call_count = 0
        expected_calls = 2

        def flaky_put(localpath, remotepath):
            nonlocal call_count
            call_count += 1
            if call_count < expected_calls:
                raise SSHException('connection dropped')
            return 'ok'

        with patch.object(sftp_manager._sftp, 'put', side_effect=flaky_put):
            result = sftp_manager.upload_file(
                local_path=local_file,
                remote_path=remote_path,
            )

        assert result == 'ok'
        assert call_count == expected_calls


def test_upload_file_does_not_retry_on_missing_local_file(
    sftp_fixture,
    tmp_path,
):
    """Test that upload_file does not retry when the local file is missing."""
    missing_file = tmp_path / 'missing.txt'
    remote_path = '/upload/test_file.txt'

    host = sftp_fixture.host
    port = sftp_fixture.port

    with SFTPManager({
        'sftp_host': host,
        'sftp_port': port,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        with patch.object(sftp_manager._sftp, 'put') as mock_put:
            with pytest.raises(FileNotFoundError):
                sftp_manager.upload_file(
                    local_path=missing_file,
                    remote_path=remote_path,
                )
            mock_put.assert_not_called()


@pytest.mark.skip('Pytest-SFTPServer not working as expected')
def test_sftp_upload(sftp_fixture, tmp_path):
    """Test fetching files and directories from SFTP server."""
    local_file = tmp_path / 'upload.txt'
    file_content = 'pytest-sftpserver test file content.'
    local_file.write_text(file_content)
    remote_path = '/upload/test_file.txt'

    host = sftp_fixture.host
    port = sftp_fixture.port
    username = 'user'
    password = 'pw'

    with SFTPManager({
        'sftp_host': host,
        'sftp_port': port,
        'sftp_user': username,
        'sftp_password': password,
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        result = sftp_manager.upload_file(
            local_path=local_file,
            remote_path=remote_path,
        )
        assert result is not None


@pytest.mark.skip('Pytest-SFTPServer not working as expected')
def test_sftp_fetch(sftp_fixture, tmp_path):
    """Test uploading files to SFTP server."""
    local_file = tmp_path / 'download_file.txt'
    remote_path = '/a_dir/download_file.txt'

    host = sftp_fixture.host
    port = sftp_fixture.port
    username = 'user'
    password = 'pw'

    with sftp_fixture.serve_content({
        'a_dir/download_file.txt': 'This is a test file for pytest-sftpserver.'
    }):
        with SFTPManager({
            'sftp_host': host,
            'sftp_port': port,
            'sftp_user': username,
            'sftp_password': password,
            'key_filepath': None,
            'key_password': None,
        }) as sftp_manager:
            sftp_manager.download_file(
                local_path=local_file,
                remote_path=remote_path,
            )

            assert local_file.is_file()
            assert (
                local_file.read_text()
                == 'This is a test file for pytest-sftpserver.'
            )  # noqa


def test_download_file_calls_sftp_get(sftp_fixture, tmp_path):
    """Test that download_file calls sftp.get with correct arguments."""
    host = sftp_fixture.host
    port = sftp_fixture.port
    remote_path = '/remote/file.txt'
    local_path = tmp_path / 'downloaded.txt'

    with SFTPManager({
        'sftp_host': host,
        'sftp_port': port,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        with patch.object(sftp_manager._sftp, 'get') as mock_get:
            sftp_manager.download_file(
                remote_path=remote_path,
                local_path=local_path,
            )
            mock_get.assert_called_once_with(remote_path, local_path)


def test_make_directory_calls_sftp_mkdir(sftp_fixture):
    """Test that make_directory calls sftp.mkdir with correct arguments."""
    host = sftp_fixture.host
    port = sftp_fixture.port
    remote_path = '/new_directory'

    with SFTPManager({
        'sftp_host': host,
        'sftp_port': port,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        with patch.object(sftp_manager._sftp, 'mkdir') as mock_mkdir:
            sftp_manager.make_directory(remote_path)
            mock_mkdir.assert_called_once_with(remote_path)


def test_remove_directory_calls_sftp_rmdir(sftp_fixture):
    """Test that remove_directory calls sftp.rmdir with correct arguments."""
    host = sftp_fixture.host
    port = sftp_fixture.port
    remote_path = '/directory_to_remove'

    with SFTPManager({
        'sftp_host': host,
        'sftp_port': port,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        with patch.object(sftp_manager._sftp, 'rmdir') as mock_rmdir:
            sftp_manager.remove_directory(remote_path)
            mock_rmdir.assert_called_once_with(remote_path)


def test_list_files_calls_sftp_listdir(sftp_fixture):
    """Test that list_files calls sftp.listdir and wraps results in Path."""
    host = sftp_fixture.host
    port = sftp_fixture.port
    remote_path = '/some_dir'

    with SFTPManager({
        'sftp_host': host,
        'sftp_port': port,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        with patch.object(
            sftp_manager._sftp,
            'listdir',
            return_value=['a.txt', 'b.txt'],
        ) as mock_listdir:
            result = sftp_manager.list_files(remote_path)

            mock_listdir.assert_called_once_with(remote_path)
            assert result == [Path('a.txt'), Path('b.txt')]


def test_download_file_raises_when_not_connected(tmp_path):
    """Test download_file raises RuntimeError when SFTP not connected."""
    config = {
        'sftp_host': 'localhost',
        'sftp_port': 22,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }
    sftp_manager = SFTPManager(config)
    # Don't enter context, so _sftp remains None

    with pytest.raises(RuntimeError, match='SFTP client is not connected'):
        sftp_manager.download_file(
            remote_path='/some/file.txt',
            local_path=tmp_path / 'file.txt',
        )


def test_upload_file_raises_when_not_connected_but_local_file_exists(
    tmp_path,
):
    """Test upload_file raises RuntimeError when not connected, file exists."""
    local_file = tmp_path / 'existing_file.txt'
    local_file.write_text('content')

    config = {
        'sftp_host': 'localhost',
        'sftp_port': 22,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }
    sftp_manager = SFTPManager(config)
    # Don't enter context, so _sftp remains None

    with pytest.raises(RuntimeError, match='SFTP client is not connected'):
        sftp_manager.upload_file(
            local_path=local_file,
            remote_path='/remote/file.txt',
        )


def test_list_files_raises_when_not_connected():
    """Test that list_files raises RuntimeError when SFTP is not connected."""
    config = {
        'sftp_host': 'localhost',
        'sftp_port': 22,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }
    sftp_manager = SFTPManager(config)

    with pytest.raises(RuntimeError, match='SFTP client is not connected'):
        sftp_manager.list_files('/remote/directory')


def test_make_directory_raises_when_not_connected():
    """Test make_directory raises RuntimeError when SFTP not connected."""
    config = {
        'sftp_host': 'localhost',
        'sftp_port': 22,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }
    sftp_manager = SFTPManager(config)

    with pytest.raises(RuntimeError, match='SFTP client is not connected'):
        sftp_manager.make_directory('/remote/directory')


def test_remove_directory_raises_when_not_connected():
    """Test remove_directory raises RuntimeError when SFTP not connected."""
    config = {
        'sftp_host': 'localhost',
        'sftp_port': 22,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }
    sftp_manager = SFTPManager(config)

    with pytest.raises(RuntimeError, match='SFTP client is not connected'):
        sftp_manager.remove_directory('/remote/directory')


def test_connect_uses_key_based_auth_when_key_filepath_set(tmp_path):
    """Test that _connect uses key-based auth when key_filepath is provided."""
    key_filepath = tmp_path / 'fake_key'
    key_password = 'key_pass'

    config = {
        'sftp_host': 'localhost',
        'sftp_port': 22,
        'sftp_user': 'testuser',
        'sftp_password': 'pw',
        'key_filepath': key_filepath,
        'key_password': key_password,
    }

    mock_key = MagicMock()
    mock_transport = MagicMock()
    mock_sftp = MagicMock()

    mock_socket = MagicMock()

    with (
        patch(
            'sftp_file_transfer.components.sftp_manager.RSAKey.from_private_key_file',
            return_value=mock_key,
        ) as mock_from_key,
        patch(
            'sftp_file_transfer.components.sftp_manager.socket.create_connection',
            return_value=mock_socket,
        ),
        patch(
            'sftp_file_transfer.components.sftp_manager.Transport',
            return_value=mock_transport,
        ) as mock_transport_class,
        patch(
            'sftp_file_transfer.components.sftp_manager.SFTPClient.from_transport',
            return_value=mock_sftp,
        ) as mock_from_transport,
    ):
        sftp_manager = SFTPManager(config)
        sftp_manager._connect()

        mock_from_key.assert_called_once_with(
            key_filepath, password=key_password
        )
        mock_transport_class.assert_called_once_with(mock_socket)
        mock_transport.connect.assert_called_once_with(
            username='testuser',
            pkey=mock_key,
        )
        mock_from_transport.assert_called_once_with(mock_transport)
        assert sftp_manager._sftp == mock_sftp
        assert sftp_manager._transport == mock_transport


@given(
    sftp_host=st.one_of(st.text(), st.integers(), st.none(), st.booleans()),
    sftp_port=st.one_of(st.integers(), st.text(), st.none()),
    sftp_user=st.one_of(st.text(), st.integers(), st.none(), st.booleans()),
    sftp_password=st.one_of(
        st.text(), st.integers(), st.none(), st.booleans()
    ),
)
def test_check_args_matches_isinstance_invariant(
    sftp_host,
    sftp_port,
    sftp_user,
    sftp_password,
):
    """Test that check_args validates types correctly using isinstance."""
    should_be_valid = (
        isinstance(sftp_host, str)
        and isinstance(sftp_port, int)
        and isinstance(sftp_user, str)
        and isinstance(sftp_password, str)
    )

    if should_be_valid:
        # Should not raise
        SFTPManager.check_args(sftp_host, sftp_port, sftp_user, sftp_password)
    else:
        # Should raise ValueError
        with pytest.raises(
            ValueError, match='All SFTP connection parameters must be provided'
        ):
            SFTPManager.check_args(
                sftp_host, sftp_port, sftp_user, sftp_password
            )


def _stall_guard_config() -> dict:
    """Build a minimal password-auth config for stall-guard tests."""
    return {
        'sftp_host': 'localhost',
        'sftp_port': 22,
        'sftp_user': 'testuser',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }


def test_connect_bounds_the_tcp_connect_attempt():
    """Test that the TCP connect is made with an explicit timeout."""
    with (
        patch(
            'sftp_file_transfer.components.sftp_manager.socket.create_connection',
            return_value=MagicMock(),
        ) as mock_create_connection,
        patch('sftp_file_transfer.components.sftp_manager.Transport'),
        patch(
            'sftp_file_transfer.components.sftp_manager.SFTPClient.from_transport',
            return_value=MagicMock(),
        ),
    ):
        SFTPManager(_stall_guard_config())._connect()

        mock_create_connection.assert_called_once_with(
            ('localhost', 22),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )


def test_connect_enables_keepalive_so_a_dead_peer_is_detected():
    """Test that keepalives are turned on for the transport."""
    mock_transport = MagicMock()

    with (
        patch(
            'sftp_file_transfer.components.sftp_manager.socket.create_connection',
            return_value=MagicMock(),
        ),
        patch(
            'sftp_file_transfer.components.sftp_manager.Transport',
            return_value=mock_transport,
        ),
        patch(
            'sftp_file_transfer.components.sftp_manager.SFTPClient.from_transport',
            return_value=MagicMock(),
        ),
    ):
        SFTPManager(_stall_guard_config())._connect()

        mock_transport.set_keepalive.assert_called_once_with(
            KEEPALIVE_INTERVAL_SECONDS,
        )


def test_connect_bounds_how_long_a_request_may_stall():
    """Test that the SFTP channel is given an explicit timeout."""
    mock_sftp = MagicMock()

    with (
        patch(
            'sftp_file_transfer.components.sftp_manager.socket.create_connection',
            return_value=MagicMock(),
        ),
        patch('sftp_file_transfer.components.sftp_manager.Transport'),
        patch(
            'sftp_file_transfer.components.sftp_manager.SFTPClient.from_transport',
            return_value=mock_sftp,
        ),
    ):
        SFTPManager(_stall_guard_config())._connect()

        mock_sftp.get_channel.return_value.settimeout.assert_called_once_with(
            CHANNEL_TIMEOUT_SECONDS,
        )


def test_connect_raises_sshexception_when_the_socket_cannot_connect():
    """Test that a refused connection still surfaces as an SSHException."""
    with patch(
        'sftp_file_transfer.components.sftp_manager.socket.create_connection',
        side_effect=OSError('refused'),
    ):
        with pytest.raises(SSHException, match='Unable to connect to'):
            SFTPManager(_stall_guard_config())._connect()


def test_live_connection_has_keepalive_and_channel_timeout(sftp_fixture):
    """Test that a real connection comes back with both stall guards set."""
    with SFTPManager({
        'sftp_host': sftp_fixture.host,
        'sftp_port': sftp_fixture.port,
        'sftp_user': 'user',
        'sftp_password': 'pw',
        'key_filepath': None,
        'key_password': None,
    }) as sftp_manager:
        channel = sftp_manager._sftp.get_channel()

        assert channel.gettimeout() == CHANNEL_TIMEOUT_SECONDS
        assert (
            sftp_manager._transport.packetizer._Packetizer__keepalive_interval
            == KEEPALIVE_INTERVAL_SECONDS
        )
