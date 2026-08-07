import hashlib
import sqlite3
from datetime import date, datetime
from logging import Logger
from pathlib import Path
from typing import List, Optional, Union

from sftp_file_transfer.components.logger_setup import setup_logger

logger: Logger = setup_logger()

DEFAULT_DB_PATH = Path('data') / 'send_history.db'

_DDL = """
CREATE TABLE IF NOT EXISTS send_history (
    path_hash       TEXT PRIMARY KEY,
    file_name       TEXT NOT NULL,
    local_path      TEXT NOT NULL,
    file_date       TEXT NOT NULL,
    sent            INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    last_attempt_at TEXT,
    sent_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_send_history_sent_date
    ON send_history (sent, file_date);
"""

_UPSERT = """
INSERT INTO send_history (
    path_hash, file_name, local_path, file_date,
    sent, attempts, last_error, last_attempt_at, sent_at
) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
ON CONFLICT(path_hash) DO UPDATE SET
    sent = excluded.sent,
    attempts = send_history.attempts + 1,
    last_error = excluded.last_error,
    last_attempt_at = excluded.last_attempt_at,
    sent_at = CASE
        WHEN excluded.sent = 1 AND send_history.sent_at IS NULL
            THEN excluded.last_attempt_at
        ELSE send_history.sent_at
    END
"""


class HistoryTracker:
    """Track file send attempts in a SQLite-backed ledger.

    Used as a context manager so the connection lifecycle matches a
    single scheduled run, similarly to SFTPManager.
    """

    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> 'HistoryTracker':
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    @staticmethod
    def hash_path(local_path: Union[str, Path]) -> str:
        """sha256 hex digest of the resolved local path string."""
        return hashlib.sha256(
            str(Path(local_path).resolve()).encode('utf-8')
        ).hexdigest()

    @staticmethod
    def file_date_of(local_path: Union[str, Path]) -> date:
        """Filesystem mtime date, consistent with
        FileManager.filter_files_by_date semantics."""
        return datetime.fromtimestamp(Path(local_path).stat().st_mtime).date()

    def get_last_sent_date(self) -> Optional[date]:
        """Max file_date among rows where sent=1, or None if empty."""
        row = self._conn.execute(
            'SELECT MAX(file_date) AS max_date '
            'FROM send_history WHERE sent = 1',
        ).fetchone()
        if row is None or row['max_date'] is None:
            return None
        return date.fromisoformat(row['max_date'])

    def get_pending_failed_files(self) -> List[Path]:
        """local_path values for rows still marked sent=0."""
        rows = self._conn.execute(
            'SELECT local_path FROM send_history WHERE sent = 0',
        ).fetchall()
        return [Path(row['local_path']) for row in rows]

    def record_attempt(
        self,
        local_path: Union[str, Path],
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Upsert the outcome of one upload attempt."""
        resolved = Path(local_path).resolve()
        path_hash = self.hash_path(resolved)
        file_date = self.file_date_of(resolved).isoformat()
        now = datetime.now().isoformat(timespec='seconds')
        self._conn.execute(
            _UPSERT,
            (
                path_hash,
                resolved.name,
                str(resolved),
                file_date,
                int(success),
                None if success else error,
                now,
                now if success else None,
            ),
        )
        self._conn.commit()
        logger.info(
            f'Recorded send attempt for {resolved}: '
            f'success={success}, error={error}',
        )
