import logging
from logging import Logger, getLogger
from pathlib import Path
from typing import Optional, TypedDict

from paramiko import RSAKey, SFTPAttributes, SFTPClient, Transport
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_result,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

logger: Logger = getLogger(__name__)
CLIENT_NOT_CONNECTED = 'SFTP client is not connected.'


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
        """Check if the provided arguments are valid."""
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
        self._transport: Optional[Transport] = None
        self._sftp: Optional[SFTPClient] = None

    def __enter__(self) -> 'SFTPManager':
        """Establish an SFTP connection.

        Returns:
            SFTPManager: The SFTPManager instance.
        """
        self._connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Close the SFTP connection.

        Args:
            exc_type (Optional[Type[BaseException]]): The type of the exception
                raised.
            exc_value (Optional[BaseException]): The exception instance raised.
            traceback (Optional[TracebackType]): The traceback object.
        """
        logger.info(
            f'Closing SFTP connection.{exc_type=:}, {exc_value=:}, {traceback=:}',  # noqa
        )
        self.close()

    def _connect(self) -> None:
        """Establish an SFTP connection."""
        self._transport = Transport((self.host, self.port))
        if self.key_filepath:
            private_key = RSAKey.from_private_key_file(
                self.key_filepath,
                password=self.key_password,
            )
            self._transport.connect(
                username=self.user,
                pkey=private_key,
            )
        else:
            self._transport.connect(
                username=self.user,
                password=self.password,
            )

        self._sftp = SFTPClient.from_transport(self._transport)

    def close(self) -> None:
        """Close the SFTP connection."""
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._transport:
            self._transport.close()
            self._transport = None

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=10),
        stop=stop_after_attempt(2),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        retry=retry_if_result(lambda result: not isinstance(result, str)),
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
        stop=stop_after_attempt(2),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True,
        retry=retry_if_not_result(lambda result: result is None),
    )
    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Download a file from the SFTP server.
        Any exception raised during the operation will be passed through.

        Args:
            remote_path (str): The remote file path on the SFTP server.
            local_path (Path): The local file path to save the downloaded file.
        """
        if not self._sftp:
            raise RuntimeError(CLIENT_NOT_CONNECTED)
        self._sftp.get(remote_path, local_path)
        logger.info(f'Downloaded {remote_path} to {local_path}.')
