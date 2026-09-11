import asyncio
import os
from datetime import datetime
from logging import Logger
from pathlib import Path
from typing import List, Optional

from aioclock import AioClock, Every
from aioclock.group import Group

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.file_manager import FileManager
from sftp_file_transfer.components.generator_runner import (
    run_generator_script,
)
from sftp_file_transfer.components.history_tracker import HistoryTracker
from sftp_file_transfer.components.logger_setup import setup_logger
from sftp_file_transfer.components.sftp_manager import (
    SFTPManager,
    SFTPManagerConfig,
)

group = Group()
logger: Logger = setup_logger()
POLL_INTERVAL_SECONDS = int(os.getenv('POLL_INTERVAL_SECONDS', '30'))


def select_files_to_send(
    local_dir_list: List[str],
    file_extension: Optional[str],
    tracker: HistoryTracker,
) -> List[Path]:
    """Select which files should be sent on this run.

    Fetches files from each local directory, skips anything already
    recorded as sent in the ledger, and unions in any file that
    previously failed to send so it gets retried.

    Args:
        local_dir_list (List[str]): Local directories to scan for files.
        file_extension (Optional[str]): Extension to filter files by.
        tracker (HistoryTracker): The send-history tracker to consult for
            already-sent hashes and pending failed files.

    Returns:
        List[Path]: Deduplicated list of files to attempt sending.
    """
    sent_hashes = tracker.get_sent_hashes()
    candidate_files: List[Path] = []

    for local_dir in local_dir_list:
        if file_extension:
            fetched_files = FileManager.fetch_files_filtered_by_extension(
                directory=local_dir,
                extension=file_extension,
            )
        else:
            fetched_files = FileManager.fetch_files(directory=local_dir)

        candidate_files.extend(
            f
            for f in fetched_files
            if HistoryTracker.hash_path(f) not in sent_hashes
        )

    failed_files = [
        f for f in tracker.get_pending_failed_files() if f.is_file()
    ]

    combined: dict = {}
    for file in candidate_files + failed_files:
        combined[HistoryTracker.hash_path(file)] = file

    return list(combined.values())


@group.task(
    trigger=Every(
        seconds=POLL_INTERVAL_SECONDS,
        first_run_strategy='immediate',
    ),
)
def scheduled_task():
    print('Starting scheduled SFTP file transfer cycle...')
    try:
        generator_script = os.getenv('GENERATOR_SCRIPT_PATH')
        generator_timeout = os.getenv('GENERATOR_TIMEOUT_SECONDS')
        if generator_script:
            try:
                run_generator_script(
                    generator_script,
                    timeout_seconds=(
                        int(generator_timeout) if generator_timeout else None
                    ),
                )
            except Exception as e:
                logger.error(f'Folio generation step failed: {e}')

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
        db_path = os.getenv('HISTORY_DB_PATH', 'data/send_history.db')

        if not local_dir_list or not remote_dir:
            raise ValueError('LOCAL_PATH and REMOTE_PATH must be set in env')
        local_dir_list = local_dir_list.split(';')
        manager = SFTPManager(config)

        with manager as sftp, HistoryTracker(db_path) as tracker:
            all_files = select_files_to_send(
                local_dir_list,
                file_extension,
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

        print(
            f'Scheduled SFTP file transfer cycle completed at '
            f'{datetime.now()}.',
        )

    except Exception:
        logger.exception('Scheduled SFTP file transfer cycle failed.')


app = AioClock()
app.include_group(group)


if __name__ == '__main__':  # pragma: no cover
    print('Starting scheduled SFTP file transfer...')
    print(f'It will run every {POLL_INTERVAL_SECONDS} seconds.')
    print('Press Ctrl+C to exit.')
    asyncio.run(app.serve())
