import os
from datetime import date
from typing import List, Optional

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from typer import Argument, Context, Option, Typer

from sftp_file_transfer.components.history_tracker import (
    HistoryTracker,
    SendHistory,
)

app = Typer()
console = Console()

_DB_HELP = (
    'Path to the send_history.db ledger (defaults to HISTORY_DB_PATH env var).'
)


def _resolve_db_path(db: Optional[str]) -> str:
    """Resolve the database path, falling back to env var or default.

    Args:
        db: Explicit database path, or None to use env var/default.

    Returns:
        str: The resolved database path.
    """
    return db or os.getenv('HISTORY_DB_PATH', 'data/send_history.db')


def _render_records_table(
    rows: List[SendHistory],
    title: str = 'Send History',
    show_error: bool = False,
) -> None:
    """Render a table of send-history records to the console.

    Args:
        rows: List of SendHistory ORM records from HistoryTracker.
        title: Title to display above the table.
        show_error: Whether to include error columns.

    Returns:
        None.
    """
    table = Table(title=title)
    table.add_column('Status')
    table.add_column('File')
    table.add_column('Date')
    table.add_column('Attempts', justify='right')
    if show_error:
        table.add_column('Last Error')
        table.add_column('Last Attempt')

    for row in rows:
        sent = bool(row.sent)
        status = '[green]sent[/]' if sent else '[red]failed[/]'
        style = 'dim' if sent else None
        cells = [
            status,
            row.file_name,
            row.file_date,
            str(row.attempts),
        ]
        if show_error:
            cells += [
                row.last_error or '-',
                row.last_attempt_at or '-',
            ]
        table.add_row(*cells, style=style)

    console.print(table)


def _do_reset(identifier: str, db_path: str, yes: bool) -> None:
    """Reset a record back to pending for retry.

    Resolves matching records via HistoryTracker.find_records(). If
    multiple matches exist and yes=False, displays them and prompts
    for confirmation. Resets all matches and prints the result table.

    Args:
        identifier: sha256 path_hash or substring of local path.
        db_path: Path to the send_history.db database.
        yes: If True, skip confirmation prompt for multiple matches.

    Returns:
        None.
    """
    with HistoryTracker(db_path) as tracker:
        matches = tracker.find_records(identifier)
        if not matches:
            console.print(
                f"[red]No record found matching '{identifier}'.[/]",
            )
            raise typer.Exit(1)

        if len(matches) > 1 and not yes:
            _render_records_table(
                matches,
                title='Multiple matches -- confirm to reset all',
            )
            if not Confirm.ask(
                f'Reset all {len(matches)} matched record(s)?',
            ):
                raise typer.Exit(0)

        updated = tracker.reset_record(identifier)
        console.print(
            f'[green]Reset {len(updated)} record(s) to pending.[/]',
        )
        _render_records_table(updated)


@app.command('list')
def list_(
    status: Optional[str] = Option(
        None,
        '--status',
        '-s',
        help="Filter by status: 'sent' or 'failed'. Omit for all.",
    ),
    since: Optional[str] = Option(
        None,
        '--since',
        help='ISO date, inclusive lower bound.',
    ),
    until: Optional[str] = Option(
        None,
        '--until',
        help='ISO date, inclusive upper bound.',
    ),
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """List tracked send-history records.

    Args:
        status: Filter by 'sent' or 'failed', or None for all records.
        since: ISO date string for inclusive lower bound, or None.
        until: ISO date string for inclusive upper bound, or None.
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    sent: Optional[bool] = None
    if status is not None:
        if status not in {'sent', 'failed'}:
            raise typer.BadParameter("status must be 'sent' or 'failed'")
        sent = status == 'sent'

    try:
        since_d = date.fromisoformat(since) if since else None
        until_d = date.fromisoformat(until) if until else None
    except ValueError:
        console.print('[red]Invalid date format, expected YYYY-MM-DD.[/]')
        raise typer.Exit(1)

    with HistoryTracker(_resolve_db_path(db)) as tracker:
        rows = tracker.list_records(sent=sent, since=since_d, until=until_d)
    _render_records_table(rows, show_error=True)


@app.command()
def failures(
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """List records that have not yet been sent successfully.

    Args:
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    with HistoryTracker(_resolve_db_path(db)) as tracker:
        rows = tracker.list_records(sent=False)
    _render_records_table(rows, title='Pending / Failed', show_error=True)


@app.command()
def report(
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """Show a summary report of the send-history ledger.

    Args:
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    with HistoryTracker(_resolve_db_path(db)) as tracker:
        summary = tracker.get_summary()

    table = Table(title='Send History Report', show_header=False)
    table.add_column('Metric')
    table.add_column('Value')
    date_range = (
        f'{summary["date_range_start"]} .. {summary["date_range_end"]}'
        if summary['date_range_start']
        else 'n/a'
    )
    table.add_row('Total tracked', str(summary['total']))
    table.add_row('Sent', str(summary['total_sent']))
    table.add_row('Pending / Failed', str(summary['total_pending']))
    table.add_row('Date range', date_range)
    table.add_row(
        'Last sent date',
        str(summary['last_sent_date'] or 'never'),
    )
    console.print(table)


@app.command()
def reset(
    identifier: str = Argument(
        ...,
        help='sha256 path_hash or a substring of the local path.',
    ),
    yes: bool = Option(
        False,
        '--yes',
        '-y',
        help='Skip the confirmation prompt.',
    ),
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """Force a record back to pending so it gets retried.

    Args:
        identifier: sha256 path_hash or substring of the local path.
        yes: If True, skip confirmation prompt for multiple matches.
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    _do_reset(identifier, _resolve_db_path(db), yes)


def _interactive_reset(db_path: str) -> None:
    """Interactively prompt and reset a record.

    Args:
        db_path: Path to the send_history.db database.

    Returns:
        None.
    """
    identifier = Prompt.ask('Enter a path substring or hash')
    _do_reset(identifier, db_path, yes=False)


def _interactive_menu(db: Optional[str]) -> None:
    """Display an interactive menu for history management.

    Loops until the user chooses to exit, offering options to list
    records, show failures, show report, or reset a record.

    Args:
        db: Path to the database, or None to use env var/default.

    Returns:
        None.
    """
    db_path = _resolve_db_path(db)
    options = {
        '1': (
            'List records',
            lambda: list_(status=None, since=None, until=None, db=db_path),
        ),
        '2': ('Show failures', lambda: failures(db=db_path)),
        '3': ('Show report', lambda: report(db=db_path)),
        '4': (
            'Reset / requeue a record',
            lambda: _interactive_reset(db_path),
        ),
        '0': ('Exit', None),
    }
    while True:
        console.print('\n[bold]SFTP Send History -- Menu[/]')
        for key, (label, _) in options.items():
            console.print(f'  {key}. {label}')
        choice = Prompt.ask(
            'Choose an option',
            choices=list(options),
            default='0',
        )
        if choice == '0':
            break
        try:
            options[choice][1]()
        except typer.Exit:
            pass


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: Context,
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """Typer app callback; launches the interactive menu if no
    subcommand is invoked.

    Args:
        ctx: Typer context object.
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    if ctx.invoked_subcommand:
        return
    _interactive_menu(db)


if __name__ == '__main__':
    app()
