import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    SelectionList,
    Static,
    TextArea,
)

from sftp_file_transfer import __version__
from sftp_file_transfer.components.file_manager import FileManager
from sftp_file_transfer.components.history_tracker import (
    HistoryTracker,
    SendHistory,
    resolve_history_db_path,
)
from sftp_file_transfer.monitor.client import DaemonClient
from sftp_file_transfer.monitor.config import DEFAULT_THEME, MonitorConfig
from sftp_file_transfer.monitor.state import DashboardState

MAX_VISIBLE_LOG_LINES = 40

HIGH_CONTRAST_THEME = Theme(
    name='high-contrast',
    primary='#FFFFFF',
    secondary='#FFFF00',
    warning='#FFFF00',
    error='#FF0000',
    success='#00FF00',
    accent='#00FFFF',
    foreground='#FFFFFF',
    background='#000000',
    surface='#000000',
    panel='#000000',
    dark=True,
)

LIGHT_THEME_NAME = 'solarized-light'
DARK_THEME_NAME = 'monokai'

# These two built-in themes use Textual's special 'ansi_*' pseudo-colors,
# which Rich's plain Style.parse() (used by our manually-styled Text
# rows) can't resolve — selecting either crashes rendering. Every other
# built-in theme's semantic colors are real hex/named colors and work
# fine, so only these two are excluded rather than restricting the
# whole theme list.
INCOMPATIBLE_BUILTIN_THEMES = ('ansi-dark', 'ansi-light')


class DashboardScreen(Screen):
    """The STATUS / USAGE STATS / LIVE LOG screen."""

    DEFAULT_CSS = """
    DashboardScreen {
        layout: horizontal;
    }

    #dashboard-left {
        width: 40;
        layout: vertical;
        margin: 1 0 1 1;
    }

    #status-content {
        border: round $primary;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #stats-panel {
        border: round $primary;
        height: 1fr;
        padding: 1;
    }

    #stats-columns {
        height: auto;
    }

    .stat-column {
        width: 1fr;
        content-align: center middle;
        text-align: center;
    }

    #log-panel {
        border: round $primary;
        width: 1fr;
        margin: 1 1 1 1;
        padding: 0 1;
    }

    #log-content {
        height: 1fr;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        Binding('r', 'force_run', 'Run now'),
        Binding('p', 'toggle_follow', 'Pause/Follow'),
    ]

    def compose(self) -> ComposeResult:  # noqa: PLR6301
        """Build this screen's widget tree.

        Returns:
            ComposeResult: The widgets that make up this screen.
        """
        yield Header()
        with Horizontal():
            with Vertical(id='dashboard-left'):
                yield Static(id='status-content')
                with Vertical(id='stats-panel'):
                    with Horizontal(id='stats-columns'):
                        yield Static(id='stats-sent', classes='stat-column')
                        yield Static(
                            id='stats-failed',
                            classes='stat-column',
                        )
                        yield Static(
                            id='stats-pending',
                            classes='stat-column',
                        )
            with Vertical(id='log-panel'):
                yield Static(id='log-content')
        yield Footer()

    def on_mount(self) -> None:
        """Populate the screen with the app's current dashboard state.

        Returns:
            None.
        """
        self.refresh_from_state()

    _STATUS_LABEL_WIDTH = 16
    _STATUS_ROW_WIDTH = 36

    def refresh_from_state(self) -> None:
        """Re-render this screen's panels from `self.app.dashboard_state`.

        Returns:
            None.
        """
        state: DashboardState = self.app.dashboard_state
        theme = self.app.get_theme(self.app.theme)

        status_widget = self.query_one('#status-content', Static)
        status_widget.border_title = 'STATUS'
        status_widget.update(
            Text('\n').join([
                self._status_row(
                    'SITE',
                    f'{state.site_name} · v{__version__}',
                    style='dim',
                ),
                self._status_row(
                    'LAST CYCLE',
                    f'{state.last_cycle_status} · {state.last_cycle_time}',
                    style=self._cycle_status_style(
                        state.last_cycle_status,
                        theme,
                    ),
                ),
                self._status_row(
                    'NEXT RUN',
                    f'in {state.countdown_sec}s',
                    style=f'bold {theme.accent}',
                ),
                self._status_row(
                    'DB CONNECTION',
                    self._connection_label(state.db_connected),
                    style=self._connection_style(state.db_connected, theme),
                ),
                self._status_row(
                    'SFTP CONNECTION',
                    self._connection_label(state.sftp_connected),
                    style=self._connection_style(
                        state.sftp_connected,
                        theme,
                    ),
                ),
                self._status_row(
                    'UPTIME',
                    self._format_uptime(state.uptime_sec),
                    style='dim bold',
                ),
                self._status_row(
                    'CYCLE #',
                    str(state.cycle_num),
                    style='bold',
                ),
            ]),
        )

        stats_panel = self.query_one('#stats-panel', Vertical)
        stats_panel.border_title = 'USAGE STATS'
        self.query_one('#stats-sent', Static).update(
            self._stat_column(state.sent_count, 'SENT', theme.success),
        )
        self.query_one('#stats-failed', Static).update(
            self._stat_column(state.failed_count, 'FAILED', theme.error),
        )
        self.query_one('#stats-pending', Static).update(
            self._stat_column(state.pending_count, 'PENDING', theme.warning),
        )

        log_panel = self.query_one('#log-panel', Vertical)
        log_panel.border_title = 'LIVE LOG'
        log_lines = [
            f'[{entry.ts}] {entry.level:<5} {entry.msg}'
            for entry in state.log_lines[-MAX_VISIBLE_LOG_LINES:]
        ]
        self.query_one('#log-content', Static).update('\n'.join(log_lines))

    @classmethod
    def _status_row(cls, label: str, value: str, style: str = '') -> Text:
        """Build one label/value STATUS row, label-left and value-right.

        Args:
            label: The row's label (e.g. 'LAST CYCLE').
            value: The row's value text.
            style: Rich style string applied to the whole row.

        Returns:
            Text: A single-line, fixed-width-aligned styled row.
        """
        value_width = cls._STATUS_ROW_WIDTH - cls._STATUS_LABEL_WIDTH
        line = (
            f'{label.ljust(cls._STATUS_LABEL_WIDTH)}{value.rjust(value_width)}'
        )
        return Text(line, style=style)

    @staticmethod
    def _stat_column(value: int, label: str, color: str) -> Text:
        """Build a big-number-over-label column for the USAGE STATS panel.

        Args:
            value: The count to display prominently.
            label: The short label shown beneath the count.
            color: Rich color to bold the count in.

        Returns:
            Text: A two-line, center-justified column.
        """
        text = Text(justify='center')
        text.append(str(value), style=f'bold {color}')
        text.append('\n')
        text.append(label, style='dim')
        return text

    @staticmethod
    def _cycle_status_style(status: str, theme: Theme) -> str:
        """Map a last-cycle status to a live theme color.

        Args:
            status: 'SUCCESS', 'PARTIAL', 'FAILED', or '' (no cycle yet).
            theme: The app's currently active Theme.

        Returns:
            str: A Rich style string.
        """
        colors = {
            'SUCCESS': theme.success,
            'PARTIAL': theme.warning,
            'FAILED': theme.error,
        }
        color = colors.get(status)
        return color if color else 'dim'

    @staticmethod
    def _connection_style(connected: Optional[bool], theme: Theme) -> str:
        """Map a tri-state connection flag to a live theme color.

        Args:
            connected: True/False for a known state, or None if not
                configured/applicable for this site.
            theme: The app's currently active Theme.

        Returns:
            str: A Rich style string.
        """
        if connected is None:
            return 'dim'
        return theme.success if connected else theme.error

    @staticmethod
    def _format_uptime(uptime_sec: int) -> str:
        """Format a second count as a compact 'Xd Yh Zm' uptime string.

        Args:
            uptime_sec: Seconds elapsed since the daemon started.

        Returns:
            str: e.g. '3d 14h 22m', or '0m' for a freshly-started daemon.
        """
        minutes, _ = divmod(uptime_sec, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        if days:
            return f'{days}d {hours}h {minutes}m'
        if hours:
            return f'{hours}h {minutes}m'
        return f'{minutes}m'

    @staticmethod
    def _connection_label(connected: Optional[bool]) -> str:
        """Render a tri-state connection flag as a display label.

        Args:
            connected: True/False for a known state, or None if this
                connection isn't configured/applicable for this site.

        Returns:
            str: '● CONNECTED', '○ DISCONNECTED', or 'N/A'.
        """
        if connected is None:
            return 'N/A'
        return '● CONNECTED' if connected else '○ DISCONNECTED'

    def action_force_run(self) -> None:
        """Ask the app to send a force-run command to the daemon.

        Returns:
            None.
        """
        self.app.send_command({'cmd': 'force_run'})

    def action_toggle_follow(self) -> None:
        """Toggle whether the log panel auto-follows new lines.

        Returns:
            None.
        """
        self.app.follow_log = not self.app.follow_log


class HistoryScreen(Screen):
    """The searchable/filterable send-history ledger screen."""

    AUTO_FOCUS = ''

    DEFAULT_CSS = """
    #history-toolbar {
        height: auto;
        margin: 1 1 0 1;
    }

    #search-input {
        width: 1fr;
        margin-right: 1;
    }

    #status-filters {
        width: auto;
        height: auto;
    }

    #status-filters Button {
        min-width: 9;
        height: 3;
        margin-left: 1;
    }

    #history-table {
        margin: 1;
    }
    """

    BINDINGS = [
        Binding('slash', 'focus_search', 'Search', key_display='/'),
        Binding('1', 'filter_all', 'All'),
        Binding('2', 'filter_sent', 'Sent'),
        Binding('3', 'filter_failed', 'Failed'),
        Binding('4', 'filter_pending', 'Pending'),
        Binding('escape', 'clear_filters', 'Clear'),
    ]

    _FILTER_BUTTON_IDS = {
        'all': 'filter-all',
        'sent': 'filter-sent',
        'failed': 'filter-failed',
        'pending': 'filter-pending',
    }

    status_filter: reactive[str] = reactive('all')
    search_query: reactive[str] = reactive('')
    _refresh_token: int = 0

    def compose(self) -> ComposeResult:  # noqa: PLR6301
        """Build this screen's widget tree.

        Returns:
            ComposeResult: The widgets that make up this screen.
        """
        yield Header()
        with Horizontal(id='history-toolbar'):
            yield Input(
                placeholder='search invoice / access key…',
                id='search-input',
            )
            with Horizontal(id='status-filters'):
                yield Button('ALL', id='filter-all')
                yield Button('SENT', id='filter-sent')
                yield Button('FAILED', id='filter-failed')
                yield Button('PENDING', id='filter-pending')
        yield DataTable(id='history-table')
        yield Footer()

    def on_mount(self) -> None:
        """Set up the table's columns and load the initial rows.

        Returns:
            None.
        """
        table = self.query_one('#history-table', DataTable)
        table.add_column('FILE', width=34)
        table.add_column('GENERATED', width=21)
        table.add_column('SENT', width=21)
        table.add_column('STATUS', width=14)
        table.add_column('RETRIES', width=12)
        self.refresh_table()

    def refresh_table(self) -> None:
        """Reload the table from the ledger + local folders, applying
        the active search/status filters.

        Reading the whole ledger and scanning LOCAL_PATH is blocking
        work, and this runs on every keystroke in the search box, so it
        is done on a worker thread. Each request carries a token and a
        stale load is discarded, so a slow scan can never overwrite the
        results of a newer keystroke.

        Returns:
            None.
        """
        self._sync_filter_buttons()
        self._refresh_token += 1
        self._load_rows(self._refresh_token)

    @work(thread=True, exclusive=True, group='history-refresh')
    def _load_rows(self, token: int) -> None:
        """Gather the table's rows off the UI thread.

        Args:
            token: The refresh generation this load belongs to.

        Returns:
            None.
        """
        display_rows = self._collect_display_rows()
        try:
            self.app.call_from_thread(
                self._populate_table,
                display_rows,
                token,
            )
        except RuntimeError:
            pass  # the app shut down while this load was in flight

    def _collect_display_rows(self) -> List[Dict[str, object]]:
        """Read the ledger and local folders into display rows.

        Returns:
            List[Dict[str, object]]: One display row per known or
                pending file, unfiltered.
        """
        with HistoryTracker(self.app.history_db_path) as tracker:
            known_rows: List[SendHistory] = tracker.list_records()

        known_hashes = {row.path_hash for row in known_rows}
        return [self._display_row_from_ledger(row) for row in known_rows] + [
            self._display_row_from_pending(path)
            for path in self._scan_pending_files(known_hashes)
        ]

    def _populate_table(
        self,
        display_rows: List[Dict[str, object]],
        token: int,
    ) -> None:
        """Render the collected rows, honouring the active filters.

        Args:
            display_rows: Rows gathered by `_collect_display_rows`.
            token: The refresh generation these rows belong to.

        Returns:
            None.
        """
        if token != self._refresh_token:
            return

        table = self.query_one('#history-table', DataTable)
        table.clear()
        theme = self.app.get_theme(self.app.theme)

        query = self.search_query.strip().lower()
        for row in display_rows:
            if self.status_filter != 'all' and (
                row['status'].lower() != self.status_filter
            ):
                continue
            if query and query not in row['file_name'].lower():
                continue
            table.add_row(
                row['file_name'],
                row['generated'],
                row['sent_at'],
                Text(
                    row['status'],
                    style=self._history_status_style(row['status'], theme),
                ),
                str(row['retries']),
            )

    @classmethod
    def _display_row_from_ledger(cls, row: SendHistory) -> Dict[str, object]:
        """Build a display row dict from a ledger (attempted) record.

        Args:
            row: A SendHistory record that has been attempted at least
                once.

        Returns:
            Dict[str, object]: file_name/generated/sent_at/status/
                retries, ready for table display.
        """
        return {
            'file_name': row.file_name,
            'generated': (
                cls._file_generated_at(Path(row.local_path)) or row.file_date
            ),
            'sent_at': row.sent_at or '-',
            'status': 'SENT' if row.sent else 'FAILED',
            'retries': max(0, row.attempts - 1),
        }

    @classmethod
    def _display_row_from_pending(cls, path: Path) -> Dict[str, object]:
        """Build a display row dict for a local file never attempted.

        Args:
            path: Path to a file found in LOCAL_PATH with no ledger
                record for it yet.

        Returns:
            Dict[str, object]: file_name/generated/sent_at/status/
                retries, ready for table display.
        """
        return {
            'file_name': path.name,
            'generated': (
                cls._file_generated_at(path)
                or HistoryTracker.file_date_of(path).isoformat()
            ),
            'sent_at': '-',
            'status': 'PENDING',
            'retries': 0,
        }

    @staticmethod
    def _file_generated_at(path: Path) -> Optional[str]:
        """Read a file's modification time as a full datetime string.

        The ledger only persists a date (for since/until range
        filtering elsewhere), not a time-of-day, so the full 'GENERATED'
        timestamp is read live from the file on disk. Falls back to
        None (letting the caller use the date-only value) if the file
        has since been moved or deleted.

        Args:
            path: Path to the local file.

        Returns:
            Optional[str]: ISO datetime string (second precision), or
                None if the file's mtime can't be read.
        """
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        return datetime.fromtimestamp(mtime).isoformat(timespec='seconds')

    @staticmethod
    def _scan_pending_files(known_hashes: Set[str]) -> List[Path]:
        """Find local files with no ledger record at all yet.

        Args:
            known_hashes: path_hash values already present in the
                ledger (attempted, whether sent or failed).

        Returns:
            List[Path]: Files under LOCAL_PATH not yet attempted. Empty
                if LOCAL_PATH isn't configured.
        """
        local_path = os.getenv('LOCAL_PATH')
        if not local_path:
            return []
        extension = os.getenv('FILE_EXTENSION')

        pending = []
        for directory in local_path.split(';'):
            if extension:
                files = FileManager.fetch_files_filtered_by_extension(
                    directory=directory,
                    extension=extension,
                )
            else:
                files = FileManager.fetch_files(directory=directory)
            pending.extend(
                f
                for f in files
                if HistoryTracker.hash_path(f) not in known_hashes
            )
        return pending

    @staticmethod
    def _history_status_style(status: str, theme: Theme) -> str:
        """Map a history row's status to a live theme color.

        Args:
            status: 'SENT', 'FAILED', or 'PENDING'.
            theme: The app's currently active Theme.

        Returns:
            str: A Rich style string.
        """
        colors = {
            'SENT': theme.success,
            'FAILED': theme.error,
            'PENDING': theme.warning,
        }
        color = colors.get(status)
        return color if color else ''

    def _sync_filter_buttons(self) -> None:
        """Highlight whichever filter button matches the active filter.

        Returns:
            None.
        """
        for name, button_id in self._FILTER_BUTTON_IDS.items():
            button = self.query_one(f'#{button_id}', Button)
            button.variant = (
                'primary' if name == self.status_filter else ('default')
            )

    @on(Button.Pressed, '#status-filters Button')
    def handle_filter_button(self, event: Button.Pressed) -> None:
        """React to a filter button being clicked.

        Args:
            event: The Button.Pressed event.

        Returns:
            None.
        """
        filter_by_id = {v: k for k, v in self._FILTER_BUTTON_IDS.items()}
        new_filter = filter_by_id.get(event.button.id or '')
        if new_filter is not None:
            self.status_filter = new_filter
            self.refresh_table()

    @on(Input.Changed, '#search-input')
    def handle_search_change(self, event: Input.Changed) -> None:
        """React to the search box's value changing.

        Args:
            event: The Input.Changed event carrying the new value.

        Returns:
            None.
        """
        self.search_query = event.value
        self.refresh_table()

    def action_focus_search(self) -> None:
        """Move focus to the search input.

        Returns:
            None.
        """
        self.query_one('#search-input', Input).focus()

    def action_filter_all(self) -> None:
        """Clear the status filter, showing every record.

        Returns:
            None.
        """
        self.status_filter = 'all'
        self.refresh_table()

    def action_filter_failed(self) -> None:
        """Filter the table to failed (unsent) records only.

        Returns:
            None.
        """
        self.status_filter = 'failed'
        self.refresh_table()

    def action_filter_sent(self) -> None:
        """Filter the table to sent records only.

        Returns:
            None.
        """
        self.status_filter = 'sent'
        self.refresh_table()

    def action_filter_pending(self) -> None:
        """Filter the table to never-attempted local files only.

        Returns:
            None.
        """
        self.status_filter = 'pending'
        self.refresh_table()

    def action_clear_filters(self) -> None:
        """Reset both the search box and the status filter.

        Returns:
            None.
        """
        self.query_one('#search-input', Input).value = ''
        self.search_query = ''
        self.status_filter = 'all'
        self.refresh_table()


class ResendScreen(Screen):
    """Paste a failure report, look it up, and resend.

    Each pasted line may be a bare identifier (e.g. an invoice/access-key
    token embedded in a filename) or a full filename. Lookup checks two
    sources: the ledger, via HistoryTracker.find_records()'s existing
    hash/substring matching (covers SENT/FAILED files); and LOCAL_PATH
    directly, via HistoryScreen._scan_pending_files() (covers files that
    were generated but never attempted at all — no ledger row yet).
    Resending a match — even one already marked SENT — resets it to
    pending and forces an immediate cycle, since the point of this
    screen is recovering files a downstream report says never arrived
    despite the local ledger believing otherwise.
    """

    DEFAULT_CSS = """
    ResendScreen {
        layout: vertical;
    }

    #resend-input-row {
        height: 12;
        margin: 1 1 0 1;
    }

    #resend-textarea {
        width: 1fr;
        border: round $primary;
    }

    #resend-actions {
        width: 24;
        height: auto;
        margin-left: 1;
    }

    #resend-actions Button {
        width: 100%;
        margin-bottom: 1;
    }

    #resend-results {
        margin: 1;
        border: round $primary;
        height: 1fr;
    }

    #resend-status {
        margin: 0 1 1 1;
        height: auto;
    }
    """

    BINDINGS = [
        Binding('ctrl+l', 'lookup', 'Lookup'),
        Binding('ctrl+r', 'resend_selected', 'Resend Selected'),
    ]

    _lookup_token: int = 0

    def compose(self) -> ComposeResult:  # noqa: PLR6301
        """Build this screen's widget tree.

        Returns:
            ComposeResult: The widgets that make up this screen.
        """
        yield Header()
        with Horizontal(id='resend-input-row'):
            yield TextArea(id='resend-textarea')
            with Vertical(id='resend-actions'):
                yield Button('Lookup', id='lookup-button')
                yield Button(
                    'Resend Selected',
                    id='resend-button',
                    variant='success',
                )
        yield SelectionList(id='resend-results')
        yield Static(id='resend-status')
        yield Footer()

    def _status_widget(self) -> Static:
        """Return the status line widget.

        Returns:
            Static: The status-line widget.
        """
        return self.query_one('#resend-status', Static)

    def action_lookup(self) -> None:
        """Resolve every pasted line against the ledger.

        The lookup reads the whole ledger, scans LOCAL_PATH and runs a
        substring query per pasted line, so it runs on a worker thread
        rather than blocking the UI.

        Returns:
            None.
        """
        textarea = self.query_one('#resend-textarea', TextArea)
        lines = list(
            dict.fromkeys(
                line.strip()
                for line in textarea.text.splitlines()
                if line.strip()
            ),
        )
        self._lookup_token += 1
        self._run_lookup(lines, self._lookup_token)

    @work(thread=True, exclusive=True, group='resend-lookup')
    def _run_lookup(self, lines: List[str], token: int) -> None:
        """Resolve the pasted lines off the UI thread.

        Args:
            lines: The deduplicated, stripped lines the user pasted.
            token: The lookup generation this run belongs to.

        Returns:
            None.
        """
        matches, not_found = self._collect_matches(lines)
        try:
            self.app.call_from_thread(
                self._show_lookup_results,
                lines,
                matches,
                not_found,
                token,
            )
        except RuntimeError:
            pass  # the app shut down while this lookup was in flight

    def _collect_matches(
        self,
        lines: List[str],
    ) -> Tuple[List[Dict[str, str]], List[str]]:
        """Resolve each line against the ledger and LOCAL_PATH.

        Args:
            lines: The deduplicated, stripped lines the user pasted.

        Returns:
            Tuple: The matched entries (file name, status, path hash and
                the line that matched) and the lines with no match.
        """
        matches: List[Dict[str, str]] = []
        not_found: List[str] = []

        with HistoryTracker(self.app.history_db_path) as tracker:
            known_hashes = {row.path_hash for row in tracker.list_records()}
            pending_files = HistoryScreen._scan_pending_files(known_hashes)

            for line in lines:
                ledger_rows: List[SendHistory] = tracker.find_records(line)
                pending_matches = [
                    path
                    for path in pending_files
                    if line.lower() in path.name.lower()
                ]
                if not ledger_rows and not pending_matches:
                    not_found.append(line)
                    continue

                for row in ledger_rows:
                    matches.append({
                        'file_name': row.file_name,
                        'status': 'SENT' if row.sent else 'FAILED',
                        'path_hash': row.path_hash,
                        'line': line,
                    })
                for path in pending_matches:
                    matches.append({
                        'file_name': path.name,
                        'status': 'PENDING',
                        'path_hash': HistoryTracker.hash_path(path),
                        'line': line,
                    })

        return matches, not_found

    def _show_lookup_results(
        self,
        lines: List[str],
        matches: List[Dict[str, str]],
        not_found: List[str],
        token: int,
    ) -> None:
        """Render the resolved matches and the summary line.

        Args:
            lines: The lines the user pasted.
            matches: Entries resolved by `_collect_matches`.
            not_found: Lines that matched nothing.
            token: The lookup generation these results belong to.

        Returns:
            None.
        """
        if token != self._lookup_token:
            return

        theme = self.app.get_theme(self.app.theme)
        results = self.query_one('#resend-results', SelectionList)
        results.clear_options()

        for match in matches:
            label = Text.assemble(
                (match['file_name'], ''),
                '  ',
                (
                    match['status'],
                    HistoryScreen._history_status_style(
                        match['status'],
                        theme,
                    ),
                ),
                f"  (matched '{match['line']}')",
            )
            results.add_options([(label, match['path_hash'], True)])

        matched_lines = len(lines) - len(not_found)
        summary = (
            f'{len(lines)} line(s) pasted, {matched_lines} matched, '
            f'{len(not_found)} not found.'
        )
        if not_found:
            summary += f' No match for: {", ".join(not_found)}'
        self._status_widget().update(summary)

    @on(Button.Pressed, '#lookup-button')
    def handle_lookup_button(self) -> None:
        """React to the Lookup button being clicked.

        Returns:
            None.
        """
        self.action_lookup()

    def action_resend_selected(self) -> None:
        """Reset every selected match to pending and force a cycle.

        Returns:
            None.
        """
        results = self.query_one('#resend-results', SelectionList)
        selected: List[str] = list(results.selected)
        if not selected:
            self._status_widget().update('Nothing selected to resend.')
            return

        self._run_resend(selected)

    @work(thread=True, group='resend-apply')
    def _run_resend(self, selected: List[str]) -> None:
        """Reset the selected records off the UI thread.

        Args:
            selected: Path hashes of the records to requeue.

        Returns:
            None.
        """
        with HistoryTracker(self.app.history_db_path) as tracker:
            for path_hash in selected:
                tracker.reset_record(path_hash)
        try:
            self.app.call_from_thread(self._finish_resend, len(selected))
        except RuntimeError:
            pass  # the app shut down while the reset was in flight

    def _finish_resend(self, count: int) -> None:
        """Trigger an immediate cycle and report what was requeued.

        Args:
            count: How many records were reset to pending.

        Returns:
            None.
        """
        self.app.send_command({'cmd': 'force_run'})
        self._status_widget().update(
            f'Requeued {count} file(s) and triggered an '
            f'immediate cycle — check Dashboard/History for results.',
        )

    @on(Button.Pressed, '#resend-button')
    def handle_resend_button(self) -> None:
        """React to the Resend Selected button being clicked.

        Returns:
            None.
        """
        self.action_resend_selected()


class HelpScreen(Screen):
    """Static keybindings reference screen."""

    HELP_TEXT = '\n'.join(
        [
            'NAVIGATION',
            '  F1        Dashboard',
            '  F2        History / ledger',
            '  F3        This help screen',
            '  F4        Resend (paste a failure report and requeue)',
            '',
            'ACTIONS',
            '  R         Force a cycle run now',
            '  P         Pause / resume live log follow',
            '  Q         Detach (scheduler keeps running in the background)',
            '  Ctrl+Q    Stop the scheduler and quit',
            '',
            'HISTORY SCREEN',
            '  /         Focus search field',
            '  1-4       Filter by status (all/sent/failed/pending),',
            '            or click the buttons above the table',
            '  Esc       Clear filters',
            '',
            'RESEND SCREEN',
            '  Paste one identifier or filename per line, then:',
            '  Ctrl+L    Look up every pasted line (ledger + local files)',
            '  Ctrl+R    Resend the checked matches (forces a cycle)',
            '  A match already marked SENT is still reset and resent —',
            '  use this when a downstream report says it never arrived.',
            '  A file with no ledger record yet (never attempted) still',
            '  shows up as PENDING if it exists under LOCAL_PATH.',
        ],
    )

    def compose(self) -> ComposeResult:
        """Build this screen's widget tree.

        Returns:
            ComposeResult: The widgets that make up this screen.
        """
        yield Header()
        yield Static(self.HELP_TEXT)
        yield Footer()


class MonitorApp(App):
    """The NFC-e SFTP Delivery Monitor TUI.

    A Textual client for a MonitorDaemon: renders live scheduler state
    on the Dashboard, the send-history ledger on History, and static
    keybindings on Help. Pressing 'q' detaches without stopping the
    daemon; 'ctrl+q' stops the daemon and exits.
    """

    BINDINGS = [
        Binding('f1', 'show_dashboard', 'Dashboard'),
        Binding('f2', 'show_history', 'History'),
        Binding('f3', 'show_help', 'Help'),
        Binding('f4', 'show_resend', 'Resend'),
        Binding('q', 'detach', 'Detach'),
        Binding('ctrl+q', 'stop_and_quit', 'Stop & Quit'),
    ]

    dashboard_state: reactive[DashboardState] = reactive(
        DashboardState(),
        init=False,
    )

    def __init__(
        self,
        history_db_path: Optional[Union[str, Path]] = None,
        client: Optional[DaemonClient] = None,
        initial_state: Optional[DashboardState] = None,
        theme_name: Optional[str] = None,
    ) -> None:
        """Initialize the app.

        Args:
            history_db_path: Path to the send-history ledger database,
                used directly by the History screen, or None to resolve
                HISTORY_DB_PATH from the .env file (see
                resolve_history_db_path()) — the same value every other
                entry point (scheduled.py, MonitorDaemon) uses.
            client: Connected DaemonClient to stream live state from,
                or None to run without a live daemon connection.
            initial_state: DashboardState to render before any update
                arrives from the client. Defaults to an empty state.
            theme_name: Theme to activate on startup. Defaults to the
                value persisted in MonitorConfig.

        Returns:
            None.
        """
        super().__init__()
        self.history_db_path = (
            Path(history_db_path)
            if history_db_path is not None
            else resolve_history_db_path()
        )
        self.client = client
        self.dashboard_state = initial_state or DashboardState()
        self.follow_log = True
        self.detached = False
        self.register_theme(HIGH_CONTRAST_THEME)
        for incompatible in INCOMPATIBLE_BUILTIN_THEMES:
            self.unregister_theme(incompatible)
        self.theme = (
            theme_name or MonitorConfig.load().theme or (DEFAULT_THEME)
        )

    def on_mount(self) -> None:
        """Install the four screens and start listening to the daemon.

        Returns:
            None.
        """
        self.install_screen(DashboardScreen(), name='dashboard')
        self.install_screen(HistoryScreen(), name='history')
        self.install_screen(ResendScreen(), name='resend')
        self.install_screen(HelpScreen(), name='help')
        self.push_screen('dashboard')
        if self.client is not None:
            self.run_worker(self._listen_to_daemon(), exclusive=True)

    async def _listen_to_daemon(self) -> None:
        """Apply each state update streamed from the daemon.

        Returns:
            None.
        """
        await self.client.connect()
        async for state in self.client.state_stream():
            self.dashboard_state = state

    def watch_dashboard_state(self, state: DashboardState) -> None:
        """React to `dashboard_state` changing by refreshing the Dashboard.

        Args:
            state: The new DashboardState.

        Returns:
            None.
        """
        try:
            dashboard = self.get_screen('dashboard')
        except KeyError:
            return
        if isinstance(dashboard, DashboardScreen) and dashboard.is_mounted:
            dashboard.refresh_from_state()

    def send_command(self, command: dict) -> None:
        """Send a command to the daemon, if a client is connected.

        Args:
            command: Command dict, e.g. {'cmd': 'force_run'}.

        Returns:
            None.
        """
        if self.client is not None:
            self.run_worker(self.client.send_command(command))

    def action_show_dashboard(self) -> None:
        """Switch to the Dashboard screen.

        Returns:
            None.
        """
        self.switch_screen('dashboard')

    def action_show_history(self) -> None:
        """Switch to the History screen.

        Returns:
            None.
        """
        self.switch_screen('history')

    def action_show_resend(self) -> None:
        """Switch to the Resend screen.

        Returns:
            None.
        """
        self.switch_screen('resend')

    def action_show_help(self) -> None:
        """Switch to the Help screen.

        Returns:
            None.
        """
        self.switch_screen('help')

    def action_detach(self) -> None:
        """Detach from the daemon: exit the TUI without stopping it.

        Returns:
            None.
        """
        self.detached = True
        self.exit()

    async def action_stop_and_quit(self) -> None:
        """Stop the daemon, then exit the TUI.

        Unlike `detach`, this sends the daemon a `stop` command and
        awaits its delivery before exiting, so the scheduler does not
        keep running in the background after the TUI closes.

        Returns:
            None.
        """
        if self.client is not None:
            try:
                await self.client.send_command({'cmd': 'stop'})
            except OSError:
                pass
        self.exit()
