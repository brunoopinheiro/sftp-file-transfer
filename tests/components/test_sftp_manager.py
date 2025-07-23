import pytest

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
