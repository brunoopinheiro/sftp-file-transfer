from pathlib import Path
from typing import Union, List


class FileManager:

    def __init__(self):
        self.root_dir = self._find_root_directory()

    def _find_root_directory(self) -> Path:
        """Find the root directory for the project.

        Returns:
            Path: The root directory path.
        """
        root = Path.home().absolute()
        return root

    def fetch_files(self, directory: Union[str, Path]) -> List[Path]:
        """Fetch files from the specified directory.

        Args:
            directory (Union[str, Path]): The directory to fetch files from.

        Returns:
            List[Path]: A list of file paths.
        """
        if isinstance(directory, str):
            directory = Path(directory)

        dir_path = directory.absolute()
        return [f for f in dir_path.iterdir() if f.is_file()]

    def fetch_directories(self, directory: Union[str, Path]) -> List[Path]:
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
        return [d for d in dir_path.iterdir() if d.is_dir()]

    def fetch_files_filtered_by_extension(
        self,
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
        return [f for f in dir_path.iterdir()
                if f.is_file() and f.suffix == extension]
