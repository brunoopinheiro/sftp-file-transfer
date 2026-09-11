import hashlib
import sqlite3
from datetime import date, datetime
from logging import Logger
from pathlib import Path
from types import TracebackType
from typing import List, Optional, Set, Type, Union

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

    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH) -> None:
        """Initialize the HistoryTracker.

        Args:
            db_path: Path to the SQLite database file. Defaults to
                'data/send_history.db'. Parent directory is created if it
                does not exist.

        Returns:
            None
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> 'HistoryTracker':
        """Open and initialize the SQLite connection for this context.

        Opens the sqlite3 connection, sets the row_factory to sqlite3.Row
        to retrieve rows as mappings, and applies the table/index DDL.

        Returns:
            self: The HistoryTracker instance, ready for queries.
        """
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the SQLite connection, committing any pending changes.

        Args:
            exc_type: The exception type, if an exception occurred in the
                context block. None if no exception.
            exc_value: The exception instance, if an exception occurred
                in the context block. None if no exception.
            traceback: The traceback object, if an exception occurred in
                the context block. None if no exception.

        Returns:
            None
        """
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    @staticmethod
    def hash_path(local_path: Union[str, Path]) -> str:
        """Compute the SHA256 hash of a resolved local path string.

        Args:
            local_path: Path to the file (as str or Path).

        Returns:
            str: SHA256 hex digest (64 hex characters) of the
                fully-resolved absolute path.
        """
        return hashlib.sha256(
            str(Path(local_path).resolve()).encode('utf-8')
        ).hexdigest()

    @staticmethod
    def file_date_of(local_path: Union[str, Path]) -> date:
        """Extract the file modification date from filesystem metadata.

        Args:
            local_path: Path to the file (as str or Path).

        Returns:
            date: The file's modification date (mtime), consistent with
                FileManager.filter_files_by_date semantics.
        """
        return datetime.fromtimestamp(Path(local_path).stat().st_mtime).date()

    def get_last_sent_date(self) -> Optional[date]:
        """Retrieve the most recent file date among successfully sent files.

        Returns:
            Optional[date]: The maximum file_date among all rows where
                sent=1, or None if no files have been marked as sent or
                the table is empty.
        """
        row = self._conn.execute(
            'SELECT MAX(file_date) AS max_date '
            'FROM send_history WHERE sent = 1',
        ).fetchone()
        if row is None or row['max_date'] is None:
            return None
        return date.fromisoformat(row['max_date'])

    def get_sent_hashes(self) -> Set[str]:
        """Retrieve all path hashes of successfully sent files.

        Returns:
            Set[str]: A set of path_hash values (SHA256 hex digests) for
                all rows where sent=1. Empty set if no files have been
                marked as sent.
        """
        rows = self._conn.execute(
            'SELECT path_hash FROM send_history WHERE sent = 1',
        ).fetchall()
        return {row['path_hash'] for row in rows}

    def get_pending_failed_files(self) -> List[Path]:
        """Retrieve all local file paths that have not been sent yet.

        Returns:
            List[Path]: A list of local_path values (as Path objects) for
                all rows where sent=0 (unsuccessful send attempts). Empty
                list if all files have been successfully sent or the
                table is empty.
        """
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
        """Retrieve all send_history records, optionally filtered.

        Args:
            sent: If provided, filter to rows where sent equals this
                boolean (1 for True, 0 for False). None (default) returns
                all rows regardless of sent status.
            since: If provided, filter to rows where file_date >=
                since.isoformat() (inclusive). None (default) returns all
                dates from the start.
            until: If provided, filter to rows where file_date <=
                until.isoformat() (inclusive). None (default) returns all
                dates up to the end.

        Returns:
            List[sqlite3.Row]: All columns from matching rows, ordered by
                file_date DESC, then file_name ASC.
        """
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
        """Compute aggregate statistics across the send_history table.

        Returns:
            dict: A dictionary with the following keys:
                'total' (int): Total count of all records.
                'total_sent' (int): Count of records where sent=1.
                'total_pending' (int): Count of records where sent=0.
                'date_range_start' (Optional[date]): The minimum
                    file_date in the table, or None if empty.
                'date_range_end' (Optional[date]): The maximum
                    file_date in the table, or None if empty.
                'last_sent_date' (Optional[date]): The most recent
                    file_date among successfully sent files (sent=1),
                    computed via get_last_sent_date(), ensuring
                    consistency in what counts as sent.
        """
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
                if row['min_date']
                else None
            ),
            'date_range_end': (
                date.fromisoformat(row['max_date'])
                if row['max_date']
                else None
            ),
            'last_sent_date': self.get_last_sent_date(),
        }

    def find_records(self, identifier: str) -> List[sqlite3.Row]:
        """Locate send_history records by path hash or substring match.

        Resolves identifier as either an exact path_hash (64 hex chars,
        case-insensitive) or as a case-insensitive substring match
        against local_path.

        Args:
            identifier: Either a SHA256 hex digest (64 hex characters) to
                match exactly against path_hash, or any other string to
                substring-match against local_path.

        Returns:
            List[sqlite3.Row]: All matching records, or an empty list if
                nothing matches.
        """
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
        """Reset send status for matching records to pending.

        Uses find_records(identifier) to resolve candidate rows, then
        sets sent=0 and sent_at=NULL for every matched row. Preserves
        attempts, last_error, and last_attempt_at for audit purposes.

        Args:
            identifier: Either a path_hash (64 hex chars) or a substring
                to match against local_path, as per find_records().

        Returns:
            List[sqlite3.Row]: The freshly-updated rows after reset, or
                an empty list if no records matched the identifier.
        """
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
            f'SELECT * FROM send_history WHERE path_hash IN ({placeholders})',
            hashes,
        ).fetchall()

    def record_attempt(
        self,
        local_path: Union[str, Path],
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Record the outcome of a single send attempt.

        Inserts a new record or updates an existing one (identified by
        path_hash) with the result of an upload attempt. Increments the
        attempts counter on each call.

        Args:
            local_path: Path to the file (as str or Path).
            success: Whether the send attempt succeeded (True=1, False=0).
            error: If success=False, the error message to log. Ignored if
                success=True. Defaults to None.

        Returns:
            None
        """
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
