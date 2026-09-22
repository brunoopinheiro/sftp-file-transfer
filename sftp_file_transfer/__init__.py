"""Automated NFC-e fiscal-XML generation and SFTP delivery."""

# Kept as a literal rather than read via importlib.metadata: a
# PyInstaller one-file exe carries no .dist-info to read, so metadata
# lookup would report the developer's editable install or raise.
# tests/test_version.py pins this against pyproject.toml.
__version__ = '1.1.0'
