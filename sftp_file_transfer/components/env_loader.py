# pragma: no cover
import os
from functools import wraps
from logging import Logger
from typing import Any, Callable

from dotenv import find_dotenv, load_dotenv

from sftp_file_transfer.components.logger_setup import setup_logger

logger: Logger = setup_logger()


def require_env_vars(func: Callable) -> Callable:
    """Decorator to ensure required SFTP environment variables are set.

    This decorator wraps a function to verify that required SFTP configuration
    variables (SFTP_HOST, SFTP_PORT, SFTP_USER, SFTP_PASSWORD) are present
    and valid before the decorated function is executed.

    Args:
        func (Callable): The function to decorate. Must accept arbitrary
            args and kwargs.

    Raises:
        ValueError: If a required environment variable is missing.
        ValueError: If a required environment variable is not a string.

    Returns:
        Callable: The wrapped function with environment variable validation.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
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
        """Initialize EnvLoader by loading environment variables from .env.

        Loads environment variables from a .env file using python-dotenv.
        Logs the result of the operation: either successful load or warning
        if no .env file was found.

        Returns:
            None
        """
        logger.info('Loading environment variables from .env file.')
        res = load_dotenv(find_dotenv())
        if res is True:
            logger.info('Environment variables successfully loaded.')
        else:
            logger.warning('No .env file found or variables not loaded.')

    @require_env_vars
    def __getattribute__(self, name: str) -> Any:
        """Get an environment variable or object attribute.

        Intercepts attribute access to return SFTP configuration variables
        from environment. For SFTP_HOST, SFTP_PORT, SFTP_USER, and
        SFTP_PASSWORD, returns the corresponding environment variable value.
        For other attributes, delegates to the parent class.

        Args:
            name (str): The name of the attribute being accessed.

        Returns:
            Any: The environment variable value for SFTP variables, or the
                attribute from the parent class for other names.
        """
        if name in {'SFTP_HOST', 'SFTP_PORT', 'SFTP_USER', 'SFTP_PASSWORD'}:
            logger.info(f'Accessing environment variable: {name}')
            return os.getenv(name)
        return super().__getattribute__(name)
