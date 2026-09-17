import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Union


@dataclass
class NfceOutputPaths:
    """Container for NFe output file paths."""

    authorized: Path
    cancellation: Path
    no_protocol: Path


def build_output_paths(
    output_dir: Union[str, Path],
    chave: str,
) -> NfceOutputPaths:
    """Build output paths for NFe files.

    Args:
        output_dir (Union[str, Path]): The output directory. Accepts a
            plain string since it commonly comes straight from an
            environment variable (e.g. NFCE_OUTPUT_PATH).
        chave (str): The NFe chave (key).

    Returns:
        NfceOutputPaths: Container with the three output paths.

    Examples:
        Build output paths for a given chave:

        >>> paths = build_output_paths(Path('output'), '12345')
        >>> paths.authorized.name
        'NFe12345.xml'
        >>> paths.cancellation.name
        'NFe12345_cancelamento.xml'
        >>> paths.no_protocol.name
        'NFe12345_semprotocolo.xml'
    """
    output_dir = Path(output_dir)
    return NfceOutputPaths(
        authorized=output_dir / f'NFe{chave}.xml',
        cancellation=output_dir / f'NFe{chave}_cancelamento.xml',
        no_protocol=output_dir / f'NFe{chave}_semprotocolo.xml',
    )


def already_exists(paths: NfceOutputPaths) -> bool:
    """Check if any of the NFe output files already exist.

    Args:
        paths (NfceOutputPaths): Container with the output paths.

    Returns:
        bool: True if any of the files exist, False otherwise.
    """
    return (
        paths.authorized.exists()
        or paths.cancellation.exists()
        or paths.no_protocol.exists()
    )


def write_document(path: Path, content: str) -> None:
    """Write content to a file as UTF-8 text without BOM.

    Args:
        path (Path): The file path to write to.
        content (str): The content to write.
    """
    path.write_text(content, encoding='utf-8')


def extract_document_datetime(xml_fragment: str) -> datetime | None:
    """Extract a datetime from an XML fragment.

    Looks for <dhEmi>...</dhEmi> tag first, then falls back to
    <dhEvento>...</dhEvento>. Returns None if neither tag is found
    or if the content is not a valid ISO-8601 datetime.

    Args:
        xml_fragment (str): XML fragment to search.

    Returns:
        datetime | None: Parsed datetime or None if not found/invalid.

    Examples:
        Extract datetime from a dhEmi tag:

        >>> extract_document_datetime(
        ...     '<dhEmi>2024-09-11T10:30:45</dhEmi>'
        ... )
        datetime.datetime(2024, 9, 11, 10, 30, 45)

        Return None when neither dhEmi nor dhEvento tag is present:

        >>> extract_document_datetime(
        ...     '<other>2024-09-11T10:30:45</other>'
        ... )
    """
    # Try to find dhEmi tag first
    match = re.search(r'<dhEmi>([^<]+)</dhEmi>', xml_fragment)
    if not match:
        # Fall back to dhEvento
        match = re.search(r'<dhEvento>([^<]+)</dhEvento>', xml_fragment)

    if not match:
        return None

    datetime_str = match.group(1)
    try:
        return datetime.fromisoformat(datetime_str)
    except ValueError:
        return None


def apply_document_datetime(path: Path, when: datetime) -> None:
    """Set the modified time of a file.

    Args:
        path (Path): The file path.
        when (datetime): The datetime to set as mtime and atime.
    """
    timestamp = when.timestamp()
    os.utime(path, (timestamp, timestamp))
