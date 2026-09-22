import re
from logging import INFO
from pathlib import Path

from sftp_file_transfer import __version__
from sftp_file_transfer.components.logger_setup import (
    log_startup_banner,
    setup_logger,
)

_SEMVER = re.compile(r'^\d+\.\d+\.\d+$')
_PYPROJECT = Path(__file__).resolve().parent.parent / 'pyproject.toml'
# tomllib is 3.11+, and this project supports 3.10, so read the single
# line rather than taking a dependency just for one assertion.
_PYPROJECT_VERSION = re.compile(
    r'^\s*version\s*=\s*[\'"](?P<version>[^\'"]+)[\'"]',
    re.MULTILINE,
)


def _pyproject_version() -> str:
    """Read the project version declared in pyproject.toml."""
    match = _PYPROJECT_VERSION.search(
        _PYPROJECT.read_text(encoding='utf-8'),
    )
    assert match is not None, 'no version found in pyproject.toml'
    return match.group('version')


def test_package_version_matches_pyproject():
    """Test that __version__ and pyproject.toml never drift apart.

    __version__ is a literal because a PyInstaller one-file exe has no
    .dist-info for importlib.metadata to read. That means nothing keeps
    it in step with pyproject.toml except this test.
    """
    assert __version__ == _pyproject_version()


def test_package_version_is_semantic():
    """Test that the version is a plain MAJOR.MINOR.PATCH string."""
    assert _SEMVER.match(__version__)


def test_startup_banner_records_the_running_version(caplog):
    """Test that a run's log identifies the build that produced it.

    Without this line a site's log cannot be traced back to a build,
    which is exactly the gap felt when diagnosing an incident from
    logs alone.
    """
    logger = setup_logger()

    with caplog.at_level(INFO, logger='sftp_file_transfer'):
        log_startup_banner(logger, 'sftp-file-transfer-monitor')

    messages = [record.getMessage() for record in caplog.records]

    assert any(
        'sftp-file-transfer-monitor' in message and __version__ in message
        for message in messages
    )
