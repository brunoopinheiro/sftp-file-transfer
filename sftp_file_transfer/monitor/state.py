import json
from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class LogEntry:
    """A single timestamped log line for the Dashboard's LIVE LOG panel.

    Attributes:
        ts: Display timestamp for the entry (e.g. '14:30:01').
        level: Log level name (e.g. 'INFO', 'WARN', 'ERROR', 'DEBUG').
        msg: The log message text.
    """

    ts: str
    level: str
    msg: str

    def to_dict(self) -> dict:
        """Serialize this entry to a JSON-compatible dict.

        Returns:
            dict: The entry's fields as a plain dict.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'LogEntry':
        """Build a LogEntry from a dict produced by to_dict().

        Args:
            data: Dict with 'ts', 'level', and 'msg' keys.

        Returns:
            LogEntry: The reconstructed entry.
        """
        return cls(ts=data['ts'], level=data['level'], msg=data['msg'])


@dataclass
class DashboardState:
    """In-memory snapshot of the monitor daemon's dashboard state.

    Attributes:
        site_name: Name of the hotel site being monitored.
        cycle_num: Number of scheduled cycles run so far.
        last_cycle_time: Display timestamp of the most recent cycle.
        last_cycle_status: 'SUCCESS', 'PARTIAL', or 'FAILED'.
        countdown_sec: Seconds remaining until the next cycle.
        db_connected: Whether the NFCe source database is reachable, or
            None if this site isn't configured for DB-based generation.
        sftp_connected: Whether the SFTP target is reachable.
        uptime_sec: Seconds elapsed since the daemon process started.
        sent_count: Total files successfully sent.
        failed_count: Total files currently failed.
        pending_count: Total files currently pending.
        daily_counts: Sent-file counts for the trailing 7 days.
        log_lines: Bounded ring buffer of recent LogEntry items.
    """

    site_name: str = ''
    cycle_num: int = 0
    last_cycle_time: str = ''
    last_cycle_status: str = ''
    countdown_sec: int = 0
    db_connected: Optional[bool] = None
    sftp_connected: bool = False
    uptime_sec: int = 0
    sent_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
    daily_counts: List[int] = field(default_factory=list)
    log_lines: List[LogEntry] = field(default_factory=list)

    def append_log_line(self, entry: LogEntry, max_lines: int) -> None:
        """Append a log entry, trimming the oldest ones beyond max_lines.

        Args:
            entry: The LogEntry to append.
            max_lines: Maximum number of entries to retain.

        Returns:
            None.
        """
        self.log_lines.append(entry)
        if len(self.log_lines) > max_lines:
            self.log_lines = self.log_lines[-max_lines:]

    def to_dict(self) -> dict:
        """Serialize this state to a JSON-compatible dict.

        Returns:
            dict: The state's fields, with log_lines as a list of dicts.
        """
        data = asdict(self)
        data['log_lines'] = [entry.to_dict() for entry in self.log_lines]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'DashboardState':
        """Build a DashboardState from a dict produced by to_dict().

        Args:
            data: Dict with this dataclass's fields, log_lines as dicts.

        Returns:
            DashboardState: The reconstructed state.
        """
        data = dict(data)
        data['log_lines'] = [
            LogEntry.from_dict(entry) for entry in data.get('log_lines', [])
        ]
        return cls(**data)

    def to_json(self) -> str:
        """Serialize this state to a JSON string.

        Returns:
            str: The JSON-encoded state.
        """
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data: str) -> 'DashboardState':
        """Build a DashboardState from a JSON string produced by to_json().

        Args:
            data: JSON string as produced by to_json().

        Returns:
            DashboardState: The reconstructed state.
        """
        return cls.from_dict(json.loads(data))
