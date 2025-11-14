import asyncio
import os
from pathlib import Path
from typing import List

from aioclock import AioClock, Every
from aioclock.group import Group

from sftp_file_transfer.components.env_loader import EnvLoader
from sftp_file_transfer.components.file_manager import FileManager

group = Group()


def find_updates(
    source_files: List[Path],
    destiny_files: List[Path],
) -> List[Path]:
    """Find new files in source_files that are not in destiny_files.

    Args:
        source_files (List[Path]): List of source file paths.
        destiny_files (List[Path]): List of destiny file paths.

    Returns:
        List[Path]: List of new file paths in source_files not present in
            destiny_files.
    """
    source_filenames = set([filepath.name for filepath in source_files])
    destiny_filenames = set([filepath.name for filepath in destiny_files])
    new_filenames = source_filenames - destiny_filenames
    new_filenames = [file_ for file_ in source_files if file_.name in new_filenames]  # noqa
    return new_filenames


@group.task(
    trigger=Every(
        minutes=1,
    )
)
def update_destiny_folder():
    print('Updating files in destiny folder...')
    EnvLoader()
    local_dir = os.getenv('LOCAL_PATH')
    destiny_dir = os.getenv('DESTINY_PATH')
    file_extension = os.getenv('FILE_EXTENSION')

    if not local_dir or not destiny_dir or not file_extension:
        raise ValueError(
            'LOCAL_PATH, DESTINY_PATH, and FILE_EXTENSION must be set in env',
        )
    try:
        local_dir_list = local_dir.split(';')
        for local_dir in local_dir_list:
            target_folder = local_dir.split('/')[-1]

            source_files = FileManager.fetch_files_filtered_by_extension(
                directory=local_dir,
                extension=file_extension,
            )

            destiny_files = FileManager.fetch_files_filtered_by_extension(
                directory=os.path.join(destiny_dir, target_folder),
                extension=file_extension,
            )

            files_to_copy = find_updates(source_files, destiny_files)
            print(f'{source_files=:}')
            print(f'{destiny_files=:}')
            print(files_to_copy)
            print(f'Copying files to destiny folder... {len(files_to_copy)} files found.')  # noqa
            FileManager.copy_files_to(
                source_files=list(files_to_copy),
                destination=os.path.join(destiny_dir, target_folder),
            )

    except Exception as e:
        print(f'Error during local file copy: {e}')


app = AioClock()
app.include_group(group)

if __name__ == '__main__':
    print('Starting local file copy scheduler...')
    print('It will run every 1 minute.')
    print('Press Ctrl+C to stop.')
    asyncio.run(app.serve())
