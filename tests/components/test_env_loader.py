import os
from unittest.mock import patch

import pytest

from sftp_file_transfer.components.env_loader import EnvLoader


def test_accessing_env_vars_returns_values_from_environment(monkeypatch):
    'Test that all 4 required env vars are accessible via EnvLoader instance.'
    monkeypatch.setenv('SFTP_HOST', 'sftp.example.com')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'testuser')
    monkeypatch.setenv('SFTP_PASSWORD', 'testpass123')

    env = EnvLoader()

    assert env.SFTP_HOST == 'sftp.example.com'
    assert env.SFTP_PORT == '22'
    assert env.SFTP_USER == 'testuser'
    assert env.SFTP_PASSWORD == 'testpass123'


def test_accessing_attribute_raises_when_required_var_missing(monkeypatch):
    'Test that accessing any attribute raises when a required var is missing.'
    monkeypatch.setenv('SFTP_HOST', 'sftp.example.com')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'testuser')
    monkeypatch.delenv('SFTP_PASSWORD', raising=False)

    patch_path = 'sftp_file_transfer.components.env_loader.load_dotenv'
    with patch(patch_path, return_value=False):
        env = EnvLoader()

    with pytest.raises(ValueError, match='SFTP_PASSWORD'):
        _ = env.SFTP_HOST


def test_raises_when_env_var_is_not_string(monkeypatch):
    'Test that accessing attribute raises when env var is not a string.'
    monkeypatch.setenv('SFTP_HOST', 'sftp.example.com')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'testuser')
    monkeypatch.setenv('SFTP_PASSWORD', 'testpass123')

    # Save the real os.getenv before patching
    real_getenv = os.getenv

    def fake_getenv(name, *args, **kwargs):
        if name == 'SFTP_PORT':
            return 12345  # Return integer instead of string
        return real_getenv(name, *args, **kwargs)

    patch_path = 'sftp_file_transfer.components.env_loader.os.getenv'
    with patch(patch_path, fake_getenv):
        env = EnvLoader()
        with pytest.raises(ValueError, match='must be a string'):
            _ = env.SFTP_HOST


def test_init_succeeds_when_load_dotenv_returns_false(monkeypatch):
    'Test that EnvLoader construction succeeds when no .env file is found.'
    monkeypatch.setenv('SFTP_HOST', 'sftp.example.com')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'testuser')
    monkeypatch.setenv('SFTP_PASSWORD', 'testpass123')

    patch_path = 'sftp_file_transfer.components.env_loader.load_dotenv'
    with patch(patch_path, return_value=False):
        env = EnvLoader()
        # Construction succeeds, no exception raised
        assert env is not None


def test_non_sftp_attributes_work_via_normal_lookup(monkeypatch):
    'Test that non-SFTP attributes are accessed normally.'
    monkeypatch.setenv('SFTP_HOST', 'sftp.example.com')
    monkeypatch.setenv('SFTP_PORT', '22')
    monkeypatch.setenv('SFTP_USER', 'testuser')
    monkeypatch.setenv('SFTP_PASSWORD', 'testpass123')

    env = EnvLoader()

    # These should work via normal Python attribute lookup, not env var lookup
    assert env.__class__.__name__ == 'EnvLoader'
    assert hasattr(env, '__dict__')
