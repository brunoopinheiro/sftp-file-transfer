from datetime import datetime, timedelta
from pathlib import Path

from sftp_file_transfer.components.file_manager import FileManager
from sftp_file_transfer.components.sftp_manager import (
    SFTPManager,
    SFTPManagerConfig,
)


def sftp_sample_flux():
    config = SFTPManagerConfig(
        sftp_host='192.168.9.157',
        sftp_port=22,
        sftp_user='tester',
        sftp_password='password',
        key_filepath=None,
        key_password=None
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
        assert local_path.is_file(), "Downloaded file does not exist."
        local_path.write_text('Updated content for the downloaded file.')

        # Upload
        sftp.upload_file(local_path, str(new_dir / local_path.name))

        files_after_upload = sftp.list_files(str(new_dir))
        print(f'Files after upload: {files_after_upload}')


if __name__ == '__main__':
    # sftp_sample_flux()
    downloads_dir = Path.home() / 'Downloads'
    print(f'Downloads directory: {downloads_dir}')
    fmanager = FileManager()
    res = fmanager.fetch_files(downloads_dir)
    a = res[0]

    b = a.stat()

    last_modified = b.st_mtime
    last_modified_datetime = datetime.fromtimestamp(last_modified)
    print(f'Last modified time: {last_modified_datetime}')

    files_sorted_by_date = sorted(
        res, key=lambda x: x.stat().st_mtime, reverse=True
    )

    yesterday = datetime.today() - timedelta(days=1)
    files_from_yesterday = [
        f for f in files_sorted_by_date
        if datetime.fromtimestamp(f.stat().st_mtime).date() == yesterday.date()
    ]

    print('Ended Execution')
