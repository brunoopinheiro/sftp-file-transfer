import os

import pytest

# Keep rich's output plain no matter what terminal the suite runs in.
#
# rich decides whether to emit ANSI from TTY_COMPATIBLE/FORCE_COLOR
# before it ever consults isatty(), so on a machine with either set,
# assertions on CLI output break: the ReprHighlighter wraps interpolated
# values in their own style spans, and an asserted substring such as
# 'Reset 1 record' stops being contiguous.
#
# TTY_COMPATIBLE is what rich checks first and it short-circuits, so it
# covers FORCE_COLOR and a genuinely attached terminal alike. Clearing
# FORCE_COLOR instead would also strip colour from pytest's own output
# for anyone who sets it deliberately.
#
# This has to run at import rather than in a fixture. Console.__init__
# resolves the colour system once, via _detect_color_system(), and
# _render_buffer styles output from that cached value -- so by the time
# any fixture runs, the module-level Console in history_cli has already
# decided. pytest imports this conftest before the test modules that
# import it, which is early enough.
os.environ['TTY_COMPATIBLE'] = '0'


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
