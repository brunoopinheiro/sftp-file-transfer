from logging import Logger, getLogger
from pathlib import Path
from shutil import SameFileError, SpecialFileError, copyfile
from typing import List, Union

logger: Logger = getLogger(__name__)


class FileManager:
    def __init__(self):
        self.root_dir = self._find_root_directory()

    @staticmethod
    def _find_root_directory() -> Path:
        """Find the root directory for the project.

        Returns:
            Path: The root directory path.
        """
        root = Path.home().absolute()
        logger.info(f'Root directory set to: {root}')
        return root

    @staticmethod
    def fetch_files(directory: Union[str, Path]) -> List[Path]:
        """Fetch files from the specified directory.

        Args:
            directory (Union[str, Path]): The directory to fetch files from.

        Returns:
            List[Path]: A list of file paths.
        """
        if isinstance(directory, str):
            directory = Path(directory)

        dir_path = directory.absolute()
        files = [f for f in dir_path.iterdir() if f.is_file()]
        logger.info(f'Fetched files from {dir_path}: {files}')
        return files

    @staticmethod
    def fetch_directories(directory: Union[str, Path]) -> List[Path]:
        """Fetch directories from the specified directory.

        Args:
            directory (Union[str, Path]): The directory to fetch directories
                from.

        Returns:
            List[Path]: A list of directory paths.
        """
        if isinstance(directory, str):
            directory = Path(directory)

        dir_path = directory.absolute()
        dirs = [d for d in dir_path.iterdir() if d.is_dir()]
        logger.info(f'Fetched directories from {dir_path}: {dirs}')
        return dirs

    @staticmethod
    def fetch_files_filtered_by_extension(
        directory: Union[str, Path],
        extension: str,
    ) -> List[Path]:
        """Fetch files from the specified directory filtered by extension.

        Args:
            directory (Union[str, Path]): The directory to fetch files from.
            extension (str): The file extension to filter by.

        Returns:
            List[Path]: A list of file paths with the specified extension.
        """
        if isinstance(directory, str):
            directory = Path(directory)

        dir_path = directory.absolute()
        files = [
            f
            for f in dir_path.iterdir()
            if f.is_file() and f.suffix == extension
        ]
        logger.info(f'Fetched {extension} files from {dir_path}: {files}')
        return files

    @staticmethod
    def copy_files_to(
        source_files: List[Path],
        destination: Union[str, Path],
    ) -> None:
        """Copy files to the specified destination directory.

        Args:
            source_files (List[Path]): List of source file paths to copy.
            destination (Union[str, Path]): The destination directory to copy
                files to.

        Raises:
            (SameFileError, SpecialFileError): If an error occurs while
                copying files.
            Exception: If an unexpected error occurs.
        """
        if isinstance(destination, str):
            destination = Path(destination)

        try:
            destination = destination.absolute()
            if not destination.exists():
                destination.mkdir(parents=True, exist_ok=True)

            for src in source_files:
                if not src.exists():
                    logger.warning(f'Source file {src} does not exist.')
                elif not src.is_file():
                    logger.warning(f'Source {src} is not a file.')
            for src in source_files:
                dest = destination / src.name
                copyfile(src, dest)
        except (SameFileError, SpecialFileError) as e:
            logger.error(f'Error copying files: {e}')
            raise e
        except Exception as e:
            logger.error(f'Unexpected error while copying files: {e}')
            raise e
