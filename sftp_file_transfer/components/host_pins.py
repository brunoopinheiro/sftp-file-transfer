import base64
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from dotenv import find_dotenv, load_dotenv

DEFAULT_PINS_PATH = Path('data') / 'known_hosts.json'

# paramiko reports every RSA host key as 'ssh-rsa' regardless of the
# signature algorithm actually negotiated, so a pin records that name
# even when the server signed with SHA-2. Offering only 'ssh-rsa' back
# would force SHA-1 signatures, which OpenSSH 8.8+ refuses -- so an RSA
# pin has to offer the whole family. Mirrors Transport.connect().
_RSA_KEY_ALGORITHMS = ('rsa-sha2-512', 'rsa-sha2-256', 'ssh-rsa')


class HostPinError(Exception):
    """Base error for the SFTP host key pin store."""


class HostKeyMismatchError(HostPinError):
    """Raised when a server's host key differs from the stored pin.

    Deliberately not an SSHException: the tenacity retry predicates on
    SFTPManager.upload_file/download_file retry SSHException, and a
    mismatch must never be retried against a possibly hostile server.
    """


class HostPinStoreError(HostPinError):
    """Raised when the pin file exists but cannot be trusted."""


@dataclass(frozen=True)
class HostPin:
    """A trusted host key recorded for one host:port endpoint."""

    key_type: str
    fingerprint: str
    pinned_at: str


def resolve_host_pins_path() -> Path:
    """Resolve the host key pin file path from the project .env file.

    Every entry point should call this instead of reading
    SFTP_HOST_PINS_PATH directly, so they all agree on the same pin
    file regardless of each process's own working directory.

    Returns:
        Path: SFTP_HOST_PINS_PATH from the environment/.env file, or
            DEFAULT_PINS_PATH if it isn't set.
    """
    load_dotenv(find_dotenv())
    return Path(os.getenv('SFTP_HOST_PINS_PATH', DEFAULT_PINS_PATH))


def format_fingerprint(key_bytes: bytes) -> str:
    """Format an SSH public key blob as an ssh-keygen SHA256 fingerprint."""
    digest = hashlib.sha256(key_bytes).digest()
    encoded = base64.b64encode(digest).decode('ascii').rstrip('=')
    return f'SHA256:{encoded}'


def preferred_key_algorithms(key_type: str) -> Tuple[str, ...]:
    """Map a pinned host key type to the algorithms to offer for it."""
    if key_type in _RSA_KEY_ALGORITHMS:
        return _RSA_KEY_ALGORITHMS
    return (key_type,)


def endpoint_key(host: str, port: int) -> str:
    """Build the pin-store key identifying one host and port."""
    return f'{host}:{port}'


def load_pins(pins_path: Union[str, Path]) -> Dict[str, HostPin]:
    """Read every stored host key pin, failing closed if unreadable.

    Unlike MonitorConfig.load, a corrupt file is an error rather than a
    silent fallback: treating an unreadable store as "no pins" would
    re-trust whatever answers the socket and hand over the password.

    Args:
        pins_path: Path to the pin file.

    Raises:
        HostPinStoreError: If the file exists but cannot be parsed, or
            holds an entry that isn't a complete pin.

    Returns:
        Dict[str, HostPin]: Pins by 'host:port', empty if no file yet.
    """
    path = Path(pins_path)
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError) as exc:
        raise HostPinStoreError(
            f'Host key pin file {path} is unreadable: {exc}. Delete it to '
            f'trust the server again on the next connection.',
        ) from exc

    if not isinstance(raw, dict):
        raise HostPinStoreError(
            f'Host key pin file {path} does not hold a JSON object.',
        )

    pins = {}
    for endpoint, entry in raw.items():
        if not isinstance(entry, dict):
            raise HostPinStoreError(
                f'Host key pin for {endpoint} in {path} is not an object.',
            )
        try:
            pins[endpoint] = HostPin(
                key_type=entry['key_type'],
                fingerprint=entry['fingerprint'],
                pinned_at=entry.get('pinned_at', ''),
            )
        except KeyError as exc:
            raise HostPinStoreError(
                f'Host key pin for {endpoint} in {path} is missing {exc}.',
            ) from exc
    return pins


def get_pin(
    host: str,
    port: int,
    pins_path: Union[str, Path],
) -> Optional[HostPin]:
    """Read the stored pin for one endpoint, or None if never pinned."""
    return load_pins(pins_path).get(endpoint_key(host, port))


def save_pin(
    host: str,
    port: int,
    pin: HostPin,
    pins_path: Union[str, Path],
) -> None:
    """Record a trusted host key, preserving pins for other endpoints.

    The file is written to a temporary sibling and moved into place with
    os.replace, which is atomic on both NTFS and POSIX, so a concurrent
    reader never observes a half-written store. Pins are re-read
    immediately before the merge to keep the lost-update window as
    small as possible; no lock file is used, because a stale lock left
    by a killed daemon would break every subsequent connection, and the
    only realistic concurrent writers (the monitor daemon's probe and a
    scheduled cycle) are pinning the same endpoint to the same value.

    Args:
        host: SFTP hostname the pin belongs to.
        port: SFTP port the pin belongs to.
        pin: The host key to record as trusted.
        pins_path: Path to the pin file.

    Returns:
        None.
    """
    path = Path(pins_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    pins = load_pins(path)
    pins[endpoint_key(host, port)] = pin
    payload = {endpoint: asdict(stored) for endpoint, stored in pins.items()}

    tmp_path = path.with_suffix(f'{path.suffix}.{os.getpid()}.tmp')
    tmp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    os.replace(tmp_path, path)


def verify_or_pin(
    host: str,
    port: int,
    key_type: str,
    fingerprint: str,
    pins_path: Union[str, Path],
) -> bool:
    """Check a server's host key against the pin store, or pin it.

    Args:
        host: SFTP hostname being connected to.
        port: SFTP port being connected to.
        key_type: The server's host key type, e.g. 'ssh-ed25519'.
        fingerprint: The server's key as a SHA256 fingerprint.
        pins_path: Path to the pin file.

    Raises:
        HostKeyMismatchError: If a pin exists and the key differs from
            it, in either type or fingerprint.
        HostPinStoreError: If the pin file exists but is unusable.

    Returns:
        bool: True if the key was newly pinned (first use), False if it
            matched an existing pin.
    """
    endpoint = endpoint_key(host, port)
    pin = load_pins(pins_path).get(endpoint)

    if pin is None:
        save_pin(
            host,
            port,
            HostPin(
                key_type=key_type,
                fingerprint=fingerprint,
                pinned_at=datetime.now().isoformat(timespec='seconds'),
            ),
            pins_path,
        )
        return True

    if pin.key_type == key_type and pin.fingerprint == fingerprint:
        return False

    raise HostKeyMismatchError(
        f'HOST KEY MISMATCH for {endpoint}: expected {pin.key_type} '
        f'{pin.fingerprint}, got {key_type} {fingerprint}. The server may '
        f'have been rebuilt, or the connection may be intercepted. If this '
        f'change is expected, run: sftp_history trust-host {host} '
        f'--port {port}',
    )
