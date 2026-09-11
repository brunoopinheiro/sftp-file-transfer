import pytest
from sftp_file_transfer.components.nfce_config import NfceConfig


def test_reads_all_required_env_vars_successfully(monkeypatch):
    """Test that all required environment variables are read correctly."""
    monkeypatch.setenv('NFCE_DB_HOST', 'localhost')
    monkeypatch.setenv('NFCE_DB_PORT', '5432')
    monkeypatch.setenv('NFCE_DB_NAME', 'nfce_db')
    monkeypatch.setenv('NFCE_DB_USER', 'admin')
    monkeypatch.setenv('NFCE_DB_PASSWORD', 'secret123')
    monkeypatch.setenv('NFCE_OUTPUT_PATH', '/output/files')

    config = NfceConfig()

    assert config.nfce_db_host == 'localhost'
    assert config.nfce_db_port == '5432'
    assert config.nfce_db_name == 'nfce_db'
    assert config.nfce_db_user == 'admin'
    assert config.nfce_db_password == 'secret123'
    assert config.nfce_output_path == '/output/files'


def test_raises_value_error_when_required_var_missing(monkeypatch):
    """Test that ValueError is raised when a required env var is missing."""
    monkeypatch.setenv('NFCE_DB_HOST', 'localhost')
    monkeypatch.setenv('NFCE_DB_PORT', '5432')
    monkeypatch.setenv('NFCE_DB_NAME', 'nfce_db')
    monkeypatch.setenv('NFCE_DB_USER', 'admin')
    monkeypatch.setenv('NFCE_DB_PASSWORD', 'secret123')
    monkeypatch.delenv('NFCE_OUTPUT_PATH', raising=False)

    with pytest.raises(ValueError, match='NFCE_OUTPUT_PATH'):
        NfceConfig()


def test_lookback_days_defaults_to_five(monkeypatch):
    """Test that NFCE_LOOKBACK_DAYS defaults to 5 when not set."""
    monkeypatch.setenv('NFCE_DB_HOST', 'localhost')
    monkeypatch.setenv('NFCE_DB_PORT', '5432')
    monkeypatch.setenv('NFCE_DB_NAME', 'nfce_db')
    monkeypatch.setenv('NFCE_DB_USER', 'admin')
    monkeypatch.setenv('NFCE_DB_PASSWORD', 'secret123')
    monkeypatch.setenv('NFCE_OUTPUT_PATH', '/output/files')
    monkeypatch.delenv('NFCE_LOOKBACK_DAYS', raising=False)
    default_lookback_days = 5

    config = NfceConfig()

    assert config.nfce_lookback_days == default_lookback_days
    assert isinstance(config.nfce_lookback_days, int)


def test_lookback_days_parsed_as_int_when_set(monkeypatch):
    """Test that NFCE_LOOKBACK_DAYS is parsed as int when explicitly set."""
    monkeypatch.setenv('NFCE_DB_HOST', 'localhost')
    monkeypatch.setenv('NFCE_DB_PORT', '5432')
    monkeypatch.setenv('NFCE_DB_NAME', 'nfce_db')
    monkeypatch.setenv('NFCE_DB_USER', 'admin')
    monkeypatch.setenv('NFCE_DB_PASSWORD', 'secret123')
    monkeypatch.setenv('NFCE_OUTPUT_PATH', '/output/files')
    monkeypatch.setenv('NFCE_LOOKBACK_DAYS', '10')
    expected_lookback_days = 10

    config = NfceConfig()

    assert config.nfce_lookback_days == expected_lookback_days
    assert isinstance(config.nfce_lookback_days, int)
