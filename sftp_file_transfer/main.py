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
        local = Path(local_path).resolve()

        if not local.is_dir():
            local.mkdir(parents=True)

        env = EnvLoader()
        config = SFTPManagerConfig(
            sftp_host=env.SFTP_HOST,
            sftp_port=int(env.SFTP_PORT),
            sftp_user=env.SFTP_USER,
            sftp_password=env.SFTP_PASSWORD,
            key_filepath=None,
            key_password=None,
        )
        print(config)
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

        print('Files to Send: ', all_files)

        with manager as sftp:
            for file in all_files:
                sftp.upload_file(
                    local_path=file,
                    remote_path=f'{remote_path}/{file.name}',
                )

            remote_files = sftp.list_files(remote_path)
            print('Files after Upload: ', remote_files)

    except Exception as e:
        print(e)


def sftp_sample_flux():
    config = SFTPManagerConfig(
        sftp_host='192.168.9.157',
        sftp_port=22,
        sftp_user='tester',
        sftp_password='password',
        key_filepath=None,
        key_password=None,
    )

    manager = SFTPManager(config)
    with manager as sftp:
        # list files in the root directory
        files = sftp.list_files('')
        print(f'Files in root directory: {files}')

        # create a new directory
        new_dir = Path('new_directory')
        sftp.make_directory(str(new_dir))

        # remove the directory
        sftp.remove_directory(str(new_dir))

        # create another new directory
        new_dir = Path('uploads')
        sftp.make_directory(str(new_dir))

        # Download
        remote_file = files[0]
        local_path = Path('.', 'downloaded_file.txt')

        sftp.download_file(str(remote_file), local_path)

        # update file
        assert local_path.is_file(), 'Downloaded file does not exist.'
        local_path.write_text('Updated content for the downloaded file.')

        # Upload
        sftp.upload_file(local_path, str(new_dir / local_path.name))

        files_after_upload = sftp.list_files(str(new_dir))
        print(f'Files after upload: {files_after_upload}')
