from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from typer import Context, Option, Typer

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.file_manager import FileManager
from sftp_file_transfer.components.sftp_manager import (
    SFTPManager,
    SFTPManagerConfig,
)

app = Typer()


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
):
    if ctx.invoked_subcommand:
        return
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


if __name__ == "__main__":
    app()
