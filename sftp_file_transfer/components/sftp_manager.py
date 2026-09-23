import logging
import socket
from logging import Logger
from pathlib import Path
from types import TracebackType
from typing import List, Optional, Type, TypedDict

from paramiko import (
    RSAKey,
    SFTPAttributes,
    SFTPClient,
    SSHException,
    Transport,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    retry_if_not_result,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

from sftp_file_transfer.components.host_pins import (
    HostKeyMismatchError,
    format_fingerprint,
    get_pin,
    preferred_key_algorithms,
    resolve_host_pins_path,
    verify_or_pin,
)
from sftp_file_transfer.components.logger_setup import setup_logger

logger: Logger = setup_logger()
CLIENT_NOT_CONNECTED = 'SFTP client is not connected.'

# Bound the TCP connect so an unreachable host fails fast instead of
# waiting out the platform default.
CONNECT_TIMEOUT_SECONDS = 15
# Paramiko never sends keepalives unless asked. Without them a
# connection killed silently (firewall/NAT idle reaping, VPN flap) is
# never detected, and the transport's reader thread waits forever.
KEEPALIVE_INTERVAL_SECONDS = 30
# Channel.timeout defaults to None, so an SFTP request whose response
# never arrives blocks indefinitely. This caps how long any single
# operation may sit with no data at all moving.
CHANNEL_TIMEOUT_SECONDS = 120


class SFTPManagerConfig(TypedDict):
    """Configuration for the SFTP manager."""

    sftp_host: str
    sftp_port: int
    sftp_user: str
    sftp_password: str
    key_filepath: Optional[Path]
    key_password: Optional[str]


class SFTPManager:
    """Manage SFTP operations using Paramiko.

    This class handles SFTP connections and file transfers using the
    Paramiko library. It requires environment variables for connection
    parameters. This class is designed to be used as a context manager, as
    it automatically handles connection setup and teardown.

    Attributes:
        env_loader (EnvLoader): An instance of EnvLoader to access environment
            variables.
        file_manager (FileManager): An instance of FileManager to manage files.

    Parameters:
        target (SFTPManagerConfig): A dictionary containing SFTP connection
            parameters including host, port, user, password, key file path,
            and key password.
    """

    @classmethod
    def check_args(
        cls,
        sftp_host: str,
        sftp_port: int,
        sftp_user: str,
        sftp_password: str,
    ) -> None:
        """Check if the provided arguments are valid.

        Args:
            sftp_host (str): The SFTP server hostname.
            sftp_port (int): The SFTP server port number.
            sftp_user (str): The SFTP user name.
            sftp_password (str): The SFTP password.

        Raises:
            ValueError: If any parameter is not of the expected type.

        Returns:
            None.
        """
        if not all([
            isinstance(sftp_host, str),
            isinstance(sftp_port, int),
            isinstance(sftp_user, str),
            isinstance(sftp_password, str),
        ]):
            raise ValueError(
                'All SFTP connection parameters must be provided.',
            )

    def __init__(
        self,
        target: SFTPManagerConfig,
        pins_path: Optional[Path] = None,
    ):
        self.check_args(
            sftp_host=target['sftp_host'],
            sftp_port=target['sftp_port'],
            sftp_user=target['sftp_user'],
            sftp_password=target['sftp_password'],
        )
        self.host = target['sftp_host']
        self.port = target['sftp_port']
        self.user = target['sftp_user']
        self.password = target['sftp_password']
        self.key_filepath = target['key_filepath']
        self.key_password = target['key_password']
        self._pins_path = pins_path or resolve_host_pins_path()
        self._transport: Optional[Transport] = None
        self._sftp: Optional[SFTPClient] = None

    def __enter__(self) -> 'SFTPManager':
        """Establish an SFTP connection.

        Returns:
            SFTPManager: The SFTPManager instance.
        """
        self._connect()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the SFTP connection.

        Args:
            exc_type (Optional[Type[BaseException]]): The type of the exception
                raised.
            exc_value (Optional[BaseException]): The exception instance raised.
            traceback (Optional[TracebackType]): The traceback object.

        Returns:
            None.
        """
        logger.info(
            f'Closing SFTP connection.{exc_type=:}, {exc_value=:}, '
            f'{traceback=:}',
        )
        self.close()

    def _connect(self) -> None:
        """Establish a host-key-verified SFTP connection.

        Opens a Transport over a socket with a bounded connect timeout,
        completes the key exchange, and checks the server's host key
        against the trust-on-first-use pin store *before* sending any
        credentials. Then authenticates either via RSA private key (if
        key_filepath is set) or via username/password, and builds the
        SFTP client (self._sftp) from the transport. Keepalives and a
        channel timeout are enabled so a connection that dies silently
        is detected instead of blocking a transfer forever.

        This deliberately replaces Transport.connect(), which is a
        shortcut for start_client + auth and so would send the password
        before anything about the server had been checked. connect()
        calls start_client() itself, so the two must never be combined.

        Raises:
            SSHException: If the TCP connection cannot be established.
            HostKeyMismatchError: If the server's host key differs from
                the stored pin. No credentials are sent in that case.
            HostPinStoreError: If the pin file exists but is unusable.

        Returns:
            None.
        """
        pin = get_pin(self.host, self.port, self._pins_path)
        self._transport = Transport(self._open_socket())
        try:
            if pin is not None:
                # Ask only for the family we pinned, so a server that
                # offers several host keys can't hand us a different
                # one and read as a mismatch -- and so an attacker
                # can't downgrade to a type we hold no pin for.
                options = self._transport.get_security_options()
                options.key_types = preferred_key_algorithms(pin.key_type)

            # No timeout argument: start_client's wait loop can break on
            # expiry *without* raising, leaving negotiation incomplete.
            # banner_timeout/handshake_timeout already bound the
            # handshake, and Transport.connect passes none either.
            self._transport.start_client()
            self._verify_host_key()

            if self.key_filepath:
                private_key = RSAKey.from_private_key_file(
                    self.key_filepath,
                    password=self.key_password,
                )
                self._transport.auth_publickey(self.user, private_key)
            else:
                self._transport.auth_password(self.user, self.password)
        except Exception:
            self.close()
            raise

        self._transport.set_keepalive(KEEPALIVE_INTERVAL_SECONDS)
        self._sftp = SFTPClient.from_transport(self._transport)
        self._apply_channel_timeout()

    def _verify_host_key(self) -> None:
        """Check the negotiated host key against the pin store.

        Raises:
            HostKeyMismatchError: If the key differs from the stored pin.

        Returns:
            None.
        """
        key = self._transport.get_remote_server_key()
        key_type = key.get_name()
        fingerprint = format_fingerprint(key.asbytes())
        try:
            newly_pinned = verify_or_pin(
                self.host,
                self.port,
                key_type,
                fingerprint,
                self._pins_path,
            )
        except HostKeyMismatchError as exc:
            logger.error(str(exc))
            raise

        if newly_pinned:
            logger.warning(
                f'Trusting the SFTP host key for {self.host}:{self.port} on '
                f'first use ({key_type} {fingerprint}). Recorded in '
                f'{self._pins_path}; later connections must match it.',
            )
        else:
            logger.info(
                f'SFTP host key verified for {self.host}:{self.port} '
                f'({key_type} {fingerprint}).',
            )

    def _open_socket(self) -> socket.socket:
        """Open a TCP socket to the target with a bounded connect timeout.

        Paramiko's own `Transport((host, port))` form calls
        `socket.connect()` with no timeout, leaving the connect attempt
        to the platform default. Connecting here instead keeps that
        bounded, and mirrors paramiko's error type/message so callers
        and logs see no behavioural change.

        Raises:
            SSHException: If the connection cannot be established.

        Returns:
            socket.socket: The connected socket.
        """
        try:
            return socket.create_connection(
                (self.host, self.port),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except OSError as e:
            raise SSHException(
                f'Unable to connect to {self.host}: {e}',
            ) from e

    def _apply_channel_timeout(self) -> None:
        """Bound how long a single SFTP request may wait for a response.

        Returns:
            None.
        """
        if self._sftp is None:
            return
        channel = self._sftp.get_channel()
        if channel is not None:
            channel.settimeout(CHANNEL_TIMEOUT_SECONDS)

    def close(self) -> None:
        """Close the SFTP connection.

        Safely closes both the SFTP client and the transport if they
        are open. This method is idempotent and safe to call multiple
        times or when nothing is connected.

        Returns:
            None.
        """
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._transport:
            self._transport.close()
            self._transport = None

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=10),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True,
        retry=(
            retry_if_result(lambda result: not result)
            | retry_if_exception_type((
                SSHException,
                ConnectionError,
                TimeoutError,
                EOFError,
            ))
        ),
    )
    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
    ) -> SFTPAttributes:
        """Upload a file to the SFTP server.

        Args:
            local_path (Path): The local file path to upload.
            remote_path (str): The remote file path on the SFTP server.

        Raises:
            FileNotFoundError: If the local file does not exist.
            RuntimeError: If the SFTP client is not connected.

        Returns:
            SFTPAttributes: The attributes of the uploaded file.
        """
        if not Path(local_path).is_file():
            raise FileNotFoundError(f'Local file {local_path} does not exist.')
        if not self._sftp:
            raise RuntimeError(CLIENT_NOT_CONNECTED)
        result = self._sftp.put(
            localpath=str(local_path.resolve()),
            remotepath=remote_path,
        )
        logger.info(f'Uploaded {local_path.absolute()} to {remote_path}.')
        return result

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=10),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True,
        retry=retry_if_not_result(lambda result: result is None),
    )
    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Download a file from the SFTP server.

        Any exception raised during the operation will be passed through.

        Args:
            remote_path (str): The remote file path on the SFTP server.
            local_path (Path): The local file path to save the downloaded
                file.

        Raises:
            RuntimeError: If the SFTP client is not connected.

        Returns:
            None.
        """
        if not self._sftp:
            raise RuntimeError(CLIENT_NOT_CONNECTED)
        self._sftp.get(remote_path, local_path)
        logger.info(f'Downloaded {remote_path} to {local_path}.')

    def list_files(self, remote_path: str) -> List[Path]:
        """List files in a remote directory.

        Args:
            remote_path (str): The remote directory path.

        Raises:
            RuntimeError: If the SFTP client is not connected.

        Returns:
            List[Path]: A list of paths representing the files in the
                remote directory.
        """
        if not self._sftp:
            raise RuntimeError(CLIENT_NOT_CONNECTED)
        logger.info(f'Listing files in {remote_path}.')
        return [Path(file) for file in self._sftp.listdir(remote_path)]

    def make_directory(self, remote_path: str) -> None:
        """Create a directory on the SFTP server.

        Args:
            remote_path (str): The remote directory path to create.

        Raises:
            RuntimeError: If the SFTP client is not connected.

        Returns:
            None.
        """
        if not self._sftp:
            raise RuntimeError(CLIENT_NOT_CONNECTED)
        self._sftp.mkdir(remote_path)
        logger.info(f'Created directory {remote_path} on SFTP server.')

    def remove_directory(self, remote_path: str) -> None:
        """Remove a directory on the SFTP server.

        Args:
            remote_path (str): The remote directory path to remove.

        Raises:
            RuntimeError: If the SFTP client is not connected.

        Returns:
            None.
        """
        if not self._sftp:
            raise RuntimeError(CLIENT_NOT_CONNECTED)
        self._sftp.rmdir(remote_path)
        logger.info(f'Removed directory {remote_path} from SFTP server.')
