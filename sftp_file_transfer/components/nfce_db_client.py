from dataclasses import dataclass
from datetime import date
from typing import List

import sqlalchemy as sa
from sqlalchemy import Column, Date, Integer, MetaData, String, Table, select


@dataclass
class NfceInvoiceRow:
    """Represents a row from the FCR_INVOICE_DATA table.

    Attributes:
        chave: NFe access key (ExtraField8).
        uf: State code (ExtraField9).
        invoice_id: Unique invoice identifier (FCRInvoiceDataID).
        status: Invoice status (InvoiceStatus).
        json_exp: Raw FCRJSONEXP blob containing encoded request and
            response data.
    """

    chave: str
    uf: str
    invoice_id: int
    status: str
    json_exp: str


def build_engine(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
) -> sa.engine.Engine:
    """Create a MySQL engine using pymysql driver.

    Constructs a SQLAlchemy Engine configured with the pymysql driver for
    MySQL database connections. The password is passed via connect_args to
    keep it out of the engine's repr/logs.

    Args:
        host: Database host address.
        port: Database port number.
        database: Database name.
        user: Database user.
        password: Database password.

    Returns:
        SQLAlchemy Engine configured with pymysql driver.
    """
    url = sa.engine.URL.create(
        drivername='mysql+pymysql',
        username=user,
        host=host,
        port=port,
        database=database,
    )
    engine = sa.create_engine(
        url,
        connect_args={'password': password},
    )
    return engine


metadata = MetaData()

_FCR_INVOICE_DATA = Table(
    'FCR_INVOICE_DATA',
    metadata,
    Column('ExtraField8', String, nullable=True),
    Column('ExtraField9', String, nullable=True),
    Column('FCRInvoiceDataID', Integer, primary_key=True),
    Column('InvoiceStatus', String, nullable=True),
    Column('FCRJSONEXP', String, nullable=True),
    Column('MicrosBsnzDate', Date, nullable=True),
)


def fetch_pending_invoice_rows(
    engine: sa.engine.Engine,
    since: date,
) -> List[NfceInvoiceRow]:
    """Fetch invoice rows where date >= since and chave is not empty.

    Queries the FCR_INVOICE_DATA table for invoice rows with a business
    date on or after the specified date and a non-null, non-empty NFe
    access key (ExtraField8).

    Args:
        engine: SQLAlchemy Engine for database access.
        since: Minimum MicrosBsnzDate to filter rows.

    Returns:
        List of NfceInvoiceRow objects matching the criteria.
    """
    stmt = select(_FCR_INVOICE_DATA).where(
        (_FCR_INVOICE_DATA.c.MicrosBsnzDate >= since)
        & (_FCR_INVOICE_DATA.c.ExtraField8.isnot(None))
        & (_FCR_INVOICE_DATA.c.ExtraField8 != '')  # noqa: PLC1901
    )

    rows = []
    with engine.connect() as conn:
        result = conn.execute(stmt)
        for row in result:
            rows.append(
                NfceInvoiceRow(
                    chave=row.ExtraField8,
                    uf=row.ExtraField9,
                    invoice_id=row.FCRInvoiceDataID,
                    status=row.InvoiceStatus,
                    json_exp=row.FCRJSONEXP,
                )
            )
    return rows
