import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Union

DEFAULT_CONFIG_PATH = Path('data') / 'monitor_config.json'
DEFAULT_THEME = 'nord'


@dataclass
class MonitorConfig:
    """Persisted user settings for the monitor TUI.

    Attributes:
        theme: Name of the registered Textual theme to use on startup.
    """

    theme: str = DEFAULT_THEME

    @classmethod
    def load(
        cls,
        config_path: Union[str, Path] = DEFAULT_CONFIG_PATH,
    ) -> 'MonitorConfig':
        """Load settings from disk, falling back to defaults.

        Falls back to a default MonitorConfig if the file doesn't
        exist, or if its contents can't be parsed as valid JSON.

        Args:
            config_path: Path to the JSON settings file.

        Returns:
            MonitorConfig: The loaded (or default) settings.
        """
        path = Path(config_path)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            return cls()
        return cls(theme=data.get('theme', DEFAULT_THEME))

    def save(
        self,
        config_path: Union[str, Path] = DEFAULT_CONFIG_PATH,
    ) -> None:
        """Persist these settings to disk as JSON.

        Creates the parent directory if it doesn't already exist.

        Args:
            config_path: Path to the JSON settings file.

        Returns:
            None.
        """
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self)), encoding='utf-8')
