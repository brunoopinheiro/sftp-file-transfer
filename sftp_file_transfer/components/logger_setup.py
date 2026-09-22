from datetime import datetime
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
from typing import Any

from sftp_file_transfer import __version__

MAX_LOG_SIZE = 1 * 1024 * 1024  # 1 MB
# 1 MB x (1 active + 59 backups) = 60 MB, the same retention ceiling
# as the previous 10 MB x (1 + 5), split into files small enough to
# open and search individually.
BACKUP_COUNT = 59


class ProcessSafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that survives a rollover another process blocks.

    Several entry points write to the same log file at once — notably
    `sftp_monitor`, whose TUI process runs alongside the background
    daemon it spawned. On Windows the rename performed during a
    rollover fails with a sharing violation while any other process
    holds the file open. The stdlib handler reports that through
    `logging`'s own error path, which is invisible for a daemon spawned
    with stderr redirected to DEVNULL, and log writes can stop for good.

    This handler instead treats a blocked rollover as a transient
    condition: it keeps appending to the current file and retries the
    rollover on a later record.

    Only a sharing violation is tolerated. Any other OSError — a full
    disk, a read-only directory — is a real fault that would otherwise
    let the log grow without bound behind a silent retry loop, so it is
    re-raised for logging's own error path to report.
    """

    #: Consecutive blocked rollovers before the problem is announced.
    WARN_AFTER_BLOCKED_ROLLOVERS = 10

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the handler and its blocked-rollover counter.

        Returns:
            None.
        """
        super().__init__(*args, **kwargs)
        self._blocked_rollovers = 0

    def doRollover(self) -> None:
        """Roll the log over, tolerating a rename blocked by another process.

        Raises:
            OSError: If the rollover failed for any reason other than
                the file being held open by another process.

        Returns:
            None.
        """
        try:
            super().doRollover()
        except PermissionError:
            self._blocked_rollovers += 1
            if self.stream is None:
                self.stream = self._open()
            self._warn_if_persistently_blocked()
        else:
            self._blocked_rollovers = 0

    def _warn_if_persistently_blocked(self) -> None:
        """Note in the log itself that rollover keeps being blocked.

        The warning is written to the log file rather than raised
        through logging's error path, because the monitor daemon is
        spawned with stderr redirected to DEVNULL — the log is the only
        place an operator would ever see it.

        Returns:
            None.
        """
        if self._blocked_rollovers % self.WARN_AFTER_BLOCKED_ROLLOVERS:
            return
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.stream.write(
            f'[{timestamp}] WARNING {__name__}: log rollover blocked by '
            f'another process {self._blocked_rollovers} times in a row; '
            f'this file is over its size cap.\n',
        )
        self.flush()


def setup_logger(
    log_name: str = 'sftp_file_transfer',
    log_dir: str = 'logs',
    max_bytes: int = MAX_LOG_SIZE,
    backup_count: int = BACKUP_COUNT,
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
            Defaults to BACKUP_COUNT.
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

    if not logger.handlers:
        formatter = Formatter(
            '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        handler = ProcessSafeRotatingFileHandler(
            filename=log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8',
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        console = StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)

    return logger


def log_startup_banner(logger: Logger, entry_point: str) -> None:
    """Record which build is running, as the first line of a run.

    Without this a site's log cannot be traced back to the build that
    produced it, which is exactly the gap felt when diagnosing an
    incident from logs alone.

    Args:
        logger (Logger): The logger to write the banner to.
        entry_point (str): Name of the executable/entry point starting.

    Returns:
        None.
    """
    logger.info('Starting %s (version %s)', entry_point, __version__)
