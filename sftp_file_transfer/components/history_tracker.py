import hashlib
import os
from datetime import date, datetime
from logging import Logger
from pathlib import Path
from types import TracebackType
from typing import List, Optional, Set, Type, Union

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import Index, create_engine, func, select, update
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from sftp_file_transfer.components.logger_setup import setup_logger

logger: Logger = setup_logger()

DEFAULT_DB_PATH = Path('data') / 'send_history.db'
_SHA256_HEX_LENGTH = 64


def resolve_history_db_path() -> Path:
    """Resolve the send-history ledger path from the project .env file.

    Every entry point (scheduled daemon, monitor TUI/daemon, history
    CLI) should call this instead of reading HISTORY_DB_PATH directly,
    so they all agree on the same ledger file regardless of each
    process's own working directory or whether it already loaded the
    .env file itself.

    Returns:
        Path: HISTORY_DB_PATH from the environment/.env file, or
            DEFAULT_DB_PATH if it isn't set.
    """
    load_dotenv(find_dotenv())
    return Path(os.getenv('HISTORY_DB_PATH', DEFAULT_DB_PATH))


class Base(DeclarativeBase):
    """Declarative base for the send-history ORM models."""


class SendHistory(Base):
    """ORM model for the ``send_history`` ledger table.

    Attributes:
        path_hash: SHA256 hex digest of the resolved local path (PK).
        file_name: Base name of the file.
        local_path: Fully-resolved absolute path of the file.
        file_date: ISO date string of the file's mtime.
        sent: 1 if the file has been successfully sent, else 0.
        attempts: Number of send attempts recorded for this file.
        last_error: Error message from the most recent failed attempt.
        last_attempt_at: ISO datetime string of the most recent attempt.
        sent_at: ISO datetime string of the first successful attempt.
    """

    __tablename__ = 'send_history'
    __table_args__ = (
        Index('idx_send_history_sent_date', 'sent', 'file_date'),
    )

    path_hash: Mapped[str] = mapped_column(primary_key=True)
    file_name: Mapped[str]
    local_path: Mapped[str]
    file_date: Mapped[str]
    sent: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[Optional[str]] = mapped_column(default=None)
    last_attempt_at: Mapped[Optional[str]] = mapped_column(default=None)
    sent_at: Mapped[Optional[str]] = mapped_column(default=None)


class HistoryTracker:
    """Track file send attempts in a SQLite-backed ledger via SQLAlchemy ORM.

    Used as a context manager so the session lifecycle matches a
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
        self._engine = create_engine(f'sqlite:///{self.db_path}')
        self._session_factory = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )
        self._session: Optional[Session] = None

    def __enter__(self) -> 'HistoryTracker':
        """Open the ORM session for this context, creating the schema.

        Creates the send_history table/index (if not already present)
        and opens a new SQLAlchemy Session bound to the engine.

        Returns:
            self: The HistoryTracker instance, ready for queries.
        """
        Base.metadata.create_all(self._engine)
        self._session = self._session_factory()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the ORM session, committing any pending changes.

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
        if self._session:
            self._session.commit()
            self._session.close()
            self._session = None

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
        max_date = self._session.execute(
            select(func.max(SendHistory.file_date)).where(
                SendHistory.sent == 1,
            ),
        ).scalar_one_or_none()
        if max_date is None:
            return None
        return date.fromisoformat(max_date)

    def get_sent_hashes(self) -> Set[str]:
        """Retrieve all path hashes of successfully sent files.

        Returns:
            Set[str]: A set of path_hash values (SHA256 hex digests) for
                all rows where sent=1. Empty set if no files have been
                marked as sent.
        """
        hashes = self._session.execute(
            select(SendHistory.path_hash).where(SendHistory.sent == 1),
        ).scalars()
        return set(hashes)

    def get_pending_failed_files(self) -> List[Path]:
        """Retrieve all local file paths that have not been sent yet.

        Returns:
            List[Path]: A list of local_path values (as Path objects) for
                all rows where sent=0 (unsuccessful send attempts). Empty
                list if all files have been successfully sent or the
                table is empty.
        """
        paths = self._session.execute(
            select(SendHistory.local_path).where(SendHistory.sent == 0),
        ).scalars()
        return [Path(p) for p in paths]

    def list_records(
        self,
        sent: Optional[bool] = None,
        since: Optional[date] = None,
        until: Optional[date] = None,
    ) -> List[SendHistory]:
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
            List[SendHistory]: All matching records, ordered by
                file_date DESC, then file_name ASC.
        """
        stmt = select(SendHistory)
        if sent is not None:
            stmt = stmt.where(SendHistory.sent == int(sent))
        if since is not None:
            stmt = stmt.where(SendHistory.file_date >= since.isoformat())
        if until is not None:
            stmt = stmt.where(SendHistory.file_date <= until.isoformat())
        stmt = stmt.order_by(
            SendHistory.file_date.desc(),
            SendHistory.file_name.asc(),
        )

        return list(self._session.execute(stmt).scalars().all())

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
        total, total_sent, total_pending, min_date, max_date = (
            self._session.execute(
                select(
                    func.count(),
                    func.sum(SendHistory.sent),
                    func.sum(1 - SendHistory.sent),
                    func.min(SendHistory.file_date),
                    func.max(SendHistory.file_date),
                ),
            ).one()
        )

        return {
            'total': total or 0,
            'total_sent': total_sent or 0,
            'total_pending': total_pending or 0,
            'date_range_start': (
                date.fromisoformat(min_date) if min_date else None
            ),
            'date_range_end': (
                date.fromisoformat(max_date) if max_date else None
            ),
            'last_sent_date': self.get_last_sent_date(),
        }

    def find_records(self, identifier: str) -> List[SendHistory]:
        """Locate send_history records by path hash or substring match.

        Resolves identifier as either an exact path_hash (64 hex chars,
        case-insensitive) or as a case-insensitive substring match
        against local_path.

        Args:
            identifier: Either a SHA256 hex digest (64 hex characters) to
                match exactly against path_hash, or any other string to
                substring-match against local_path.

        Returns:
            List[SendHistory]: All matching records, or an empty list if
                nothing matches.
        """
        is_hash = len(identifier) == _SHA256_HEX_LENGTH and all(
            c in '0123456789abcdef' for c in identifier.lower()
        )
        if is_hash:
            stmt = select(SendHistory).where(
                SendHistory.path_hash == identifier.lower(),
            )
        else:
            stmt = select(SendHistory).where(
                SendHistory.local_path.like(f'%{identifier}%'),
            )
        return list(self._session.execute(stmt).scalars().all())

    def reset_record(self, identifier: str) -> List[SendHistory]:
        """Reset send status for matching records to pending.

        Uses find_records(identifier) to resolve candidate rows, then
        sets sent=0 and sent_at=NULL for every matched row. Preserves
        attempts, last_error, and last_attempt_at for audit purposes.

        Args:
            identifier: Either a path_hash (64 hex chars) or a substring
                to match against local_path, as per find_records().

        Returns:
            List[SendHistory]: The freshly-updated rows after reset, or
                an empty list if no records matched the identifier.
        """
        rows = self.find_records(identifier)
        if not rows:
            return []
        hashes = [row.path_hash for row in rows]
        self._session.execute(
            update(SendHistory)
            .where(SendHistory.path_hash.in_(hashes))
            .values(sent=0, sent_at=None),
        )
        self._session.commit()
        logger.info(
            f'Reset {len(hashes)} record(s) for identifier={identifier!r}',
        )
        return list(
            self._session.execute(
                select(SendHistory).where(
                    SendHistory.path_hash.in_(hashes),
                ),
            ).scalars().all(),
        )

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

        record = self._session.get(SendHistory, path_hash)
        if record is None:
            record = SendHistory(
                path_hash=path_hash,
                file_name=resolved.name,
                local_path=str(resolved),
                file_date=file_date,
                sent=int(success),
                attempts=1,
                last_error=None if success else error,
                last_attempt_at=now,
                sent_at=now if success else None,
            )
            self._session.add(record)
        else:
            record.file_name = resolved.name
            record.local_path = str(resolved)
            record.file_date = file_date
            record.sent = int(success)
            record.attempts += 1
            record.last_error = None if success else error
            record.last_attempt_at = now
            if success and record.sent_at is None:
                record.sent_at = now

        self._session.commit()
        logger.info(
            f'Recorded send attempt for {resolved}: '
            f'success={success}, error={error}',
        )
