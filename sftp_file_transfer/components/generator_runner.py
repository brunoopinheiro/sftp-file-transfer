import subprocess
from logging import Logger
from typing import Optional

from sftp_file_transfer.components.logger_setup import setup_logger

logger: Logger = setup_logger()


def run_generator_script(
    script_path: str,
    timeout_seconds: Optional[int] = None,
) -> None:
    """Invoke the folio-generation PowerShell script and wait for it
    to finish.

    Args:
        script_path (str): Path to the .ps1 script to run.
        timeout_seconds (Optional[int]): Seconds to wait before giving
            up on the script. None waits indefinitely.

    Raises:
        subprocess.CalledProcessError: If the script exits non-zero.
        subprocess.TimeoutExpired: If the script exceeds timeout_seconds.
    """
    result = subprocess.run(
        [
            'powershell.exe',
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', script_path,
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    logger.info(f'Generator script stdout: {result.stdout}')
    if result.returncode != 0:
        logger.error(f'Generator script stderr: {result.stderr}')
        result.check_returncode()
