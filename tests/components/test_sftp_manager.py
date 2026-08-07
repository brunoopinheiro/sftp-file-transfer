from unittest.mock import patch

import pytest
from paramiko import SSHException

from sftp_file_transfer.components.sftp_manager import SFTPManager


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
    sftp_fixture, tmp_path,
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
