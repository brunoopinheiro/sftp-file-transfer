from logging import (
    CRITICAL,
    DEBUG,
    ERROR,
    INFO,
    WARNING,
    Formatter,
    Logger,
    StreamHandler,
    getLogger,
)
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB


def setup_logger(
    log_name: str = 'sftp_file_transfer',
    log_dir: str = 'logs',
    max_bytes: int = MAX_LOG_SIZE,
    backup_count: int = 5,
    default_level: int = INFO,
) -> Logger:
    """Set up a rotating file logger.

    Args:
        log_name (str, optional): The name of the log file (without extension).
            Defaults to 'sftp_file_transfer'.
        log_dir (str, optional): The directory where log files will be stored.
            Defaults to 'logs'.
        max_bytes (int, optional): The maximum size of the log file before it
            is rotated. Defaults to MAX_LOG_SIZE.
        backup_count (int, optional): The number of backup log files to keep.
            Defaults to 5.
        default_level (int, optional): The default logging level.
            Defaults to INFO.

    Raises:
        ValueError: If an invalid log level is provided.

    Returns:
        Logger: The configured logger instance.
    """
    if default_level not in {DEBUG, INFO, WARNING, ERROR, CRITICAL}:
        raise ValueError(f'Invalid log level: {default_level}')
    basepath = Path(log_dir).resolve()
    if not basepath.exists():
        basepath.mkdir(parents=True, exist_ok=True)
    log_path = basepath / f'{log_name}.log'

    logger = getLogger(log_name)
    logger.setLevel(default_level)

    handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8',
    )
    formatter = Formatter(
        '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if not logger.hasHandlers():
        console = StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)

    return logger
