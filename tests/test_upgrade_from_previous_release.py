"""Regression tests for upgrading a deployed site from an earlier release.

Deployment replaces only the executable: `data\\send_history.db`,
`data\\known_hosts.json` and `logs\\` are inherited untouched. These
tests therefore run the current code against state shaped exactly as a
previous release left it.

Baselines are named after the shape they froze rather than the release
that produced it -- the ledger's `pre-orm`/`orm` schema eras, the log
layout, the host key pin format -- because what breaks an upgrade is a
change of shape, and a given shape usually spans several releases.
Adding a new one is how a future release records what it must stay
compatible with.

The baselines are checked in as code rather than as binary fixtures.
`.gitignore` ignores `data/`, `*.log` and `.env`, so the obvious fixture
paths could not be tracked at all; and more importantly, a committed
`.db` would be regenerated from current code the first time a drift test
went red, at which point every assertion here would pass vacuously
behind an unreadable diff.
"""

import hashlib
import json
import sqlite3
import uuid
from dataclasses import fields
from datetime import date
from pathlib import Path

import pytest

from sftp_file_transfer.components.history_tracker import (
    DEFAULT_DB_PATH,
    HistoryTracker,
    SendHistory,
    resolve_history_db_path,
)
from sftp_file_transfer.components.host_pins import (
    HostKeyMismatchError,
    HostPin,
    get_pin,
    verify_or_pin,
)
from sftp_file_transfer.components.logger_setup import (
    BACKUP_COUNT,
    MAX_LOG_SIZE,
    setup_logger,
)
from sftp_file_transfer.scheduled import select_files_to_send

# --------------------------------------------------------------------
# The frozen ledger baselines.
# --------------------------------------------------------------------

# Verbatim DDL from the pre-ORM tracker. Retrieve with:
#   git show f01f463^:sftp_file_transfer/components/history_tracker.py
# It matters that this is NOT produced by Base.metadata.create_all():
# create_all(checkfirst=True) never rewrites an existing table, so a
# site first deployed before the ORM migration still carries this exact
# DDL today.
_LEGACY_DDL_PRE_ORM = """
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

# What create_all() emitted after the ORM migration: VARCHAR instead of
# TEXT, and no SQL DEFAULT (the ORM's defaults are Python-side). A site
# first deployed after that migration carries this shape instead.
_LEGACY_DDL_ORM = """
CREATE TABLE send_history (
    path_hash VARCHAR NOT NULL,
    file_name VARCHAR NOT NULL,
    local_path VARCHAR NOT NULL,
    file_date VARCHAR NOT NULL,
    sent INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    last_error VARCHAR,
    last_attempt_at VARCHAR,
    sent_at VARCHAR,
    PRIMARY KEY (path_hash)
);
CREATE INDEX idx_send_history_sent_date
    ON send_history (sent, file_date);
"""

_LEGACY_DDLS = {
    'pre-orm': _LEGACY_DDL_PRE_ORM,
    'orm': _LEGACY_DDL_ORM,
}

_INSERT_ROW = """
INSERT INTO send_history (
    path_hash, file_name, local_path, file_date,
    sent, attempts, last_error, last_attempt_at, sent_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# Rows as a real site recorded them: absolute Windows paths that do not
# exist on this machine, including the awkward shapes a live ledger
# accumulates (accents, a UNC share, the same file_name in two
# directories, a sent row with no sent_at, an empty error string, and a
# retry count from months of failures).
_FROZEN_ROWS = (
    # local_path, file_date, sent, attempts, last_error, sent_at
    (
        r'C:\SFTP\arquivos\NFCe_20240101_0001.xml',
        '2024-01-01',
        1,
        1,
        None,
        '2024-01-01T08:00:00',
    ),
    (
        r'C:\SFTP\arquivos\NFCe_20240101_0002.xml',
        '2024-01-01',
        1,
        2,
        None,
        None,
    ),
    (
        r'C:\SFTP\arquivos\NFCe_ATENDIMENTO_JOÃO.xml',
        '2024-02-15',
        1,
        1,
        '',
        '2024-02-15T09:30:00',
    ),
    (
        r'\\nas\fiscal\out\NFCe_20240301_0007.xml',
        '2024-03-01',
        0,
        40000,
        'Connection timed out',
        None,
    ),
    (
        r'C:\SFTP\arquivos\antigos\NFCe_20240101_0001.xml',
        '2024-01-01',
        0,
        3,
        'Permission denied',
        None,
    ),
)

_EXPECTED_FROZEN_ROWS = len(_FROZEN_ROWS)
_EXPECTED_FROZEN_SENT = 3
_EXPECTED_FROZEN_PENDING = 2
_LEGACY_ATTEMPTS_BEFORE = 3
_LEGACY_ATTEMPTS_AFTER = 4

# --------------------------------------------------------------------
# Log baseline. Earlier releases used 10 MB x 5 backups (and 5 MB x 5
# before that); both produce the same file set, and the current 1 MB
# threshold sits below either, so one shape covers them.
# --------------------------------------------------------------------

_LEGACY_BACKUPS = 5
_ACTIVE_MARKER = 'LEGACY-ACTIVE'
_BACKUP_MARKER = 'LEGACY-BACKUP-{index}'
_OVERSIZED_PADDING = 2048
_SMALL_LOG_BYTES = 512
_TINY_MAX_BYTES = 256
_ROLLOVER_RECORDS = 200

# --------------------------------------------------------------------
# The frozen host key pin baseline.
# --------------------------------------------------------------------

# Verbatim known_hosts.json as the release that introduced host key
# verification wrote it. Deliberately NOT produced by save_pin(), for
# the same reason the ledger DDL is not produced by create_all(): a
# baseline generated from current code adopts any format change
# silently, and the drift it exists to catch would pass unnoticed.
_LEGACY_PIN_HOST = 'sftp.hotel.example'
_LEGACY_PIN_PORT = 22
_LEGACY_PIN_KEY_TYPE = 'ssh-ed25519'
_LEGACY_PIN_FINGERPRINT = 'SHA256:0h2DQOSyM4vT7bWDkkYNC0ux1lCgRSWlrGsHpvmiSMU'
_LEGACY_KNOWN_HOSTS = """{
  "sftp.hotel.example:22": {
    "fingerprint": "SHA256:0h2DQOSyM4vT7bWDkkYNC0ux1lCgRSWlrGsHpvmiSMU",
    "key_type": "ssh-ed25519",
    "pinned_at": "2026-09-22T14:03:11"
  }
}
"""


def _legacy_hash(local_path: str) -> str:
    """Hash a frozen path exactly as an earlier release stored it.

    Deliberately not HistoryTracker.hash_path(): that resolves the path
    first, and resolution is environment-dependent (on Windows it also
    expands 8.3 short names such as BRUNO~1.PIN). Hashing the literal
    reproduces what a site recorded for an absolute path, for which
    resolution is the identity.

    Args:
        local_path: The path string as the ledger stored it.

    Returns:
        str: SHA256 hex digest of the path string.
    """
    return hashlib.sha256(local_path.encode('utf-8')).hexdigest()


def _frozen_seed_rows() -> list:
    """Build the insertable form of the frozen legacy rows."""
    return [
        (
            _legacy_hash(local_path),
            Path(local_path).name,
            local_path,
            file_date,
            sent,
            attempts,
            last_error,
            f'{file_date}T10:00:00',
            sent_at,
        )
        for local_path, file_date, sent, attempts, last_error, sent_at in (
            _FROZEN_ROWS
        )
    ]


def _live_row(local_path: Path, sent: int, attempts: int = 1) -> tuple:
    """Build a ledger row for a file that really exists on disk.

    Retry behaviour depends on the recorded path existing, so rows that
    drive select_files_to_send() must be keyed the way the running code
    keys them, via HistoryTracker.hash_path().

    Args:
        local_path: A file that exists in the test's temp directory.
        sent: 1 for a delivered file, 0 for a pending/failed one.
        attempts: Number of attempts already recorded.

    Returns:
        tuple: A row ready for _INSERT_ROW.
    """
    resolved = local_path.resolve()
    today = date.today().isoformat()
    return (
        HistoryTracker.hash_path(resolved),
        resolved.name,
        str(resolved),
        today,
        sent,
        attempts,
        None if sent else 'Connection reset',
        f'{today}T10:00:00',
        f'{today}T10:00:00' if sent else None,
    )


def _build_legacy_ledger(db_path: Path, ddl: str, live_rows=()) -> Path:
    """Materialise a ledger exactly as a previous release left it.

    Args:
        db_path: Where to create the SQLite file.
        ddl: One of the frozen legacy DDL variants.
        live_rows: Extra rows for files that exist on disk.

    Returns:
        Path: The path the ledger was created at.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(ddl)
        conn.executemany(_INSERT_ROW, _frozen_seed_rows())
        conn.executemany(_INSERT_ROW, live_rows)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _table_sql(db_path: Path) -> str:
    """Read the stored CREATE TABLE statement for send_history."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'send_history'",
        ).fetchone()[0]
    finally:
        conn.close()


def _column_names(db_path: Path) -> set:
    """Read the column names actually present in send_history."""
    conn = sqlite3.connect(db_path)
    try:
        return {
            row[1] for row in conn.execute('PRAGMA table_info(send_history)')
        }
    finally:
        conn.close()


def _index_names(db_path: Path) -> set:
    """Read the named indexes present on send_history."""
    conn = sqlite3.connect(db_path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name NOT LIKE 'sqlite_%'",
            )
        }
    finally:
        conn.close()


def _row_count(db_path: Path) -> int:
    """Count the rows currently in send_history."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute('SELECT count(*) FROM send_history').fetchone()[0]
    finally:
        conn.close()


def _write_log_file(path: Path, marker: str, size_bytes: int = 0) -> None:
    """Write a log file tagged with a findable marker.

    Args:
        path: File to write.
        marker: Unique string identifying this file's original content.
        size_bytes: Pad the file to at least this size.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f'[2025-01-01 00:00:00] INFO sftp_file_transfer: {marker}\n'
    padding = max(0, size_bytes - len(header))
    path.write_text(header + ('p' * padding), encoding='utf-8')


def _seed_legacy_logs(log_dir: Path, name: str, active_bytes: int) -> None:
    """Create an inherited log plus the five backups earlier releases kept."""
    _write_log_file(log_dir / f'{name}.log', _ACTIVE_MARKER, active_bytes)
    for index in range(1, _LEGACY_BACKUPS + 1):
        _write_log_file(
            log_dir / f'{name}.log.{index}',
            _BACKUP_MARKER.format(index=index),
        )


def _build_legacy_pin_file(pins_path: Path) -> Path:
    """Materialise a pin file exactly as an earlier release wrote it.

    Args:
        pins_path: Where to create the known_hosts.json file.

    Returns:
        Path: The path the pin file was created at.
    """
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(_LEGACY_KNOWN_HOSTS, encoding='utf-8')
    return pins_path


def _first_pin_entry(pins_path: Path) -> dict:
    """Read the single stored entry out of a pin file."""
    stored = json.loads(pins_path.read_text(encoding='utf-8'))
    return next(iter(stored.values()))


@pytest.fixture
def upgrade_logger():
    """Provide a setup_logger factory that closes handlers on teardown.

    setup_logger caches by logger name and only attaches handlers when
    the logger has none, so every test needs a fresh name. On Windows an
    open handler also keeps a lock that breaks tmp_path cleanup.
    """
    created = []

    def _make(name, **kwargs):
        created.append(setup_logger(log_name=name, **kwargs))
        return created[-1]

    yield _make

    for logger in created:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


# --------------------------------------------------------------------
# A. Baseline integrity
# --------------------------------------------------------------------


def test_pre_orm_baseline_really_is_pre_orm(tmp_path):
    """Test that the pre-ORM baseline is not silently a modern table.

    Every other assertion here is worthless if the fixture is ever
    regenerated from current code, so pin its distinguishing features.
    """
    db_path = _build_legacy_ledger(
        tmp_path / 'send_history.db',
        _LEGACY_DDL_PRE_ORM,
    )

    sql = _table_sql(db_path)

    assert 'DEFAULT 0' in sql
    assert 'TEXT' in sql
    assert 'VARCHAR' not in sql.upper()


def test_orm_era_baseline_really_is_orm_shaped(tmp_path):
    """Test that the ORM-era baseline keeps its distinguishing shape."""
    db_path = _build_legacy_ledger(
        tmp_path / 'send_history.db',
        _LEGACY_DDL_ORM,
    )

    sql = _table_sql(db_path)

    assert 'VARCHAR' in sql
    assert 'DEFAULT' not in sql.upper()


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_a_legacy_ledger_carries_no_migration_marker(tmp_path, ddl):
    """Test that legacy ledgers have no schema version recorded.

    There has never been a migration mechanism, so PRAGMA user_version
    is 0 everywhere. Recorded here because it is the natural hook if a
    migration is ever needed.
    """
    db_path = _build_legacy_ledger(tmp_path / 'send_history.db', ddl)

    conn = sqlite3.connect(db_path)
    try:
        user_version = conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()

    assert user_version == 0


# --------------------------------------------------------------------
# B. Ledger continuity
# --------------------------------------------------------------------


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_legacy_ledger_is_opened_in_place_not_recreated(tmp_path, ddl):
    """Test that the ledger is adopted in place, not replaced.

    If the ledger were recreated, every file a site already delivered
    would be sent again.
    """
    db_path = _build_legacy_ledger(tmp_path / 'send_history.db', ddl)
    sql_before = _table_sql(db_path)
    rows_before = _row_count(db_path)

    with HistoryTracker(db_path):
        pass

    assert _table_sql(db_path) == sql_before
    assert _row_count(db_path) == rows_before
    assert 'idx_send_history_sent_date' in _index_names(db_path)


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_legacy_rows_are_readable_through_the_current_orm(tmp_path, ddl):
    """Test that every inherited row round-trips through the current ORM.

    The semantic half of the drift guard: it exercises the columns the
    ORM actually selects, including the nullable ones a column-name
    comparison cannot type-check.
    """
    db_path = _build_legacy_ledger(tmp_path / 'send_history.db', ddl)

    with HistoryTracker(db_path) as tracker:
        records = tracker.list_records()
        summary = tracker.get_summary()

    assert len(records) == _EXPECTED_FROZEN_ROWS
    assert summary['total'] == _EXPECTED_FROZEN_ROWS
    assert summary['total_sent'] == _EXPECTED_FROZEN_SENT
    assert summary['total_pending'] == _EXPECTED_FROZEN_PENDING

    stored_paths = {record.local_path for record in records}
    for local_path, *_ in _FROZEN_ROWS:
        assert local_path in stored_paths

    for record in records:
        assert isinstance(record.sent, int)
        assert date.fromisoformat(record.file_date)


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_every_orm_column_exists_in_the_legacy_table(tmp_path, ddl):
    """Test that no ORM column is missing from an inherited table.

    The structural half of the drift guard. create_all(checkfirst=True)
    is a no-op against an existing table, so a column added to the model
    without a migration would never appear on a deployed site: startup
    succeeds and the first query touching it fails, on the hotel machine
    only, while CI stays green against a fresh database.
    """
    db_path = _build_legacy_ledger(tmp_path / 'send_history.db', ddl)

    with HistoryTracker(db_path):
        pass

    legacy_columns = _column_names(db_path)
    orm_columns = {column.name for column in SendHistory.__table__.columns}

    assert orm_columns <= legacy_columns, (
        f'ORM columns missing from a legacy ledger: '
        f'{sorted(orm_columns - legacy_columns)}. create_all() will NOT '
        f'add them to an existing table; a migration is required.'
    )


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_every_orm_index_exists_in_the_legacy_table(tmp_path, ddl):
    """Test that no declared index is missing from an inherited table.

    create_all() does not add an index to a table it did not create, so
    a new index would silently never exist on the one machine with a
    large ledger.
    """
    db_path = _build_legacy_ledger(tmp_path / 'send_history.db', ddl)

    with HistoryTracker(db_path):
        pass

    legacy_indexes = _index_names(db_path)
    orm_indexes = {index.name for index in SendHistory.__table__.indexes}

    assert orm_indexes <= legacy_indexes, (
        f'ORM indexes missing from a legacy ledger: '
        f'{sorted(orm_indexes - legacy_indexes)}.'
    )


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_new_attempts_write_into_the_legacy_table(tmp_path, ddl):
    """Test that the current code writes into an inherited table.

    Exercises the ORM's Python-side defaults against the legacy table's
    NOT NULL columns.
    """
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    existing = source_dir / 'retry_me.xml'
    existing.touch()
    db_path = _build_legacy_ledger(
        tmp_path / 'send_history.db',
        ddl,
        live_rows=[_live_row(existing, sent=0, attempts=3)],
    )
    brand_new = source_dir / 'brand_new.xml'
    brand_new.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(brand_new, success=True)
        tracker.record_attempt(existing, success=True)

        inserted = tracker.find_records('brand_new.xml')[0]
        updated = tracker.find_records('retry_me.xml')[0]

    assert inserted.sent == 1
    assert updated.sent == 1
    assert updated.attempts == _LEGACY_ATTEMPTS_AFTER


# --------------------------------------------------------------------
# C. Selection behaviour
# --------------------------------------------------------------------


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_already_sent_files_are_not_resent_after_the_upgrade(tmp_path, ddl):
    """Test that upgrading does not trigger a mass re-send.

    The most expensive way an upgrade can go wrong: every file the site
    already delivered would be uploaded again.
    """
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    delivered_one = source_dir / 'delivered_one.xml'
    delivered_two = source_dir / 'delivered_two.xml'
    untouched = source_dir / 'never_attempted.xml'
    for file in (delivered_one, delivered_two, untouched):
        file.touch()

    db_path = _build_legacy_ledger(
        tmp_path / 'send_history.db',
        ddl,
        live_rows=[
            _live_row(delivered_one, sent=1),
            _live_row(delivered_two, sent=1),
        ],
    )

    with HistoryTracker(db_path) as tracker:
        selected = select_files_to_send([str(source_dir)], '.xml', tracker)

    assert [file.name for file in selected] == ['never_attempted.xml']


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_previously_failed_files_are_still_retried_after_upgrade(
    tmp_path,
    ddl,
):
    """Test that inherited failures are retried, including outside the scan.

    A failed row whose file sits outside LOCAL_PATH can only be
    recovered through the pending-failed union, so including one proves
    that branch rather than just the directory scan.
    """
    source_dir = tmp_path / 'source'
    archive_dir = tmp_path / 'archive'
    source_dir.mkdir()
    archive_dir.mkdir()
    failed_in_scan = source_dir / 'failed_in_scan.xml'
    failed_elsewhere = archive_dir / 'failed_elsewhere.xml'
    failed_in_scan.touch()
    failed_elsewhere.touch()

    db_path = _build_legacy_ledger(
        tmp_path / 'send_history.db',
        ddl,
        live_rows=[
            _live_row(failed_in_scan, sent=0),
            _live_row(failed_elsewhere, sent=0),
        ],
    )

    with HistoryTracker(db_path) as tracker:
        selected = select_files_to_send([str(source_dir)], '.xml', tracker)

    assert {file.name for file in selected} == {
        'failed_in_scan.xml',
        'failed_elsewhere.xml',
    }


@pytest.mark.parametrize('ddl', _LEGACY_DDLS.values(), ids=_LEGACY_DDLS)
def test_rows_whose_files_are_gone_are_skipped_not_crashing(tmp_path, ddl):
    """Test that inherited rows for deleted files are simply skipped.

    A months-old ledger is full of rows whose files were archived away.
    Documents the is_file() filter, so a future change to it has to be
    deliberate.
    """
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    still_here = source_dir / 'still_here.xml'
    still_here.touch()
    archived = source_dir / 'archived.xml'
    archived.touch()
    archived_row = _live_row(archived, sent=0)
    archived.unlink()

    db_path = _build_legacy_ledger(
        tmp_path / 'send_history.db',
        ddl,
        live_rows=[archived_row],
    )

    with HistoryTracker(db_path) as tracker:
        selected = select_files_to_send([str(source_dir)], '.xml', tracker)

    assert [file.name for file in selected] == ['still_here.xml']


def test_selection_deduplicates_new_files_and_inherited_retries(tmp_path):
    """Test that a file both scanned and pending is selected once."""
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    retry = source_dir / 'retry.xml'
    fresh = source_dir / 'fresh.xml'
    retry.touch()
    fresh.touch()

    db_path = _build_legacy_ledger(
        tmp_path / 'send_history.db',
        _LEGACY_DDL_PRE_ORM,
        live_rows=[_live_row(retry, sent=0)],
    )

    with HistoryTracker(db_path) as tracker:
        selected = select_files_to_send([str(source_dir)], '.xml', tracker)

    hashes = {HistoryTracker.hash_path(file) for file in selected}

    assert len(selected) == len(hashes)
    assert {file.name for file in selected} == {'retry.xml', 'fresh.xml'}


# --------------------------------------------------------------------
# D. The working-directory hazard
# --------------------------------------------------------------------


def test_running_from_another_directory_starts_an_empty_ledger(
    tmp_path,
    monkeypatch,
):
    """Test the documented behaviour when run with a different CWD.

    Every persistent path is relative, and nothing anchors them to the
    executable — which is why run_uploader.bat does `cd /d "C:\\SFTP"`.
    A launcher or scheduled task registered without a working directory
    therefore finds no ledger and re-sends everything.

    This pins current behaviour. If it is ever fixed, update this test
    deliberately rather than deleting it.
    """
    install_dir = tmp_path / 'sftp'
    source_dir = install_dir / 'source'
    source_dir.mkdir(parents=True)
    delivered = source_dir / 'delivered.xml'
    delivered.touch()
    _build_legacy_ledger(
        install_dir / DEFAULT_DB_PATH,
        _LEGACY_DDL_PRE_ORM,
        live_rows=[_live_row(delivered, sent=1)],
    )

    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv('HISTORY_DB_PATH', str(DEFAULT_DB_PATH))

    resolved = resolve_history_db_path()

    assert not resolved.is_absolute()

    with HistoryTracker(resolved) as tracker:
        summary = tracker.get_summary()
        selected = select_files_to_send([str(source_dir)], '.xml', tracker)

    assert summary['total'] == 0
    assert (elsewhere / DEFAULT_DB_PATH).is_file()
    assert [file.name for file in selected] == ['delivered.xml']


# --------------------------------------------------------------------
# E. Logs
# --------------------------------------------------------------------


def test_inherited_oversized_log_is_appended_not_truncated(
    tmp_path,
    upgrade_logger,
):
    """Test that an inherited log survives being opened.

    Asserted before any record is emitted: the handler opens in append
    mode, but the first emit rotates, so checking afterwards would be
    testing something else entirely.
    """
    log_dir = tmp_path / 'logs'
    oversized = MAX_LOG_SIZE + _OVERSIZED_PADDING
    name = str(uuid.uuid4())
    _write_log_file(log_dir / f'{name}.log', _ACTIVE_MARKER, oversized)
    size_before = (log_dir / f'{name}.log').stat().st_size

    upgrade_logger(name, log_dir=str(log_dir))

    contents = (log_dir / f'{name}.log').read_text(encoding='utf-8')

    assert (log_dir / f'{name}.log').stat().st_size >= size_before
    assert _ACTIVE_MARKER in contents


def test_first_record_after_upgrade_shifts_legacy_backups(
    tmp_path,
    upgrade_logger,
):
    """Test that inherited backups shift down rather than being lost.

    The current 1 MB rotation threshold is below what earlier releases
    used, so an inherited log rotates on the very first record. If that
    rollover discarded the inherited backups it would destroy the
    forensic record of whatever prompted the upgrade.
    """
    log_dir = tmp_path / 'logs'
    name = str(uuid.uuid4())
    _seed_legacy_logs(log_dir, name, MAX_LOG_SIZE + _OVERSIZED_PADDING)

    logger = upgrade_logger(name, log_dir=str(log_dir))
    logger.info('first record after the upgrade')

    for index in range(1, _LEGACY_BACKUPS + 1):
        shifted = log_dir / f'{name}.log.{index + 1}'
        assert shifted.is_file()
        assert _BACKUP_MARKER.format(index=index) in shifted.read_text(
            encoding='utf-8',
        )

    rotated_active = (log_dir / f'{name}.log.1').read_text(encoding='utf-8')
    current = (log_dir / f'{name}.log').read_text(encoding='utf-8')

    assert _ACTIVE_MARKER in rotated_active
    assert 'first record after the upgrade' in current


def test_retention_settles_at_the_new_backup_count(tmp_path, upgrade_logger):
    """Test that retention converges on the current cap after upgrade.

    Driven with a small max_bytes: rotating 60 times at the real 1 MB
    threshold would write ~60 MB per run and prove nothing extra, since
    MAX_LOG_SIZE itself is asserted in test_logger_setup.py.
    """
    log_dir = tmp_path / 'logs'
    name = str(uuid.uuid4())
    _seed_legacy_logs(log_dir, name, _SMALL_LOG_BYTES)

    logger = upgrade_logger(
        name,
        log_dir=str(log_dir),
        max_bytes=_TINY_MAX_BYTES,
        backup_count=BACKUP_COUNT,
    )
    for index in range(_ROLLOVER_RECORDS):
        logger.info(f'post upgrade record {index} ' + 'q' * 60)

    retained = list(log_dir.glob(f'{name}.log*'))

    assert len(retained) <= BACKUP_COUNT + 1


def test_small_inherited_log_is_left_alone(tmp_path, upgrade_logger):
    """Test that an under-threshold inherited log is not rotated."""
    log_dir = tmp_path / 'logs'
    name = str(uuid.uuid4())
    _seed_legacy_logs(log_dir, name, _SMALL_LOG_BYTES)

    logger = upgrade_logger(name, log_dir=str(log_dir))
    logger.info('first record after the upgrade')

    assert not (log_dir / f'{name}.log.{_LEGACY_BACKUPS + 1}').exists()
    for index in range(1, _LEGACY_BACKUPS + 1):
        backup = log_dir / f'{name}.log.{index}'
        assert _BACKUP_MARKER.format(index=index) in backup.read_text(
            encoding='utf-8',
        )


# --------------------------------------------------------------------
# F. Host key pins
# --------------------------------------------------------------------


def test_an_inherited_pin_file_is_read_not_reset(tmp_path):
    """Test a pin written by an earlier release is still honoured.

    If an upgrade could not read the inherited file it would fall back
    to trust-on-first-use and re-trust whatever answered the socket,
    which is precisely what pinning exists to prevent.
    """
    pins_path = _build_legacy_pin_file(tmp_path / 'data' / 'known_hosts.json')

    stored = get_pin(_LEGACY_PIN_HOST, _LEGACY_PIN_PORT, pins_path)

    assert stored is not None
    assert stored.key_type == _LEGACY_PIN_KEY_TYPE
    assert stored.fingerprint == _LEGACY_PIN_FINGERPRINT


def test_every_pin_field_exists_in_an_inherited_pin_file(tmp_path):
    """Test no HostPin field is missing from an inherited pin file.

    The structural drift guard, mirroring the ORM-column check: a field
    added to HostPin without a fallback would raise on a deployed site
    the first time its inherited file was read, while CI stayed green
    against one this code had just written itself.
    """
    pins_path = _build_legacy_pin_file(tmp_path / 'known_hosts.json')

    entry = _first_pin_entry(pins_path)
    pin_fields = {field.name for field in fields(HostPin)}

    assert pin_fields <= set(entry), (
        f'HostPin fields missing from an inherited pin file: '
        f'{sorted(pin_fields - set(entry))}. Reading one would fail on a '
        f'deployed site, or silently lose the field.'
    )


def test_an_inherited_pin_still_refuses_a_changed_key(tmp_path):
    """Test the inherited trust anchor still halts an unexpected key.

    The pin file is worthless if an upgrade keeps reading it but stops
    enforcing it.
    """
    pins_path = _build_legacy_pin_file(tmp_path / 'known_hosts.json')

    with pytest.raises(HostKeyMismatchError):
        verify_or_pin(
            _LEGACY_PIN_HOST,
            _LEGACY_PIN_PORT,
            _LEGACY_PIN_KEY_TYPE,
            'SHA256:a-key-this-site-has-never-seen',
            pins_path,
        )


def test_an_inherited_pin_is_not_rewritten_when_it_matches(tmp_path):
    """Test a matching key leaves the inherited file byte-identical.

    Rewriting on every connect would churn the file and discard the
    original pinned_at, the only record of when trust was established.
    """
    pins_path = _build_legacy_pin_file(tmp_path / 'known_hosts.json')
    before = pins_path.read_text(encoding='utf-8')

    newly_pinned = verify_or_pin(
        _LEGACY_PIN_HOST,
        _LEGACY_PIN_PORT,
        _LEGACY_PIN_KEY_TYPE,
        _LEGACY_PIN_FINGERPRINT,
        pins_path,
    )

    assert newly_pinned is False
    assert pins_path.read_text(encoding='utf-8') == before
