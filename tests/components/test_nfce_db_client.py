from datetime import date
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy import Column, Date, Integer, MetaData, String, Table

from sftp_file_transfer.components.nfce_db_client import (
    DB_CONNECT_TIMEOUT_SECONDS,
    DB_READ_TIMEOUT_SECONDS,
    DB_WRITE_TIMEOUT_SECONDS,
    build_engine,
    fetch_pending_invoice_rows,
)


def test_build_engine_returns_pymysql_driver():
    """Test that build_engine creates an engine with pymysql driver."""
    expected_port = 3306
    engine = build_engine(
        host='localhost',
        port=expected_port,
        database='test_db',
        user='root',
        password='secret',
    )

    assert engine.url.drivername == 'mysql+pymysql'
    assert engine.url.host == 'localhost'
    assert engine.url.port == expected_port
    assert engine.url.database == 'test_db'
    assert engine.url.username == 'root'


def test_build_engine_url_format_with_custom_values():
    """Test that build_engine URL encodes host, port, and database."""
    engine = build_engine(
        host='192.168.1.1',
        port=3307,
        database='custom_db',
        user='admin',
        password='pass123',
    )

    url_str = str(engine.url)
    assert 'mysql+pymysql://admin@192.168.1.1:3307/custom_db' in url_str


def _create_fcr_invoice_data_table(engine):
    """Helper to create FCR_INVOICE_DATA table in SQLite."""
    metadata = MetaData()
    table = Table(
        'FCR_INVOICE_DATA',
        metadata,
        Column('ExtraField8', String, nullable=True),
        Column('ExtraField9', String, nullable=True),
        Column('FCRInvoiceDataID', Integer, primary_key=True),
        Column('InvoiceStatus', String, nullable=True),
        Column('FCRJSONEXP', String, nullable=True),
        Column('MicrosBsnzDate', Date, nullable=True),
    )
    metadata.create_all(engine)
    return table


def test_fetch_pending_invoice_rows_excludes_rows_before_since_date():
    """Test that rows with MicrosBsnzDate < since are excluded."""
    engine = sa.create_engine('sqlite:///:memory:')
    table = _create_fcr_invoice_data_table(engine)

    old_date = date(2025, 1, 1)
    since_date = date(2025, 1, 15)

    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {
                    'ExtraField8': 'chave1',
                    'ExtraField9': 'SP',
                    'FCRInvoiceDataID': 1,
                    'InvoiceStatus': 'pending',
                    'FCRJSONEXP': '{}',
                    'MicrosBsnzDate': old_date,
                },
            ],
        )

    rows = fetch_pending_invoice_rows(engine, since_date)

    assert rows == []


def test_fetch_pending_invoice_rows_includes_rows_on_since_date_boundary():
    """Test that MicrosBsnzDate == since is included (>= is inclusive)."""
    engine = sa.create_engine('sqlite:///:memory:')
    table = _create_fcr_invoice_data_table(engine)

    since_date = date(2025, 1, 15)

    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {
                    'ExtraField8': 'chave1',
                    'ExtraField9': 'SP',
                    'FCRInvoiceDataID': 1,
                    'InvoiceStatus': 'approved',
                    'FCRJSONEXP': '{"key": "value"}',
                    'MicrosBsnzDate': since_date,
                },
            ],
        )

    rows = fetch_pending_invoice_rows(engine, since_date)

    assert len(rows) == 1
    assert rows[0].chave == 'chave1'


def test_fetch_pending_invoice_rows_excludes_null_extra_field8():
    """Test that rows where ExtraField8 IS NULL are excluded."""
    engine = sa.create_engine('sqlite:///:memory:')
    table = _create_fcr_invoice_data_table(engine)

    since_date = date(2025, 1, 15)

    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {
                    'ExtraField8': None,
                    'ExtraField9': 'RJ',
                    'FCRInvoiceDataID': 1,
                    'InvoiceStatus': 'pending',
                    'FCRJSONEXP': '{}',
                    'MicrosBsnzDate': since_date,
                },
            ],
        )

    rows = fetch_pending_invoice_rows(engine, since_date)

    assert rows == []


def test_fetch_pending_invoice_rows_excludes_empty_string_extra_field8():
    """Test that rows where ExtraField8 is empty string are excluded."""
    engine = sa.create_engine('sqlite:///:memory:')
    table = _create_fcr_invoice_data_table(engine)

    since_date = date(2025, 1, 15)

    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {
                    'ExtraField8': '',
                    'ExtraField9': 'MG',
                    'FCRInvoiceDataID': 1,
                    'InvoiceStatus': 'approved',
                    'FCRJSONEXP': '{}',
                    'MicrosBsnzDate': since_date,
                },
            ],
        )

    rows = fetch_pending_invoice_rows(engine, since_date)

    assert rows == []


def test_fetch_pending_invoice_rows_returns_correct_fields_and_order():
    """Test that matching rows return chave, uf, invoice_id, status, json_exp
    in that order."""
    engine = sa.create_engine('sqlite:///:memory:')
    table = _create_fcr_invoice_data_table(engine)

    since_date = date(2025, 1, 15)

    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {
                    'ExtraField8': 'abc123def456',
                    'ExtraField9': 'BA',
                    'FCRInvoiceDataID': 42,
                    'InvoiceStatus': 'sent',
                    'FCRJSONEXP': '{"status": "emitida"}',
                    'MicrosBsnzDate': since_date,
                },
            ],
        )

    rows = fetch_pending_invoice_rows(engine, since_date)

    expected_invoice_id = 42
    assert len(rows) == 1
    row = rows[0]
    assert row.chave == 'abc123def456'
    assert row.uf == 'BA'
    assert row.invoice_id == expected_invoice_id
    assert row.status == 'sent'
    assert row.json_exp == '{"status": "emitida"}'


def test_fetch_pending_invoice_rows_returns_empty_list_when_no_matches():
    """Test that an empty table returns an empty list."""
    engine = sa.create_engine('sqlite:///:memory:')
    _create_fcr_invoice_data_table(engine)

    since_date = date(2025, 1, 15)
    rows = fetch_pending_invoice_rows(engine, since_date)

    assert rows == []


def test_build_engine_bounds_every_stage_of_a_db_call():
    """Test that connect/read/write timeouts are all passed to pymysql."""
    with patch(
        'sftp_file_transfer.components.nfce_db_client.sa.create_engine',
    ) as mock_create_engine:
        build_engine(
            host='localhost',
            port=3306,
            database='test_db',
            user='root',
            password='secret',
        )

    connect_args = mock_create_engine.call_args.kwargs['connect_args']

    assert connect_args['connect_timeout'] == DB_CONNECT_TIMEOUT_SECONDS
    assert connect_args['read_timeout'] == DB_READ_TIMEOUT_SECONDS
    assert connect_args['write_timeout'] == DB_WRITE_TIMEOUT_SECONDS
    assert connect_args['password'] == 'secret'


def test_build_engine_pre_pings_pooled_connections():
    """Test that a connection dropped while pooled is replaced, not reused."""
    engine = build_engine(
        host='localhost',
        port=3306,
        database='test_db',
        user='root',
        password='secret',
    )

    assert engine.pool._pre_ping is True
