import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sftp_file_transfer.components.file_manager import FileManager


def _set_mtime(file_path: Path, when: datetime) -> None:
    timestamp = when.timestamp()
    os.utime(file_path, (timestamp, timestamp))


def test_file_manager_initialization():
    """Test the initialization of FileManager."""
    file_manager = FileManager()
    assert file_manager.root_dir.is_absolute()
    assert file_manager.root_dir == Path.home().absolute()


def test_fetch_files(tmp_path):
    """Test fetching files from a directory."""
    file_manager = FileManager()

    # Create a temporary directory and some files
    test_dir = tmp_path / 'test_dir'
    test_dir.mkdir()
    (test_dir / 'file1.txt').touch()
    (test_dir / 'file2.txt').touch()

    # Fetch files from the temporary directory
    files = file_manager.fetch_files(test_dir)

    # Check if the fetched files match the created files
    expected_len = 2
    assert len(files) == expected_len
    assert all(f.name in {'file1.txt', 'file2.txt'} for f in files)


def test_fetch_directories(tmp_path):
    """Test fetching directories from a directory."""
    file_manager = FileManager()

    # Create a temporary directory and some subdirectories
    test_dir = tmp_path / 'test_dir'
    test_dir.mkdir()
    (test_dir / 'subdir1').mkdir()
    (test_dir / 'subdir2').mkdir()

    # Fetch directories from the temporary directory
    directories = file_manager.fetch_directories(test_dir)

    # Check if the fetched directories match the created subdirectories
    expected_len = 2
    assert len(directories) == expected_len
    assert all(d.name in {'subdir1', 'subdir2'} for d in directories)


def test_fetch_files_filtered_by_extension(tmp_path):
    """Test fetching files filtered by extension."""
    file_manager = FileManager()

    # Create a temporary directory and some files with different extensions
    test_dir = tmp_path / 'test_dir'
    test_dir.mkdir()
    (test_dir / 'file1.txt').touch()
    (test_dir / 'file2.py').touch()
    (test_dir / 'file3.txt').touch()

    # Fetch files with .txt extension
    txt_files = file_manager.fetch_files_filtered_by_extension(
        test_dir,
        '.txt',
    )

    # Check if the fetched files match the .txt files created
    expected_len = 2
    assert len(txt_files) == expected_len
    assert all(f.suffix == '.txt' for f in txt_files)
    assert all(f.name in {'file1.txt', 'file3.txt'} for f in txt_files)


def test_fetch_files_with_invalid_directory():
    """Test fetching files with an invalid directory."""
    file_manager = FileManager()

    with pytest.raises(FileNotFoundError):
        file_manager.fetch_files('invalid_directory')


def test_fetch_directories_with_invalid_directory():
    """Test fetching directories with an invalid directory."""
    file_manager = FileManager()

    with pytest.raises(FileNotFoundError):
        file_manager.fetch_directories('invalid_directory')


def test_fetch_files_filtered_by_extension_with_invalid_directory():
    """Test fetching files filtered by extension with an invalid directory."""
    file_manager = FileManager()

    with pytest.raises(FileNotFoundError):
        file_manager.fetch_files_filtered_by_extension(
            'invalid_directory',
            '.txt',
        )


def test_copy_files(tmp_path):
    """Test copying files to a directory."""
    file_manager = FileManager()

    # Create a temporary directory and some files
    source_dir = tmp_path / 'source_dir'
    source_dir.mkdir()
    (source_dir / 'file1.txt').touch()
    (source_dir / 'file2.txt').touch()

    # Create a destination directory
    dest_dir = tmp_path / 'dest_dir'
    dest_dir.mkdir()

    # Copy files to the destination directory
    file_manager.copy_files_to(
        [Path(source_dir / 'file1.txt'), Path(source_dir / 'file2.txt')],
        dest_dir,
    )

    # Check if the files were copied correctly
    copied_files = list(dest_dir.iterdir())
    expected_len = 2
    assert len(copied_files) == expected_len
    assert all(f.name in {'file1.txt', 'file2.txt'} for f in copied_files)


def test_filter_files_by_date_range_inclusive_bounds(tmp_path):
    """Test filtering files by date range with inclusive bounds."""
    file_manager = FileManager()
    today = datetime.now()

    start_file = tmp_path / 'start.txt'
    end_file = tmp_path / 'end.txt'
    outside_file = tmp_path / 'outside.txt'
    start_file.touch()
    end_file.touch()
    outside_file.touch()

    _set_mtime(start_file, today - timedelta(days=3))
    _set_mtime(end_file, today)
    _set_mtime(outside_file, today - timedelta(days=10))

    filtered = file_manager.filter_files_by_date_range(
        [start_file, end_file, outside_file],
        (today - timedelta(days=3)).date(),
        today.date(),
    )

    expected_len = 2
    assert len(filtered) == expected_len
    assert all(f.name in {'start.txt', 'end.txt'} for f in filtered)


def test_filter_files_by_date_range_empty_range(tmp_path):
    """Test filtering files with a range that matches nothing."""
    file_manager = FileManager()
    today = datetime.now()

    file_path = tmp_path / 'file1.txt'
    file_path.touch()
    _set_mtime(file_path, today - timedelta(days=20))

    filtered = file_manager.filter_files_by_date_range(
        [file_path],
        (today - timedelta(days=2)).date(),
        today.date(),
    )

    assert filtered == []


def test_filter_files_by_date_range_boundary_dates(tmp_path):
    """Test that files exactly on the start/end boundary are included."""
    file_manager = FileManager()
    today = datetime.now()

    boundary_file = tmp_path / 'boundary.txt'
    boundary_file.touch()
    _set_mtime(boundary_file, today - timedelta(days=5))

    filtered = file_manager.filter_files_by_date_range(
        [boundary_file],
        (today - timedelta(days=5)).date(),
        (today - timedelta(days=5)).date(),
    )

    assert filtered == [boundary_file]


def test_copy_files_to_non_existent_directory(tmp_path):
    """Test copying files to a non-existent directory."""
    file_manager = FileManager()

    # Create a temporary directory and some files
    source_dir = tmp_path / 'source_dir'
    source_dir.mkdir()
    (source_dir / 'file1.txt').touch()

    # Define a non-existent destination directory
    dest_dir = tmp_path / 'non_existent_dest'

    # Copy files to the non-existent destination directory
    file_manager.copy_files_to([source_dir / 'file1.txt'], dest_dir)

    # Check if the file was copied correctly
    copied_file = dest_dir / 'file1.txt'
    assert copied_file.exists()
