from dataclasses import dataclass
from datetime import date
from typing import List

import sqlalchemy as sa
from sqlalchemy import Column, Date, Integer, MetaData, String, Table, select


@dataclass
class NfceInvoiceRow:
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
    """Create a MySQL engine using pymysql driver."""
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
    """Fetch invoice rows since date where ExtraField8 is not null/empty."""
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
