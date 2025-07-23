import pytest


# https://github.com/ulope/pytest-sftpserver/issues/30#issuecomment-1530896213
@pytest.fixture
def sftp_fixture(sftpserver):
    """Prevents pytest-sftpserver from blocking the main thread."""
    sftpserver.daemon_threads = True
    sftpserver.block_on_close = False
    yield sftpserver  # noqa
