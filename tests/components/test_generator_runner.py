import subprocess
from unittest.mock import patch

import pytest

from sftp_file_transfer.components.generator_runner import (
    run_generator_script,
)


def _completed_process(returncode, stdout='', stderr=''):
    return subprocess.CompletedProcess(
        args=['powershell.exe'],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_run_generator_script_success_does_not_raise():
    """Test that a zero exit code does not raise."""
    with patch(
        'subprocess.run',
        return_value=_completed_process(0, stdout='done'),
    ) as mock_run:
        run_generator_script('C:/scripts/generate.ps1')

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == [
        'powershell.exe',
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        'C:/scripts/generate.ps1',
    ]
    assert kwargs['timeout'] is None


def test_run_generator_script_passes_timeout():
    """Test that timeout_seconds is forwarded to subprocess.run."""
    expected_timeout = 15
    with patch(
        'subprocess.run',
        return_value=_completed_process(0),
    ) as mock_run:
        run_generator_script(
            'C:/scripts/generate.ps1',
            timeout_seconds=expected_timeout,
        )

    assert mock_run.call_args.kwargs['timeout'] == expected_timeout


def test_run_generator_script_nonzero_exit_raises():
    """Test that a non-zero exit code raises CalledProcessError."""
    with patch(
        'subprocess.run',
        return_value=_completed_process(1, stderr='boom'),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            run_generator_script('C:/scripts/generate.ps1')


def test_run_generator_script_timeout_propagates():
    """Test that a subprocess timeout is not swallowed."""
    with patch(
        'subprocess.run',
        side_effect=subprocess.TimeoutExpired(cmd='powershell.exe', timeout=5),
    ):
        with pytest.raises(subprocess.TimeoutExpired):
            run_generator_script('C:/scripts/generate.ps1', timeout_seconds=5)
