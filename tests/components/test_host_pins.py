import string
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from paramiko import RSAKey, Transport

from sftp_file_transfer.components.host_pins import (
    DEFAULT_PINS_PATH,
    HostKeyMismatchError,
    HostPin,
    HostPinStoreError,
    format_fingerprint,
    get_pin,
    load_pins,
    preferred_key_algorithms,
    resolve_host_pins_path,
    save_pin,
    verify_or_pin,
)

_BASE64_ALPHABET = set(string.ascii_letters + string.digits + '+/')
_SHA256_B64_LENGTH = 43


def test_format_fingerprint_matches_paramiko_pkey_fingerprint():
    """Test our fingerprint equals paramiko's, i.e. ssh-keygen -lf."""
    key = RSAKey.generate(2048)

    assert format_fingerprint(key.asbytes()) == key.fingerprint


@given(key_bytes=st.binary())
def test_format_fingerprint_shape_is_stable(key_bytes):
    """Test every fingerprint is an unpadded SHA256 base64 string."""
    fingerprint = format_fingerprint(key_bytes)

    assert fingerprint.startswith('SHA256:')
    encoded = fingerprint.removeprefix('SHA256:')
    assert len(encoded) == _SHA256_B64_LENGTH
    assert set(encoded) <= _BASE64_ALPHABET


@given(key_bytes=st.binary())
def test_format_fingerprint_is_deterministic(key_bytes):
    """Test the same key blob always yields the same fingerprint."""
    assert format_fingerprint(key_bytes) == format_fingerprint(key_bytes)


@given(first=st.binary(), second=st.binary())
def test_distinct_keys_get_distinct_fingerprints(first, second):
    """Test two different key blobs never share a fingerprint."""
    assume(first != second)

    assert format_fingerprint(first) != format_fingerprint(second)


def test_preferred_key_algorithms_widens_an_rsa_pin():
    """Test an ssh-rsa pin still offers the SHA-2 RSA algorithms.

    Pinning literally ssh-rsa would force SHA-1 host key signatures,
    which OpenSSH 8.8 and newer refuse outright.
    """
    assert preferred_key_algorithms('ssh-rsa') == (
        'rsa-sha2-512',
        'rsa-sha2-256',
        'ssh-rsa',
    )


@pytest.mark.parametrize(
    'key_type',
    ['ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-dss'],
)
def test_preferred_key_algorithms_passes_other_types_through(key_type):
    """Test a non-RSA pin offers exactly the pinned algorithm."""
    assert preferred_key_algorithms(key_type) == (key_type,)


@pytest.mark.parametrize(
    'key_type',
    [
        'ssh-rsa',
        'rsa-sha2-256',
        'rsa-sha2-512',
        'ssh-ed25519',
        'ecdsa-sha2-nistp256',
        'ssh-dss',
    ],
)
def test_preferred_key_algorithms_are_accepted_by_paramiko(key_type):
    """Test every returned algorithm name is one paramiko will offer."""
    options = Transport(MagicMock()).get_security_options()

    options.key_types = preferred_key_algorithms(key_type)

    assert set(options.key_types) >= {key_type}


def _pin(fingerprint: str = 'SHA256:abc', key_type: str = 'ssh-ed25519'):
    """Build a HostPin for tests."""
    return HostPin(
        key_type=key_type,
        fingerprint=fingerprint,
        pinned_at='2026-09-22T14:03:11',
    )


def test_load_pins_returns_empty_when_no_file_exists(tmp_path):
    """Test a missing pin file is a legitimate first run, not an error."""
    assert load_pins(tmp_path / 'known_hosts.json') == {}


def test_save_then_load_round_trips_a_pin(tmp_path):
    """Test a saved pin is read back with every field intact."""
    pins_path = tmp_path / 'known_hosts.json'

    save_pin('sftp.example.com', 22, _pin(), pins_path)
    reloaded = get_pin('sftp.example.com', 22, pins_path)

    assert reloaded == _pin()


def test_save_pin_creates_parent_directory(tmp_path):
    """Test save_pin creates the directory if it doesn't exist yet."""
    pins_path = tmp_path / 'nested' / 'known_hosts.json'

    save_pin('sftp.example.com', 22, _pin(), pins_path)

    assert pins_path.exists()


def test_load_pins_raises_on_corrupt_file(tmp_path):
    """Test a corrupt pin file fails closed instead of returning {}.

    Silently treating an unreadable store as "no pins" would re-trust
    whatever answers the socket and hand over the password.
    """
    pins_path = tmp_path / 'known_hosts.json'
    pins_path.write_text('not valid json', encoding='utf-8')

    with pytest.raises(HostPinStoreError):
        load_pins(pins_path)


def test_load_pins_raises_when_the_file_is_not_an_object(tmp_path):
    """Test valid JSON of the wrong shape still fails closed."""
    pins_path = tmp_path / 'known_hosts.json'
    pins_path.write_text('["sftp.example.com:22"]', encoding='utf-8')

    with pytest.raises(HostPinStoreError):
        load_pins(pins_path)


def test_load_pins_raises_when_an_entry_is_not_an_object(tmp_path):
    """Test an entry of the wrong shape fails closed."""
    pins_path = tmp_path / 'known_hosts.json'
    pins_path.write_text(
        '{"sftp.example.com:22": "SHA256:abc"}',
        encoding='utf-8',
    )

    with pytest.raises(HostPinStoreError):
        load_pins(pins_path)


def test_load_pins_raises_when_an_entry_is_missing_a_field(tmp_path):
    """Test a partially-written entry fails closed."""
    pins_path = tmp_path / 'known_hosts.json'
    pins_path.write_text(
        '{"sftp.example.com:22": {"key_type": "ssh-ed25519"}}',
        encoding='utf-8',
    )

    with pytest.raises(HostPinStoreError):
        load_pins(pins_path)


def test_get_pin_returns_none_for_an_unknown_endpoint(tmp_path):
    """Test an endpoint with no stored pin reads back as None."""
    pins_path = tmp_path / 'known_hosts.json'
    save_pin('sftp.example.com', 22, _pin(), pins_path)

    assert get_pin('other.example.com', 22, pins_path) is None


def test_save_pin_preserves_other_hosts(tmp_path):
    """Test pinning a second host leaves the first one intact."""
    pins_path = tmp_path / 'known_hosts.json'
    first = _pin(fingerprint='SHA256:first')
    second = _pin(fingerprint='SHA256:second')

    save_pin('a.example.com', 22, first, pins_path)
    save_pin('b.example.com', 22, second, pins_path)

    assert get_pin('a.example.com', 22, pins_path) == first
    assert get_pin('b.example.com', 22, pins_path) == second


def test_pins_for_different_ports_are_independent(tmp_path):
    """Test the same host on two ports keeps two separate pins."""
    pins_path = tmp_path / 'known_hosts.json'
    default_port = _pin(fingerprint='SHA256:default')
    alt_port = _pin(fingerprint='SHA256:alt')

    save_pin('sftp.example.com', 22, default_port, pins_path)
    save_pin('sftp.example.com', 2222, alt_port, pins_path)

    assert get_pin('sftp.example.com', 22, pins_path) == default_port
    assert get_pin('sftp.example.com', 2222, pins_path) == alt_port


def test_resolve_host_pins_path_uses_the_env_var(tmp_path, monkeypatch):
    """Test SFTP_HOST_PINS_PATH overrides the default location."""
    custom = tmp_path / 'custom' / 'pins.json'
    monkeypatch.setenv('SFTP_HOST_PINS_PATH', str(custom))

    with patch('sftp_file_transfer.components.host_pins.load_dotenv'):
        assert resolve_host_pins_path() == custom


def test_resolve_host_pins_path_falls_back_to_the_default(monkeypatch):
    """Test the default path is used when the env var isn't set."""
    monkeypatch.delenv('SFTP_HOST_PINS_PATH', raising=False)

    with patch('sftp_file_transfer.components.host_pins.load_dotenv'):
        assert resolve_host_pins_path() == DEFAULT_PINS_PATH


def test_verify_or_pin_records_the_key_on_first_use(tmp_path):
    """Test an unknown endpoint is pinned and reported as newly trusted."""
    pins_path = tmp_path / 'known_hosts.json'

    pinned = verify_or_pin(
        'sftp.example.com',
        22,
        'ssh-ed25519',
        'SHA256:abc',
        pins_path,
    )

    assert pinned is True
    stored = get_pin('sftp.example.com', 22, pins_path)
    assert stored.key_type == 'ssh-ed25519'
    assert stored.fingerprint == 'SHA256:abc'


def test_verify_or_pin_accepts_a_matching_pin(tmp_path):
    """Test a known, matching key is accepted without rewriting it."""
    pins_path = tmp_path / 'known_hosts.json'
    save_pin('sftp.example.com', 22, _pin(), pins_path)
    before = pins_path.read_text(encoding='utf-8')

    pinned = verify_or_pin(
        'sftp.example.com',
        22,
        'ssh-ed25519',
        'SHA256:abc',
        pins_path,
    )

    assert pinned is False
    assert pins_path.read_text(encoding='utf-8') == before


def test_verify_or_pin_raises_on_a_fingerprint_mismatch(tmp_path):
    """Test a changed fingerprint is rejected, naming both values."""
    pins_path = tmp_path / 'known_hosts.json'
    save_pin('sftp.example.com', 22, _pin(), pins_path)

    with pytest.raises(HostKeyMismatchError) as excinfo:
        verify_or_pin(
            'sftp.example.com',
            22,
            'ssh-ed25519',
            'SHA256:different',
            pins_path,
        )

    message = str(excinfo.value)
    assert 'sftp.example.com:22' in message
    assert 'SHA256:abc' in message
    assert 'SHA256:different' in message


def test_verify_or_pin_raises_on_a_key_type_mismatch(tmp_path):
    """Test a same-fingerprint key of another type is still rejected.

    Otherwise a server could sidestep the pin by offering a key of a
    type we have no pin for.
    """
    pins_path = tmp_path / 'known_hosts.json'
    save_pin('sftp.example.com', 22, _pin(), pins_path)

    with pytest.raises(HostKeyMismatchError):
        verify_or_pin(
            'sftp.example.com',
            22,
            'ssh-rsa',
            'SHA256:abc',
            pins_path,
        )


def test_verify_or_pin_leaves_the_stored_pin_after_a_mismatch(tmp_path):
    """Test a rejected key never overwrites the trusted one."""
    pins_path = tmp_path / 'known_hosts.json'
    save_pin('sftp.example.com', 22, _pin(), pins_path)

    with pytest.raises(HostKeyMismatchError):
        verify_or_pin(
            'sftp.example.com',
            22,
            'ssh-ed25519',
            'SHA256:evil',
            pins_path,
        )

    assert get_pin('sftp.example.com', 22, pins_path) == _pin()
