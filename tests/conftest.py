import pytest


# https://github.com/ulope/pytest-sftpserver/issues/30#issuecomment-1530896213
@pytest.fixture
def sftp_fixture(sftpserver):
    """Prevents pytest-sftpserver from blocking the main thread."""
    sftpserver.daemon_threads = True
    sftpserver.block_on_close = False
    yield sftpserver  # noqa


@pytest.fixture(autouse=True)
def isolated_host_pins(tmp_path, monkeypatch):
    """Give every test its own SFTP host key pin file.

    Without this, connecting in a test would trust-on-first-use into the
    real data/known_hosts.json in the repo, and tests would leak pins
    into one another.
    """
    monkeypatch.setenv(
        'SFTP_HOST_PINS_PATH',
        str(tmp_path / 'known_hosts.json'),
    )
