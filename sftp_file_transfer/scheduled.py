import asyncio
import os
from datetime import datetime
from logging import Logger
from pathlib import Path
from typing import List, Optional

from aioclock import AioClock, Every
from aioclock.group import Group
from dotenv import find_dotenv, load_dotenv

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.file_manager import FileManager
from sftp_file_transfer.components.history_tracker import (
    HistoryTracker,
    resolve_history_db_path,
)
from sftp_file_transfer.components.logger_setup import (
    log_startup_banner,
    setup_logger,
)
from sftp_file_transfer.components.nfce_config import NfceConfig
from sftp_file_transfer.components.nfce_db_client import build_engine
from sftp_file_transfer.components.nfce_generator import run_nfce_extraction
from sftp_file_transfer.components.sftp_manager import (
    SFTPManager,
    SFTPManagerConfig,
)

group = Group()
logger: Logger = setup_logger()
DEFAULT_POLL_INTERVAL_SECONDS = 30


def resolve_poll_interval_seconds() -> int:
    """Resolve the cycle interval from the project .env file.

    Reads POLL_INTERVAL_SECONDS the same way `resolve_history_db_path`
    reads HISTORY_DB_PATH, so every entry point agrees on the interval.
    The .env file must be loaded first: this value is needed at import
    time to build the schedule trigger, which is earlier than any
    entry point constructs an `EnvLoader`.

    Returns:
        int: POLL_INTERVAL_SECONDS from the environment/.env file, or
            DEFAULT_POLL_INTERVAL_SECONDS if it isn't set.
    """
    load_dotenv(find_dotenv())
    return int(
        os.getenv(
            'POLL_INTERVAL_SECONDS',
            str(DEFAULT_POLL_INTERVAL_SECONDS),
        ),
    )


POLL_INTERVAL_SECONDS = resolve_poll_interval_seconds()


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


def run_folio_generation_step() -> None:
    """Run the folio-generation step.

    Runs the in-house NFCe DB extraction (`NfceConfig` +
    `run_nfce_extraction`) when NFCe DB env vars are configured. Does
    nothing (logs a warning) if they aren't. Any failure is logged,
    never raised, so the send step still runs afterwards. The engine is
    always disposed, so a cycle never leaks a pooled DB connection.
    """
    try:
        nfce_config = NfceConfig()
    except ValueError:
        logger.warning(
            'NFCe DB env vars not configured; skipping generation step.',
        )
        return

    engine = None
    try:
        engine = build_engine(
            host=nfce_config.nfce_db_host,
            port=int(nfce_config.nfce_db_port),
            database=nfce_config.nfce_db_name,
            user=nfce_config.nfce_db_user,
            password=nfce_config.nfce_db_password,
        )
        summary = run_nfce_extraction(
            engine,
            nfce_config.nfce_output_path,
            nfce_config.nfce_lookback_days,
        )
        logger.info(
            'NFCe extraction summary: total=%s generated=%s '
            'cancellations=%s no_protocol=%s skipped=%s errors=%s',
            summary.total,
            summary.generated,
            summary.cancellations,
            summary.no_protocol,
            summary.skipped,
            summary.errors,
        )
    except Exception as e:
        logger.error(f'NFCe extraction step failed: {e}')
    finally:
        if engine is not None:
            engine.dispose()


@group.task(
    trigger=Every(
        seconds=POLL_INTERVAL_SECONDS,
        first_run_strategy='immediate',
    ),
)
def scheduled_task() -> None:
    """Run the scheduled SFTP file transfer cycle.

    AioClock-scheduled job that runs the folio-generation step, then
    selects and uploads all pending files to the remote SFTP server,
    recording each attempt (success or failure) in the send-history
    ledger. Catches and logs any exception so the schedule continues
    running even if a cycle fails.

    Returns:
        None.
    """
    print('Starting scheduled SFTP file transfer cycle...')
    try:
        run_folio_generation_step()

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
        db_path = resolve_history_db_path()

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
    log_startup_banner(logger, 'sftp-file-transfer-scheduled')
    print('Starting scheduled SFTP file transfer...')
    print(f'It will run every {POLL_INTERVAL_SECONDS} seconds.')
    print('Press Ctrl+C to exit.')
    asyncio.run(app.serve())
