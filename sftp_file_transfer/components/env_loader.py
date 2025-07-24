# pragma: no cover
import os
from functools import wraps
from logging import Logger, getLogger
from typing import Any, Callable

from dotenv import find_dotenv, load_dotenv

logger: Logger = getLogger(__name__)


def require_env_vars(func: Callable) -> Callable:
    """Decorator to ensure required environment variables are set.

    Args:
        func (callable): The function to decorate.

    Raises:
        ValueError: If a required environment variable is missing.
        ValueError: If a required environment variable is not a string.

    Returns:
        Callable: The wrapped function.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        required_vars = {
            'SFTP_HOST',
            'SFTP_PORT',
            'SFTP_USER',
            'SFTP_PASSWORD',
        }
        for var in required_vars:
            if not os.getenv(var):
                logger.error(f'Missing required environment variable: {var}')
                raise ValueError(
                    f'Missing required environment variable: {var}',
                )
            elif not isinstance(os.getenv(var), str):
                logger.error(f'Environment variable {var} must be a string.')
                raise ValueError(
                    f'Environment variable {var} must be a string.',
                )
        return func(*args, **kwargs)

    return wrapper


class EnvLoader:
    """Load environment variables from a .env file.
    This class loads environment variables required for SFTP operations.

    It ensures that the required variables are set and are of the correct type.

    You can use this class to access environment variables

    Attributes:
        SFTP_HOST (str): The SFTP host.
        SFTP_PORT (str): The SFTP port.
        SFTP_USER (str): The SFTP user.
        SFTP_PASSWORD (str): The SFTP password.
    """

    def __init__(self) -> None:
        logger.info('Loading environment variables from .env file.')
        res = load_dotenv(find_dotenv())
        if res is True:
            logger.info('Environment variables successfully loaded.')
        else:
            logger.warning('No .env file found or variables not loaded.')

    @require_env_vars
    def __getattribute__(self, name: str) -> Any:
        if name in {'SFTP_HOST', 'SFTP_PORT', 'SFTP_USER', 'SFTP_PASSWORD'}:
            logger.info(f'Accessing environment variable: {name}')
            return os.getenv(name)
        return super().__getattribute__(name)
