from pathlib import Path

from sftp_file_transfer.components.file_manager import FileManager


def test_file_manager_initialization():
    """Test the initialization of FileManager."""
    file_manager = FileManager()
    assert file_manager.root_dir.is_absolute()
    assert file_manager.root_dir == Path.home().absolute()


def test_fetch_files(tmp_path):
    """Test fetching files from a directory."""
    file_manager = FileManager()

    # Create a temporary directory and some files
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    (test_dir / "file1.txt").touch()
    (test_dir / "file2.txt").touch()

    # Fetch files from the temporary directory
    files = file_manager.fetch_files(test_dir)

    # Check if the fetched files match the created files
    expected_len = 2
    assert len(files) == expected_len
    assert all(f.name in {"file1.txt", "file2.txt"} for f in files)


def test_fetch_directories(tmp_path):
    """Test fetching directories from a directory."""
    file_manager = FileManager()

    # Create a temporary directory and some subdirectories
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    (test_dir / "subdir1").mkdir()
    (test_dir / "subdir2").mkdir()

    # Fetch directories from the temporary directory
    directories = file_manager.fetch_directories(test_dir)

    # Check if the fetched directories match the created subdirectories
    expected_len = 2
    assert len(directories) == expected_len
    assert all(d.name in {"subdir1", "subdir2"} for d in directories)


def test_fetch_files_filtered_by_extension(tmp_path):
    """Test fetching files filtered by extension."""
    file_manager = FileManager()

    # Create a temporary directory and some files with different extensions
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    (test_dir / "file1.txt").touch()
    (test_dir / "file2.py").touch()
    (test_dir / "file3.txt").touch()

    # Fetch files with .txt extension
    txt_files = file_manager.fetch_files_filtered_by_extension(
        test_dir,
        ".txt",
    )

    # Check if the fetched files match the .txt files created
    expected_len = 2
    assert len(txt_files) == expected_len
    assert all(f.suffix == ".txt" for f in txt_files)
    assert all(f.name in {"file1.txt", "file3.txt"} for f in txt_files)
