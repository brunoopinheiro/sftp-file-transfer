from sftp_file_transfer.monitor.config import DEFAULT_THEME, MonitorConfig


def test_load_returns_default_theme_when_no_file_exists(tmp_path):
    """Test load() falls back to DEFAULT_THEME when the file is missing."""
    config_path = tmp_path / 'monitor_config.json'

    config = MonitorConfig.load(config_path)

    assert config.theme == DEFAULT_THEME


def test_save_then_load_round_trips_the_theme(tmp_path):
    """Test a saved theme is read back correctly by a fresh load()."""
    config_path = tmp_path / 'monitor_config.json'

    MonitorConfig(theme='monokai').save(config_path)
    reloaded = MonitorConfig.load(config_path)

    assert reloaded.theme == 'monokai'


def test_save_creates_parent_directory(tmp_path):
    """Test save() creates the parent directory if it doesn't exist."""
    config_path = tmp_path / 'nested' / 'monitor_config.json'

    MonitorConfig(theme='nord').save(config_path)

    assert config_path.exists()


def test_load_falls_back_to_default_on_corrupt_file(tmp_path):
    """Test load() ignores unreadable/corrupt JSON and uses the default."""
    config_path = tmp_path / 'monitor_config.json'
    config_path.write_text('not valid json', encoding='utf-8')

    config = MonitorConfig.load(config_path)

    assert config.theme == DEFAULT_THEME
