import hashlib
import sqlite3
from datetime import date, datetime
from logging import Logger
from pathlib import Path
from typing import List, Optional, Union

from sftp_file_transfer.components.logger_setup import setup_logger

logger: Logger = setup_logger()

DEFAULT_DB_PATH = Path('data') / 'send_history.db'
_SHA256_HEX_LENGTH = 64

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

    def list_records(
        self,
        sent: Optional[bool] = None,
        since: Optional[date] = None,
        until: Optional[date] = None,
    ) -> List[sqlite3.Row]:
        """All columns, optionally filtered by sent flag and/or a
        file_date range (inclusive). Ordered by file_date DESC, then
        file_name."""
        clauses: List[str] = []
        params: List[Union[str, int]] = []
        if sent is not None:
            clauses.append('sent = ?')
            params.append(int(sent))
        if since is not None:
            clauses.append('file_date >= ?')
            params.append(since.isoformat())
        if until is not None:
            clauses.append('file_date <= ?')
            params.append(until.isoformat())

        query = 'SELECT * FROM send_history'
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        query += ' ORDER BY file_date DESC, file_name ASC'

        return self._conn.execute(query, params).fetchall()

    def get_summary(self) -> dict:
        """Aggregate counts and date range, plus last_sent_date via
        get_last_sent_date() so both agree on what counts as sent."""
        row = self._conn.execute(
            'SELECT COUNT(*) AS total, SUM(sent) AS total_sent, '
            'SUM(1 - sent) AS total_pending, MIN(file_date) AS min_date, '
            'MAX(file_date) AS max_date FROM send_history',
        ).fetchone()

        return {
            'total': row['total'] or 0,
            'total_sent': row['total_sent'] or 0,
            'total_pending': row['total_pending'] or 0,
            'date_range_start': (
                date.fromisoformat(row['min_date'])
                if row['min_date'] else None
            ),
            'date_range_end': (
                date.fromisoformat(row['max_date'])
                if row['max_date'] else None
            ),
            'last_sent_date': self.get_last_sent_date(),
        }

    def find_records(self, identifier: str) -> List[sqlite3.Row]:
        """Resolve identifier as either an exact path_hash (64 hex
        chars, case-insensitive) or a case-insensitive substring match
        against local_path. [] if nothing matches."""
        is_hash = len(identifier) == _SHA256_HEX_LENGTH and all(
            c in '0123456789abcdef' for c in identifier.lower()
        )
        if is_hash:
            return self._conn.execute(
                'SELECT * FROM send_history WHERE path_hash = ?',
                (identifier.lower(),),
            ).fetchall()
        return self._conn.execute(
            'SELECT * FROM send_history WHERE local_path LIKE ?',
            (f'%{identifier}%',),
        ).fetchall()

    def reset_record(self, identifier: str) -> List[sqlite3.Row]:
        """Uses find_records(identifier) to resolve candidates, then
        sets sent=0 and sent_at=NULL for every matched row, without
        touching attempts/last_error/last_attempt_at."""
        rows = self.find_records(identifier)
        if not rows:
            return []
        hashes = [row['path_hash'] for row in rows]
        self._conn.executemany(
            'UPDATE send_history SET sent = 0, sent_at = NULL '
            'WHERE path_hash = ?',
            [(h,) for h in hashes],
        )
        self._conn.commit()
        logger.info(
            f'Reset {len(hashes)} record(s) for identifier={identifier!r}',
        )
        placeholders = ','.join('?' * len(hashes))
        return self._conn.execute(
            f'SELECT * FROM send_history '
            f'WHERE path_hash IN ({placeholders})',
            hashes,
        ).fetchall()

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
