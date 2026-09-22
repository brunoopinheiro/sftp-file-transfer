import string
import uuid
from logging.handlers import RotatingFileHandler

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sftp_file_transfer.components.logger_setup import (
    BACKUP_COUNT,
    MAX_LOG_SIZE,
    setup_logger,
)

EXPECTED_HANDLER_COUNT = 2
ONE_MEGABYTE = 1 * 1024 * 1024
EXPECTED_RETENTION_BYTES = 60 * 1024 * 1024


def test_setup_logger_raises_value_error_on_invalid_level():
    """Test that setup_logger raises ValueError for invalid log level."""
    unique_name = str(uuid.uuid4())

    with pytest.raises(ValueError, match='Invalid log level'):
        setup_logger(log_name=unique_name, default_level=999)


def test_setup_logger_creates_log_directory_if_not_exists(tmp_path):
    """Test that setup_logger creates the log directory if missing."""
    unique_name = str(uuid.uuid4())
    nested_dir = tmp_path / 'nested' / 'logs'

    setup_logger(log_name=unique_name, log_dir=str(nested_dir))

    assert nested_dir.exists()


def test_setup_logger_first_call_adds_exactly_two_handlers(tmp_path):
    """Test that first call adds exactly 2 handlers (file + console)."""
    unique_name = str(uuid.uuid4())
    log_dir = tmp_path / 'logs'

    logger = setup_logger(log_name=unique_name, log_dir=str(log_dir))

    assert len(logger.handlers) == EXPECTED_HANDLER_COUNT
    handler_types = {type(h).__name__ for h in logger.handlers}
    assert 'RotatingFileHandler' in handler_types
    assert 'StreamHandler' in handler_types


def test_setup_logger_second_call_does_not_add_duplicate_handlers(tmp_path):
    """Test that second call with same log_name does not add more handlers."""
    unique_name = str(uuid.uuid4())
    log_dir = tmp_path / 'logs'

    logger_first = setup_logger(log_name=unique_name, log_dir=str(log_dir))
    handler_count_after_first = len(logger_first.handlers)

    logger_second = setup_logger(log_name=unique_name, log_dir=str(log_dir))
    handler_count_after_second = len(logger_second.handlers)

    assert (
        handler_count_after_first
        == handler_count_after_second
        == EXPECTED_HANDLER_COUNT
    )


def test_setup_logger_returns_same_logger_object_by_identity(tmp_path):
    """Test that same log_name returns the exact same logger object."""
    unique_name = str(uuid.uuid4())
    log_dir = tmp_path / 'logs'

    logger_first = setup_logger(log_name=unique_name, log_dir=str(log_dir))
    logger_second = setup_logger(log_name=unique_name, log_dir=str(log_dir))

    assert logger_first is logger_second


@given(
    num_calls=st.integers(min_value=1, max_value=5),
    random_name=st.text(
        alphabet=string.ascii_letters + string.digits + '_',
        min_size=1,
        max_size=20,
    ),
)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_setup_logger_idempotency_property(tmp_path, num_calls, random_name):
    """Test that repeated calls preserve handler count (idempotency)."""
    # Use uuid to ensure completely unique names per hypothesis example
    unique_name = f'{random_name}_{uuid.uuid4().hex[:8]}'
    log_dir = tmp_path / unique_name / 'logs'

    # First call
    logger = setup_logger(log_name=unique_name, log_dir=str(log_dir))
    handler_count_after_first = len(logger.handlers)

    # Subsequent calls
    for _ in range(num_calls - 1):
        logger = setup_logger(log_name=unique_name, log_dir=str(log_dir))
        handler_count_after_call = len(logger.handlers)
        assert handler_count_after_call == handler_count_after_first, (
            f'Handler count changed after call: '
            f'{handler_count_after_call} != {handler_count_after_first}'
        )


def _file_handler(logger):
    """Return the logger's RotatingFileHandler."""
    return next(
        h for h in logger.handlers if isinstance(h, RotatingFileHandler)
    )


def test_setup_logger_default_rotation_caps_files_at_one_megabyte(tmp_path):
    """Test that the default rotation threshold is 1 MB."""
    unique_name = str(uuid.uuid4())

    logger = setup_logger(log_name=unique_name, log_dir=str(tmp_path))

    assert MAX_LOG_SIZE == ONE_MEGABYTE
    assert _file_handler(logger).maxBytes == ONE_MEGABYTE


def test_setup_logger_default_keeps_enough_backups_for_same_retention(
    tmp_path,
):
    """Test that 1 MB x (1 active + BACKUP_COUNT) retains 60 MB total."""
    unique_name = str(uuid.uuid4())

    logger = setup_logger(log_name=unique_name, log_dir=str(tmp_path))

    assert _file_handler(logger).backupCount == BACKUP_COUNT
    assert MAX_LOG_SIZE * (1 + BACKUP_COUNT) == EXPECTED_RETENTION_BYTES


def test_setup_logger_rotates_and_never_exceeds_backup_count(tmp_path):
    """Test that writing past the threshold rotates and prunes backups."""
    unique_name = str(uuid.uuid4())
    max_bytes = 256
    backup_count = 3

    logger = setup_logger(
        log_name=unique_name,
        log_dir=str(tmp_path),
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
    for i in range(200):
        logger.info(f'rotation probe line {i} ' + 'x' * 40)

    log_files = sorted(tmp_path.glob(f'{unique_name}.log*'))

    assert len(log_files) == backup_count + 1
    assert (tmp_path / f'{unique_name}.log').exists()
    for backup in range(1, backup_count + 1):
        assert (tmp_path / f'{unique_name}.log.{backup}').exists()


@given(
    max_bytes=st.integers(min_value=128, max_value=4096),
    backup_count=st.integers(min_value=1, max_value=10),
)
@settings(
    max_examples=25,
    deadline=None,  # real rotation touches the filesystem on every example
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_setup_logger_retention_never_exceeds_configured_budget_property(
    tmp_path,
    max_bytes,
    backup_count,
):
    """Test that retained log files never exceed the configured budget."""
    unique_name = f'rotation_{uuid.uuid4().hex[:8]}'
    log_dir = tmp_path / unique_name

    logger = setup_logger(
        log_name=unique_name,
        log_dir=str(log_dir),
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
    for i in range(150):
        logger.info(f'property probe line {i} ' + 'y' * 60)

    log_files = list(log_dir.glob(f'{unique_name}.log*'))

    assert len(log_files) <= backup_count + 1
