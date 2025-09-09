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
        local_dir = os.getenv('LOCAL_PATH')
        remote_dir = os.getenv('REMOTE_PATH')
        file_extension = os.getenv('FILE_EXTENSION')
        t_delta = os.getenv('TIME_DELTA')

        if not local_dir or not remote_dir:
            raise ValueError('LOCAL_PATH and REMOTE_PATH must be set in env')
        manager = SFTPManager(config)

        all_files: List[Path] = []
        if file_extension:
            all_files = FileManager.fetch_files_filtered_by_extension(
                directory=local_dir,
                extension=file_extension
            )
        else:
            all_files = FileManager.fetch_files(directory=local_dir)

        if t_delta:
            target_day = datetime.now() - timedelta(days=int(t_delta))
            all_files = FileManager.filter_files_by_date(all_files, target_day)

        with manager as sftp:
            for file in all_files:
                sftp.upload_file(
                    local_path=file,
                    remote_path=f'{remote_dir}/{file.name}',
                )

    except Exception as e:
        print(e)


app = AioClock()
app.include_group(group)


if __name__ == '__main__':
    asyncio.run(app.serve())
