import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from shutil import SameFileError

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

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


def test_sort_files_by_date_ascending_orders_oldest_first(tmp_path):
    """Test that sort_files_by_date ascending orders oldest first."""
    file_manager = FileManager()
    today = datetime.now()

    file1 = tmp_path / 'file1.txt'
    file2 = tmp_path / 'file2.txt'
    file3 = tmp_path / 'file3.txt'
    file1.touch()
    file2.touch()
    file3.touch()

    _set_mtime(file1, today - timedelta(days=5))
    _set_mtime(file2, today - timedelta(days=2))
    _set_mtime(file3, today - timedelta(days=10))

    files = [file1, file2, file3]
    sorted_files = file_manager.sort_files_by_date(files, reverse=False)

    assert sorted_files == [file3, file1, file2]


def test_sort_files_by_date_descending_orders_newest_first(tmp_path):
    """Test that sort_files_by_date descending orders newest first."""
    file_manager = FileManager()
    today = datetime.now()

    file1 = tmp_path / 'file1.txt'
    file2 = tmp_path / 'file2.txt'
    file3 = tmp_path / 'file3.txt'
    file1.touch()
    file2.touch()
    file3.touch()

    _set_mtime(file1, today - timedelta(days=5))
    _set_mtime(file2, today - timedelta(days=2))
    _set_mtime(file3, today - timedelta(days=10))

    files = [file1, file2, file3]
    sorted_files = file_manager.sort_files_by_date(files, reverse=True)

    assert sorted_files == [file2, file1, file3]


@given(
    day_offsets=st.lists(
        st.integers(min_value=0, max_value=100),
        min_size=2,
        max_size=6,
        unique=True,
    ),
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_sort_files_by_date_property_monotonic(tmp_path, day_offsets):
    """Test that sort_files_by_date returns monotonically ordered mtimes."""
    file_manager = FileManager()
    today = datetime.now()

    files = []
    for i, offset in enumerate(day_offsets):
        file_path = tmp_path / f'file_{i}.txt'
        file_path.touch()
        _set_mtime(file_path, today - timedelta(days=offset))
        files.append(file_path)

    shuffled_files = files.copy()
    random.shuffle(shuffled_files)

    sorted_files = file_manager.sort_files_by_date(
        shuffled_files, reverse=False
    )

    mtimes = [f.stat().st_mtime for f in sorted_files]
    assert mtimes == sorted(mtimes)


def test_filter_files_by_date_matches_only_exact_day(tmp_path):
    """Test that filter_files_by_date matches only exact day."""
    file_manager = FileManager()
    today = datetime.now()

    today_file = tmp_path / 'today.txt'
    yesterday_file = tmp_path / 'yesterday.txt'
    week_ago_file = tmp_path / 'week_ago.txt'
    today_file.touch()
    yesterday_file.touch()
    week_ago_file.touch()

    _set_mtime(today_file, today)
    _set_mtime(yesterday_file, today - timedelta(days=1))
    _set_mtime(week_ago_file, today - timedelta(days=7))

    files = [today_file, yesterday_file, week_ago_file]
    filtered = file_manager.filter_files_by_date(files, today)

    assert filtered == [today_file]


def test_copy_files_to_accepts_string_destination(tmp_path):
    """Test that copy_files_to accepts string destination."""
    file_manager = FileManager()

    source_dir = tmp_path / 'source_dir'
    source_dir.mkdir()
    source_file = source_dir / 'file1.txt'
    source_file.touch()

    dest_dir = tmp_path / 'dest_dir'
    dest_dir.mkdir()

    file_manager.copy_files_to([source_file], str(dest_dir))

    copied_file = dest_dir / 'file1.txt'
    assert copied_file.exists()


def test_copy_files_to_raises_on_missing_source_file(tmp_path):
    """Test that copy_files_to raises on missing source file."""
    file_manager = FileManager()

    missing_file = tmp_path / 'missing.txt'
    dest_dir = tmp_path / 'dest_dir'
    dest_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        file_manager.copy_files_to([missing_file], dest_dir)


def test_copy_files_to_raises_on_directory_source(tmp_path):
    """Test that copy_files_to raises when source is a directory."""
    file_manager = FileManager()

    source_dir = tmp_path / 'source_dir'
    source_dir.mkdir()
    dest_dir = tmp_path / 'dest_dir'
    dest_dir.mkdir()

    # OSError is deliberately broad here: shutil.copyfile raises
    # IsADirectoryError on POSIX but PermissionError on Windows for a
    # directory source, and both are OSError subclasses.
    with pytest.raises(OSError):  # noqa: PT011
        file_manager.copy_files_to([source_dir], dest_dir)


def test_copy_files_to_raises_samefileerror_when_source_equals_destination(
    tmp_path,
):
    """Test that copy_files_to raises SameFileError for same source/dest."""
    file_manager = FileManager()

    source_dir = tmp_path / 'source_dir'
    source_dir.mkdir()
    source_file = source_dir / 'file1.txt'
    source_file.write_text('content')

    with pytest.raises(SameFileError):
        file_manager.copy_files_to([source_file], source_dir)
