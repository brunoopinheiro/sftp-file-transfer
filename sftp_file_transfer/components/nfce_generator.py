"""NFCe extraction generator module.

This module orchestrates the NFCe extraction pipeline, coordinating between
database fetching, JSON parsing, XML building, and file writing.
"""

import base64
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Union

import sqlalchemy as sa

from sftp_file_transfer.components.nfce_db_client import (
    fetch_pending_invoice_rows,
)
from sftp_file_transfer.components.nfce_file_writer import (
    already_exists,
    apply_document_datetime,
    build_output_paths,
    extract_document_datetime,
    write_document,
)
from sftp_file_transfer.components.nfce_json_parser import (
    extract_invoice_content,
)
from sftp_file_transfer.components.nfce_xml_builder import classify_and_build


@dataclass
class NfceExtractionSummary:
    """Summary of NFCe extraction results.

    Attributes:
        total: Total number of rows processed.
        generated: Number of authorized_with_protocol documents generated.
        cancellations: Number of cancellation documents generated.
        no_protocol: Number of authorized_no_protocol documents generated.
        skipped: Number of rows skipped because output already exists.
        errors: Number of rows that failed processing.
    """

    total: int = 0
    generated: int = 0
    cancellations: int = 0
    no_protocol: int = 0
    skipped: int = 0
    errors: int = 0


def run_nfce_extraction(
    engine: sa.engine.Engine,
    output_dir: Union[str, Path],
    lookback_days: int,
) -> NfceExtractionSummary:
    """Run NFCe extraction pipeline.

    Fetches pending invoice rows since the lookback date, extracts XML
    content from JSON, classifies documents, and writes output files.

    Args:
        engine: SQLAlchemy engine for database access.
        output_dir: Directory to write output files to.
        lookback_days: Number of days to look back for pending invoices.

    Returns:
        NfceExtractionSummary with extraction results.
    """
    summary = NfceExtractionSummary()

    # Compute since date
    since = date.today() - timedelta(days=lookback_days)

    # Fetch pending rows
    rows = fetch_pending_invoice_rows(engine, since)
    summary.total = len(rows)

    # Process each row
    for row in rows:
        # Build output paths
        paths = build_output_paths(output_dir, row.chave)

        # Check if output already exists
        if already_exists(paths):
            summary.skipped += 1
            continue

        # Wrap rest of processing in try/except
        try:
            # Extract base64 content from Request and Response
            request_b64 = extract_invoice_content(row.json_exp, 'Request')
            response_b64 = extract_invoice_content(row.json_exp, 'Response')

            # Decode base64 to UTF-8 text (or use empty string if falsy)
            request_xml = (
                base64.b64decode(request_b64).decode('utf-8')
                if request_b64
                else ''
            )
            response_xml = (
                base64.b64decode(response_b64).decode('utf-8')
                if response_b64
                else ''
            )

            # Classify and build document
            result = classify_and_build(request_xml, response_xml)

            # Handle based on document kind
            if result.kind == 'authorized_with_protocol':
                write_document(paths.authorized, result.content)
                summary.generated += 1
                path_to_update = paths.authorized
            elif result.kind == 'authorized_no_protocol':
                write_document(paths.no_protocol, result.content)
                summary.no_protocol += 1
                path_to_update = paths.no_protocol
            elif result.kind in {
                'cancellation_with_return',
                'cancellation_no_return',
            }:
                write_document(paths.cancellation, result.content)
                summary.cancellations += 1
                path_to_update = paths.cancellation
            elif result.kind == 'unrecognized':
                summary.errors += 1
                path_to_update = None
            else:
                summary.errors += 1
                path_to_update = None

            # Apply document datetime if appropriate
            if path_to_update is not None and result.date_source_fragment:
                dt = extract_document_datetime(result.date_source_fragment)
                if dt is not None:
                    apply_document_datetime(path_to_update, dt)

        except Exception:
            summary.errors += 1
            continue

    return summary
