import asyncio
import os
from datetime import datetime, timedelta
from logging import Logger
from pathlib import Path
from typing import List, Optional

from aioclock import AioClock, At
from aioclock.group import Group

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.file_manager import FileManager
from sftp_file_transfer.components.history_tracker import HistoryTracker
from sftp_file_transfer.components.logger_setup import setup_logger
from sftp_file_transfer.components.sftp_manager import (
    SFTPManager,
    SFTPManagerConfig,
)

group = Group()
logger: Logger = setup_logger()


def select_files_to_send(
    local_dir_list: List[str],
    file_extension: Optional[str],
    t_delta: Optional[str],
    tracker: HistoryTracker,
) -> List[Path]:
    """Select which files should be sent on this run.

    Fetches files from each local directory, narrows them to the date
    range between the last successfully sent date and the configured
    target day, and unions in any file that previously failed to send
    (regardless of its date) so it gets retried.

    Args:
        local_dir_list (List[str]): Local directories to scan for files.
        file_extension (Optional[str]): Extension to filter files by.
        t_delta (Optional[str]): Days to subtract from today to compute the
            target day. If unset, no date filtering is applied.
        tracker (HistoryTracker): The send-history tracker to consult for
            the last successful send date and pending failed files.

    Returns:
        List[Path]: Deduplicated list of files to attempt sending.
    """
    range_files: List[Path] = []

    for local_dir in local_dir_list:
        if file_extension:
            fetched_files = FileManager.fetch_files_filtered_by_extension(
                directory=local_dir,
                extension=file_extension,
            )
        else:
            fetched_files = FileManager.fetch_files(directory=local_dir)

        if t_delta:
            target_day = (datetime.now() - timedelta(days=int(t_delta))).date()
            last_sent = tracker.get_last_sent_date()
            if last_sent is None:
                range_start = target_day
            else:
                range_start = last_sent + timedelta(days=1)

            if range_start <= target_day:
                fetched_files = FileManager.filter_files_by_date_range(
                    fetched_files,
                    range_start,
                    target_day,
                )
            else:
                fetched_files = []

        range_files.extend(fetched_files)

    failed_files = [
        f for f in tracker.get_pending_failed_files() if f.is_file()
    ]

    combined: dict = {}
    for file in range_files + failed_files:
        combined[HistoryTracker.hash_path(file)] = file

    return list(combined.values())


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
        db_path = os.getenv('HISTORY_DB_PATH', 'data/send_history.db')

        if not local_dir_list or not remote_dir:
            raise ValueError('LOCAL_PATH and REMOTE_PATH must be set in env')
        local_dir_list = local_dir_list.split(';')
        manager = SFTPManager(config)

        with manager as sftp, HistoryTracker(db_path) as tracker:
            all_files = select_files_to_send(
                local_dir_list,
                file_extension,
                t_delta,
                tracker,
            )

            for file in all_files:
                try:
                    sftp.upload_file(
                        local_path=file,
                        remote_path=f'{remote_dir}/{file.name}',
                    )
                    tracker.record_attempt(file, success=True)
                except Exception as e:
                    tracker.record_attempt(file, success=False, error=str(e))
                    logger.error(f'Failed to send {file}: {e}')
                    continue

            sftp.list_files(remote_dir)

        print(f'Scheduled SFTP file transfer completed at {datetime.now()}.')

    except Exception:
        logger.exception('Scheduled SFTP file transfer failed.')


app = AioClock()
app.include_group(group)


if __name__ == '__main__':
    print('Starting scheduled SFTP file transfer...')
    print('It will run every day at 00:01:01 (America/Recife timezone).')
    print('Press Ctrl+C to exit.')
    asyncio.run(app.serve())
