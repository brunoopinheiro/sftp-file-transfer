from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from typer import Context, Option, Typer

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.file_manager import FileManager
from sftp_file_transfer.components.logger_setup import (
    log_startup_banner,
    setup_logger,
)
from sftp_file_transfer.components.sftp_manager import (
    SFTPManager,
    SFTPManagerConfig,
)

app = Typer()
logger = setup_logger()


@app.callback(invoke_without_command=True)
def main(
    ctx: Context,
    t_delta: Optional[int] = Option(
        None,
        '--timedelta',
        '-T',
        help='The day difference from which the files should be sent. 0 = Today, 1 = yesterday',  # noqa
    ),
    file_extension: Optional[str] = Option(
        None,
        '--file_ext',
        '-F',
        help='The file extension that must be sent, if any.',
    ),
    remote_path: str = Option(
        None,
        '--remote',
        '-R',
        help='The remote path to which the files must be sent.',
    ),
    local_path: str = Option(
        None,
        '--local',
        '-L',
        help='The local path from which the files must be fetched.',
    ),
) -> None:
    """CLI entrypoint that uploads matching files over SFTP.

    Fetches files from the local path (optionally filtered by
    extension), filters by date if specified, and uploads each
    file to the remote path via SFTP.

    Args:
        ctx: Typer context object.
        t_delta: Day offset from today (0=today, 1=yesterday), or
            None for all files.
        file_extension: Filter by file extension, or None for all.
        remote_path: Remote directory path for uploads.
        local_path: Local directory path to fetch files from.

    Returns:
        None.
    """
    if ctx.invoked_subcommand:
        return
    log_startup_banner(logger, 'sftp-file-transfer')
    try:
        env = EnvLoader()
        config = SFTPManagerConfig(
            sftp_host=env.SFTP_HOST,
            sftp_port=int(env.SFTP_PORT),
            sftp_user=env.SFTP_USER,
            sftp_password=env.SFTP_PASSWORD,
            key_filepath=None,
            key_password=None,
        )
        manager = SFTPManager(config)

        all_files: List[Path] = []
        if file_extension:
            all_files = FileManager.fetch_files_filtered_by_extension(
                directory=local_path,
                extension=file_extension,
            )
        else:
            all_files = FileManager.fetch_files(local_path)

        if t_delta is not None:
            target_day = datetime.today() - timedelta(days=t_delta)
            all_files = FileManager.filter_files_by_date(all_files, target_day)

        with manager as sftp:
            for file in all_files:
                sftp.upload_file(
                    local_path=file,
                    remote_path=f'{remote_path}/{file.name}',
                )

            sftp.list_files(remote_path)

    except Exception as e:
        print(e)


if __name__ == '__main__':
    app()
