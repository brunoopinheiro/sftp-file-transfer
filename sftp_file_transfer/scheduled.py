import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from aioclock import AioClock, At
from aioclock.group import Group

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.file_manager import FileManager
from sftp_file_transfer.components.sftp_manager import (
    SFTPManager,
    SFTPManagerConfig,
)

group = Group()


@group.task(
    trigger=At(
        hour=0,
        minute=1,
        second=1,
        max_loop_count=None,
        tz='America/Recife',
    ),
)
def scheduled_task():
    print('Starting scheduled SFTP file transfer...')
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
        local_dir_list = os.getenv('LOCAL_PATH')
        remote_dir = os.getenv('REMOTE_PATH')
        file_extension = os.getenv('FILE_EXTENSION')
        t_delta = os.getenv('TIME_DELTA')

        if not local_dir_list or not remote_dir:
            raise ValueError('LOCAL_PATH and REMOTE_PATH must be set in env')
        local_dir_list = local_dir_list.split(';')
        manager = SFTPManager(config)

        all_files: List[Path] = []

        for local_dir in local_dir_list:
            fetched_files: List[Path] = []
            if file_extension:
                fetched_files = FileManager.fetch_files_filtered_by_extension(
                    directory=local_dir,
                    extension=file_extension
                )
            else:
                fetched_files = FileManager.fetch_files(directory=local_dir)

            if t_delta:
                target_day = datetime.now() - timedelta(days=int(t_delta))
                fetched_files = FileManager.filter_files_by_date(
                    fetched_files,
                    target_day,
                )
            all_files.extend(fetched_files)

        with manager as sftp:
            for file in all_files:
                sftp.upload_file(
                    local_path=file,
                    remote_path=f'{remote_dir}/{file.name}',
                )
            sftp.list_files(remote_dir)

        print(f'Scheduled SFTP file transfer completed at {datetime.now()}.')

    except Exception as e:
        print(e)


app = AioClock()
app.include_group(group)


if __name__ == '__main__':
    print('Starting scheduled SFTP file transfer...')
    print('It will run every day at 00:01:01 (America/Recife timezone).')
    print('Press Ctrl+C to exit.')
    asyncio.run(app.serve())
